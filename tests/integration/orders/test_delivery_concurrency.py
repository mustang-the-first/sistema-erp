"""Concurrent deliveries of the same order: the order row lock prevents over-delivery."""

import threading
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import BusinessRuleViolation
from app.models.catalog import Product
from app.models.enums import OrderStatus
from app.models.sales import DeliveryNoteLine, Order
from app.models.stock import StockMovement
from app.services.delivery_notes import DeliveryLineInput, create_delivery_note
from tests.factories import make_order, make_product, make_user

pytestmark = pytest.mark.concurrency

D = Decimal
WORKERS = 4
JOIN_TIMEOUT_S = 30


def test_concurrent_deliveries_same_order_never_over_deliver(
    committed_factory: sessionmaker[Session],
) -> None:
    with committed_factory() as session, session.begin():
        user = make_user(session)
        product = make_product(session, stock=D("100"), created_by=user)
        order = make_order(session, lines=[(product, D("5"))], created_by=user)
        ids = (user.id, product.id, order.id, order.lines[0].id)
    user_id, product_id, order_id, line_id = ids

    barrier = threading.Barrier(WORKERS, timeout=10)
    outcomes: list[str] = []
    lock = threading.Lock()

    def work() -> None:
        session = committed_factory()
        try:
            barrier.wait()
            try:
                create_delivery_note(
                    session,
                    order_id,
                    lines=[DeliveryLineInput(line_id, D("5"))],
                    created_by=user_id,
                )
                session.commit()
                outcome = "ok"
            except BusinessRuleViolation as exc:
                session.rollback()
                outcome = exc.code
        except BaseException as exc:  # pragma: no cover - reported via the assertion below
            outcome = repr(exc)
        finally:
            session.close()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=work) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_S)
    assert not any(t.is_alive() for t in threads)

    assert sorted(outcomes) == ["invalid_status"] * (WORKERS - 1) + ["ok"]
    with committed_factory() as session:
        delivered = session.scalar(
            select(func.sum(DeliveryNoteLine.quantity)).where(
                DeliveryNoteLine.order_line_id == line_id
            )
        )
        stock = session.get_one(Product, product_id).stock_qty
        ledger = session.scalar(
            select(func.sum(StockMovement.quantity)).where(StockMovement.product_id == product_id)
        )
        status = session.get_one(Order, order_id).status
    assert delivered == D("5")
    assert stock == D("95") == ledger
    assert status is OrderStatus.DELIVERED
