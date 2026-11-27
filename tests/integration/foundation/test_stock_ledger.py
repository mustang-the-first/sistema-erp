import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, NotFound
from app.models.catalog import Product
from app.models.enums import AdjustmentReason, StockMovementKind, UserRole
from app.models.stock import StockMovement
from app.models.users import User
from app.services.stock_ledger import MovementDraft, apply_movements, movement_value
from tests.factories import make_product, make_user

D = Decimal
TODAY = dt.date(2026, 11, 27)
ADJ = StockMovementKind.ADJUSTMENT


@pytest.fixture
def user(db: Session) -> User:
    return make_user(db, role=UserRole.ADMIN)


def out(product: Product, qty: str) -> MovementDraft:
    return MovementDraft(product.id, -D(qty), ADJ, reason=AdjustmentReason.DAMAGE)


def apply(db: Session, user: User, *drafts: MovementDraft) -> list[StockMovement]:
    return apply_movements(db, drafts, movement_date=TODAY, created_by=user.id)


def ledger_sum(db: Session, product: Product) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
            StockMovement.product_id == product.id
        )
    )
    return D(total)


def test_initial_load_sets_stock_and_snapshots_cost(db: Session, user: User) -> None:
    product = make_product(db, unit_cost=D("60.00"), stock=D("10"), created_by=user)

    movement = db.scalars(select(StockMovement).where(StockMovement.product_id == product.id)).one()
    assert product.stock_qty == D("10") == ledger_sum(db, product)
    assert movement.unit_cost == D("60.00")
    assert movement.reason is AdjustmentReason.INITIAL_LOAD


def test_outgoing_movement_reduces_stock(db: Session, user: User) -> None:
    product = make_product(db, stock=D("10"), created_by=user)

    apply(db, user, out(product, "3"))

    assert product.stock_qty == D("7") == ledger_sum(db, product)


def test_stock_cannot_go_negative(db: Session, user: User) -> None:
    product = make_product(db, stock=D("2"), created_by=user)

    with pytest.raises(BusinessRuleViolation) as exc:
        apply(db, user, out(product, "3"))

    assert exc.value.code == "insufficient_stock"
    assert product.sku in exc.value.message
    assert product.stock_qty == D("2") == ledger_sum(db, product)


def test_multi_product_movement_is_all_or_nothing(db: Session, user: User) -> None:
    enough = make_product(db, stock=D("10"), created_by=user)
    short = make_product(db, stock=D("1"), created_by=user)

    with pytest.raises(BusinessRuleViolation):
        apply(db, user, out(enough, "5"), out(short, "2"))

    assert enough.stock_qty == D("10") == ledger_sum(db, enough)
    assert short.stock_qty == D("1") == ledger_sum(db, short)


def test_repeated_product_in_one_call_is_checked_cumulatively(db: Session, user: User) -> None:
    product = make_product(db, stock=D("5"), created_by=user)

    with pytest.raises(BusinessRuleViolation):
        apply(db, user, out(product, "3"), out(product, "3"))

    apply(db, user, out(product, "2"), out(product, "3"))
    assert product.stock_qty == D("0") == ledger_sum(db, product)


def test_cost_change_does_not_revalue_existing_movements(db: Session, user: User) -> None:
    product = make_product(db, unit_cost=D("60.00"), stock=D("10"), created_by=user)
    product.unit_cost = D("80.00")
    db.flush()

    [movement] = apply(db, user, out(product, "1"))

    costs = set(
        db.scalars(select(StockMovement.unit_cost).where(StockMovement.product_id == product.id))
    )
    assert costs == {D("60.00"), D("80.00")}
    assert movement_value(movement) == D("80.00")


def test_movement_value_rounds_to_cents(db: Session, user: User) -> None:
    product = make_product(db, unit_cost=D("3.33"), stock=D("10"), created_by=user)

    [movement] = apply(db, user, out(product, "0.5"))

    assert movement_value(movement) == D("1.67")  # 1.665 -> 1.67


@pytest.mark.parametrize(
    "draft_kwargs",
    [
        {"quantity": D("-1"), "kind": StockMovementKind.DELIVERY},  # no delivery line
        {"quantity": D("1"), "kind": ADJ},  # no reason
        {"quantity": D("0"), "kind": ADJ, "reason": AdjustmentReason.COUNT_DIFFERENCE},
        {"quantity": D("1.0001"), "kind": ADJ, "reason": AdjustmentReason.COUNT_DIFFERENCE},
        {"quantity": D("-1"), "kind": ADJ, "reason": AdjustmentReason.INITIAL_LOAD},
        {"quantity": D("1"), "kind": ADJ, "reason": AdjustmentReason.DAMAGE},
        {
            "quantity": D("-1"),
            "kind": StockMovementKind.DELIVERY,
            "reason": AdjustmentReason.DAMAGE,
            "delivery_note_line_id": 1,
        },
    ],
)
def test_invalid_movements_are_rejected(
    db: Session, user: User, draft_kwargs: dict[str, Any]
) -> None:
    product = make_product(db, stock=D("10"), created_by=user)

    with pytest.raises(BusinessRuleViolation):
        apply(db, user, MovementDraft(product_id=product.id, **draft_kwargs))


def test_empty_movement_list_is_rejected(db: Session, user: User) -> None:
    with pytest.raises(BusinessRuleViolation):
        apply(db, user)


def test_unknown_product_is_not_found(db: Session, user: User) -> None:
    with pytest.raises(NotFound):
        apply(
            db, user, MovementDraft(999_999, D("1"), ADJ, reason=AdjustmentReason.COUNT_DIFFERENCE)
        )


@pytest.fixture
def locked_product_ids(db: Session) -> Iterator[list[int]]:
    """Capture the product id of every SELECT ... FOR UPDATE on products, in order."""
    seen: list[int] = []

    def capture(conn: Any, cursor: Any, statement: str, params: Any, *args: Any) -> None:
        if "FROM products" in statement and "FOR UPDATE" in statement:
            seen.append(next(v for v in params.values() if isinstance(v, int)))

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", capture)
    yield seen
    event.remove(connection, "before_cursor_execute", capture)


def test_products_are_locked_in_ascending_id_order(
    db: Session, user: User, locked_product_ids: list[int]
) -> None:
    products = [make_product(db, stock=D("10"), created_by=user) for _ in range(3)]
    locked_product_ids.clear()  # ignore the locks taken by the initial loads
    first, second, third = products

    apply(db, user, out(third, "1"), out(first, "1"), out(second, "1"), out(third, "1"))

    assert locked_product_ids == sorted(p.id for p in products)
