"""Concurrent receipts on the same invoices: separate connections that really commit."""

import threading
import time
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import BusinessRuleViolation
from app.models.accounting import JournalEntry
from app.models.collections import Receipt, ReceiptAllocation
from app.models.enums import JournalSourceType, PaymentMethod, UserRole
from app.services.collections import AllocationRequest, create_receipt
from app.services.invoice_balance import invoice_balance
from tests.factories import make_customer, make_user
from tests.integration.collections.helpers import invoice_of

pytestmark = pytest.mark.concurrency

D = Decimal
WORKERS = 10
BARRIER_TIMEOUT_S = 10
JOIN_TIMEOUT_S = 30


def run_concurrently[T](
    factory: sessionmaker[Session], work: Callable[[int, Session], T], n: int = WORKERS
) -> list[T]:
    """Each thread opens its OWN Session and closes it in ``finally``; joined with a timeout."""
    barrier = threading.Barrier(n, timeout=BARRIER_TIMEOUT_S)
    results: list[T | None] = [None] * n
    errors: list[BaseException] = []

    def target(i: int) -> None:
        session = factory()
        try:
            barrier.wait()
            results[i] = work(i, session)
        except BaseException as exc:
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=target, args=(i,), name=f"receipt-{i}") for i in range(n)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + JOIN_TIMEOUT_S
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    alive = [t.name for t in threads if t.is_alive()]
    assert not alive, f"threads still running after {JOIN_TIMEOUT_S}s: {alive}"
    if errors:
        raise errors[0]
    return results  # type: ignore[return-value]


def allocated_total(factory: sessionmaker[Session], invoice_id: int) -> Decimal:
    with factory() as session:
        value = session.scalar(
            select(func.coalesce(func.sum(ReceiptAllocation.amount), 0)).where(
                ReceiptAllocation.invoice_id == invoice_id
            )
        )
        return D(value)


def test_concurrent_receipts_never_overpay_an_invoice(
    committed_factory: sessionmaker[Session],
) -> None:
    with committed_factory.begin() as session:
        user_id = make_user(session, role=UserRole.SELLER).id
        customer = make_customer(session)
        customer_id = customer.id
        invoice_id = invoice_of(session, customer).id  # total 121.00

    def worker(_: int, session: Session) -> str:
        try:
            create_receipt(
                session,
                customer_id=customer_id,
                method=PaymentMethod.CASH,
                allocations=[AllocationRequest(invoice_id, D("50.00"))],
                created_by=user_id,
            )
            time.sleep(0.01)  # hold the invoice lock so unlocked reads would interleave
            session.commit()
            return "ok"
        except BusinessRuleViolation as exc:
            session.rollback()
            return exc.code

    results = run_concurrently(committed_factory, worker)

    assert results.count("ok") == 2  # 50 + 50 <= 121 < 150
    assert results.count("overpayment") == WORKERS - 2
    assert allocated_total(committed_factory, invoice_id) == D("100.00")
    with committed_factory() as session:
        assert invoice_balance(session, invoice_id) == D("21.00")
        receipts = session.scalars(select(Receipt).order_by(Receipt.number)).all()
        assert [r.number for r in receipts] == [1, 2]  # gap-free numbering
        entries = session.scalar(
            select(func.count())
            .select_from(JournalEntry)
            .where(JournalEntry.source_type == JournalSourceType.RECEIPT)
        )
        assert entries == 2


def test_receipts_on_same_invoices_in_opposite_order_do_not_deadlock(
    committed_factory: sessionmaker[Session],
) -> None:
    """Half the workers list [A, B], the other half [B, A]; invoices are locked by id."""
    with committed_factory.begin() as session:
        user_id = make_user(session, role=UserRole.SELLER).id
        customer = make_customer(session)
        customer_id = customer.id
        a = invoice_of(session, customer, "1000.00").id  # 1210.00
        b = invoice_of(session, customer, "1000.00").id

    def worker(i: int, session: Session) -> str:
        order = [a, b] if i % 2 == 0 else [b, a]
        create_receipt(
            session,
            customer_id=customer_id,
            method=PaymentMethod.BANK_TRANSFER,
            allocations=[AllocationRequest(inv, D("10.00")) for inv in order],
            created_by=user_id,
        )
        time.sleep(0.01)
        session.commit()
        return "ok"

    # A deadlock raises OperationalError (re-raised here); a lock wait fails via lock_timeout.
    results = run_concurrently(committed_factory, worker)

    assert results == ["ok"] * WORKERS
    assert allocated_total(committed_factory, a) == D("100.00")
    assert allocated_total(committed_factory, b) == D("100.00")
