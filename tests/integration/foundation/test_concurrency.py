"""Concurrency: separate connections that really commit (TRUNCATE cleanup in the fixture)."""

import threading
import time
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import today
from app.core.errors import BusinessRuleViolation
from app.models.catalog import Product
from app.models.enums import (
    AdjustmentReason,
    DocumentType,
    InvoiceLetter,
    StockMovementKind,
    UserRole,
)
from app.models.stock import StockMovement
from app.services.sequences import next_number
from app.services.stock_ledger import MovementDraft, apply_movements
from tests.factories import make_product, make_user

pytestmark = pytest.mark.concurrency

D = Decimal
WORKERS = 20


BARRIER_TIMEOUT_S = 10
JOIN_TIMEOUT_S = 30


def run_concurrently[T](
    factory: sessionmaker[Session], work: Callable[[int, Session], T], n: int = WORKERS
) -> list[T]:
    """Run ``work(i, session)`` in ``n`` threads that start together.

    Each thread opens its OWN Session and always closes it (finally). Threads are joined with
    a timeout: a stuck thread fails the test instead of hanging it. Worker exceptions are
    re-raised in the test thread.
    """
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

    threads = [threading.Thread(target=target, args=(i,), name=f"worker-{i}") for i in range(n)]
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


def damage(product_id: int, qty: str = "1") -> MovementDraft:
    return MovementDraft(
        product_id, -D(qty), StockMovementKind.ADJUSTMENT, reason=AdjustmentReason.DAMAGE
    )


def stock_state(factory: sessionmaker[Session], product_id: int) -> tuple[Decimal, Decimal]:
    with factory() as session:
        stock = session.get_one(Product, product_id).stock_qty
        ledger = session.scalar(
            select(func.sum(StockMovement.quantity)).where(StockMovement.product_id == product_id)
        )
        return stock, D(ledger)


def test_concurrent_outgoing_movements_never_overdraw_stock(
    committed_factory: sessionmaker[Session],
) -> None:
    with committed_factory.begin() as session:
        user_id = make_user(session, role=UserRole.ADMIN).id
        product_id = make_product(session, stock=D("10")).id

    def worker(_: int, session: Session) -> str:
        try:
            apply_movements(
                session, [damage(product_id)], movement_date=today(), created_by=user_id
            )
            session.commit()
            return "ok"
        except BusinessRuleViolation:
            session.rollback()
            return "insufficient"

    results = run_concurrently(committed_factory, worker)

    assert results.count("ok") == 10
    assert results.count("insufficient") == WORKERS - 10
    assert stock_state(committed_factory, product_id) == (D("0"), D("0"))


def test_opposite_product_order_does_not_deadlock(committed_factory: sessionmaker[Session]) -> None:
    """Half the workers list [A, B], the other half [B, A]; locks are still taken A then B."""
    with committed_factory.begin() as session:
        user_id = make_user(session, role=UserRole.ADMIN).id
        a = make_product(session, stock=D("100")).id
        b = make_product(session, stock=D("100")).id

    def worker(i: int, session: Session) -> str:
        order = [a, b] if i % 2 == 0 else [b, a]  # deliberately unordered: the service sorts
        apply_movements(
            session, [damage(pid) for pid in order], movement_date=today(), created_by=user_id
        )
        time.sleep(0.01)  # hold both locks so unordered locking would interleave
        session.commit()
        return "ok"

    # A deadlock raises OperationalError (re-raised here); a lock wait fails via lock_timeout.
    results = run_concurrently(committed_factory, worker)

    assert results == ["ok"] * WORKERS
    assert stock_state(committed_factory, a) == (D("80"), D("80"))
    assert stock_state(committed_factory, b) == (D("80"), D("80"))


def test_concurrent_numbering_is_unique_and_gap_free(
    committed_factory: sessionmaker[Session],
) -> None:
    """Every 4th worker rolls back after taking a number: that number must be reused."""

    def worker(i: int, session: Session) -> int | None:
        number = next_number(session, DocumentType.INVOICE, point_of_sale=1, letter=InvoiceLetter.A)
        if i % 4 == 0:
            session.rollback()
            return None
        session.commit()
        return number

    committed = sorted(n for n in run_concurrently(committed_factory, worker) if n is not None)

    assert committed == list(range(1, len(committed) + 1))
    assert len(committed) == WORKERS - WORKERS // 4
    with committed_factory() as session:
        assert (
            next_number(session, DocumentType.INVOICE, point_of_sale=1, letter=InvoiceLetter.A)
            == len(committed) + 1
        )
