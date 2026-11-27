import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.errors import BusinessRuleViolation, NotFound
from app.models.accounting import JournalEntry, SystemAccount
from app.models.catalog import Product
from app.models.enums import (
    AdjustmentReason,
    JournalSourceType,
    StockMovementKind,
    SystemAccountKey,
    UserRole,
)
from app.models.stock import StockMovement
from app.models.users import User
from app.repositories.stock import MovementFilters
from app.schemas.stock import AdjustmentCreate
from app.services import stock
from app.services.stock_ledger import MovementDraft, apply_movements
from tests.factories import make_delivery_note, make_order, make_product, make_user

D = Decimal
R = AdjustmentReason
INVENTORY, CAPITAL, GAIN, LOSS = "1.1.4.01", "3.1.01", "4.2.01", "5.2.01"
DAY = dt.date(2026, 11, 20)


@pytest.fixture
def admin(db: Session) -> User:
    return make_user(db, role=UserRole.ADMIN)


def adjust(
    db: Session,
    user: User,
    product: Product,
    quantity: str,
    reason: AdjustmentReason,
    *,
    on: dt.date | None = DAY,
    note: str | None = None,
) -> stock.Adjustment:
    body = AdjustmentCreate(
        product_id=product.id, quantity=D(quantity), reason=reason, date=on, note=note
    )
    return stock.create_adjustment(db, body, created_by=user.id)


def entry_lines(db: Session, number: int | None) -> set[tuple[str, Decimal, Decimal]]:
    assert number is not None
    entry = db.scalars(select(JournalEntry).where(JournalEntry.number == number)).one()
    return {(line.account.code, line.debit, line.credit) for line in entry.lines}


def ledger_sum(db: Session, product: Product) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
            StockMovement.product_id == product.id
        )
    )
    return D(total)


def test_initial_load_debits_inventory_and_credits_capital(db: Session, admin: User) -> None:
    product = make_product(db, unit_cost=D("60.00"))

    result = adjust(db, admin, product, "10", R.INITIAL_LOAD, note="Inventario inicial")

    assert entry_lines(db, result.journal_entry_number) == {
        (INVENTORY, D("600.00"), D("0")),
        (CAPITAL, D("0"), D("600.00")),
    }
    movement = result.movement
    assert (movement.kind, movement.reason, movement.quantity) == (
        StockMovementKind.ADJUSTMENT,
        R.INITIAL_LOAD,
        D("10"),
    )
    assert movement.unit_cost == D("60.00")
    assert (movement.date, movement.note, movement.created_by) == (
        DAY,
        "Inventario inicial",
        admin.id,
    )
    entry = db.scalars(
        select(JournalEntry).where(JournalEntry.number == result.journal_entry_number)
    ).one()
    assert (entry.source_type, entry.source_id, entry.date) == (
        JournalSourceType.STOCK_ADJUSTMENT,
        movement.id,
        DAY,
    )
    assert entry.description == f"Ajuste de stock (carga inicial) {product.sku} +10"
    db.refresh(product)
    assert product.stock_qty == D("10") == ledger_sum(db, product)


def test_count_difference_positive_credits_inventory_gain(db: Session, admin: User) -> None:
    product = make_product(db, unit_cost=D("12.35"), stock=D("5"))

    result = adjust(db, admin, product, "2.5", R.COUNT_DIFFERENCE)

    # 2.5 x 12.35 = 30.875 -> 30.88 (half up)
    assert entry_lines(db, result.journal_entry_number) == {
        (INVENTORY, D("30.88"), D("0")),
        (GAIN, D("0"), D("30.88")),
    }
    db.refresh(product)
    assert product.stock_qty == D("7.5") == ledger_sum(db, product)


def test_count_difference_negative_debits_inventory_loss(db: Session, admin: User) -> None:
    product = make_product(db, unit_cost=D("60.00"), stock=D("10"))

    result = adjust(db, admin, product, "-3", R.COUNT_DIFFERENCE)

    assert entry_lines(db, result.journal_entry_number) == {
        (LOSS, D("180.00"), D("0")),
        (INVENTORY, D("0"), D("180.00")),
    }
    db.refresh(product)
    assert product.stock_qty == D("7") == ledger_sum(db, product)


def test_damage_debits_inventory_loss(db: Session, admin: User) -> None:
    product = make_product(db, unit_cost=D("45.50"), stock=D("4"))

    result = adjust(db, admin, product, "-4", R.DAMAGE)

    assert entry_lines(db, result.journal_entry_number) == {
        (LOSS, D("182.00"), D("0")),
        (INVENTORY, D("0"), D("182.00")),
    }
    db.refresh(product)
    assert product.stock_qty == D("0") == ledger_sum(db, product)


def test_adjustment_uses_cost_snapshot_not_later_cost(db: Session, admin: User) -> None:
    product = make_product(db, unit_cost=D("10.00"))
    first = adjust(db, admin, product, "5", R.INITIAL_LOAD)
    product.unit_cost = D("20.00")
    db.flush()

    second = adjust(db, admin, product, "-1", R.DAMAGE)

    assert first.movement.unit_cost == D("10.00")
    assert second.movement.unit_cost == D("20.00")
    assert entry_lines(db, second.journal_entry_number) == {
        (LOSS, D("20.00"), D("0")),
        (INVENTORY, D("0"), D("20.00")),
    }


def test_zero_cost_product_moves_stock_without_entry(db: Session, admin: User) -> None:
    product = make_product(db, unit_cost=D("0"))
    entries_before = db.scalar(select(func.count()).select_from(JournalEntry))

    result = adjust(db, admin, product, "3", R.INITIAL_LOAD)

    assert result.journal_entry_number is None
    assert db.scalar(select(func.count()).select_from(JournalEntry)) == entries_before
    db.refresh(product)
    assert product.stock_qty == D("3")


def test_adjustment_date_defaults_to_today(db: Session, admin: User) -> None:
    product = make_product(db)

    result = adjust(db, admin, product, "1", R.INITIAL_LOAD, on=None)

    assert result.movement.date == today()


def test_adjustment_beyond_stock_raises_insufficient_stock(db: Session, admin: User) -> None:
    product = make_product(db, stock=D("2"))

    with pytest.raises(BusinessRuleViolation) as exc:
        adjust(db, admin, product, "-2.001", R.COUNT_DIFFERENCE)

    assert exc.value.code == "insufficient_stock"
    db.refresh(product)
    assert product.stock_qty == D("2") == ledger_sum(db, product)


@pytest.mark.parametrize(
    ("quantity", "reason", "code"),
    [
        ("0", R.COUNT_DIFFERENCE, "bad_quantity"),
        ("1.0001", R.COUNT_DIFFERENCE, "bad_quantity"),
        ("-1", R.INITIAL_LOAD, "bad_movement"),
        ("1", R.DAMAGE, "bad_movement"),
    ],
)
def test_adjustment_invalid_quantity_or_sign_is_rejected(
    db: Session, admin: User, quantity: str, reason: AdjustmentReason, code: str
) -> None:
    product = make_product(db, stock=D("5"))

    with pytest.raises(BusinessRuleViolation) as exc:
        adjust(db, admin, product, quantity, reason)

    assert exc.value.code == code


def test_adjustment_unknown_product_raises_not_found(db: Session, admin: User) -> None:
    body = AdjustmentCreate(product_id=999_999, quantity=D("1"), reason=R.INITIAL_LOAD)

    with pytest.raises(NotFound):
        stock.create_adjustment(db, body, created_by=admin.id)


def test_adjustment_without_system_account_fails_and_posts_nothing(
    db: Session, admin: User
) -> None:
    product = make_product(db)
    mapping = db.get(SystemAccount, SystemAccountKey.CAPITAL)
    assert mapping is not None
    db.delete(mapping)
    db.flush()

    with pytest.raises(BusinessRuleViolation) as exc:
        adjust(db, admin, product, "1", R.INITIAL_LOAD)

    assert exc.value.code == "system_account_missing"


# Listings ---------------------------------------------------------------------------------


@pytest.fixture
def ledger(db: Session, admin: User) -> dict[str, Product]:
    """Two products with adjustments on different days plus one delivery movement."""
    a = make_product(db, sku="LST-A", unit_cost=D("10.00"))
    b = make_product(db, sku="LST-B", unit_cost=D("0"))
    adjust(db, admin, a, "10", R.INITIAL_LOAD, on=dt.date(2026, 11, 1))
    adjust(db, admin, b, "4", R.INITIAL_LOAD, on=dt.date(2026, 11, 2))
    adjust(db, admin, a, "-1", R.DAMAGE, on=dt.date(2026, 11, 3))
    order = make_order(db, lines=[(a, D("2"))])
    note = make_delivery_note(db, order=order)
    apply_movements(
        db,
        [
            MovementDraft(
                product_id=a.id,
                quantity=D("-2"),
                kind=StockMovementKind.DELIVERY,
                delivery_note_line_id=note.lines[0].id,
            )
        ],
        movement_date=dt.date(2026, 11, 4),
        created_by=admin.id,
    )
    return {"a": a, "b": b}


def test_list_movements_filters_by_product_kind_and_dates(
    db: Session, ledger: dict[str, Product]
) -> None:
    a = ledger["a"]

    all_a, total_a = stock.list_movements(db, MovementFilters(product_id=a.id), limit=50, offset=0)
    deliveries, _ = stock.list_movements(
        db,
        MovementFilters(product_id=a.id, kind=StockMovementKind.DELIVERY),
        limit=50,
        offset=0,
    )
    window, _ = stock.list_movements(
        db,
        MovementFilters(date_from=dt.date(2026, 11, 2), date_to=dt.date(2026, 11, 3)),
        limit=50,
        offset=0,
    )
    page, page_total = stock.list_movements(db, MovementFilters(product_id=a.id), limit=1, offset=1)

    assert total_a == 3
    assert [m.date.day for m in all_a] == [4, 3, 1]  # newest first
    assert [m.quantity for m in deliveries] == [D("-2")]
    assert {(m.product.sku, m.date.day) for m in window} >= {("LST-B", 2), ("LST-A", 3)}
    assert [m.date.day for m in page] == [3]
    assert page_total == 3
    db.refresh(a)
    assert a.stock_qty == D("7") == ledger_sum(db, a)


def test_list_movements_inverted_range_raises(db: Session) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        stock.list_movements(
            db,
            MovementFilters(date_from=dt.date(2026, 11, 5), date_to=dt.date(2026, 11, 1)),
            limit=50,
            offset=0,
        )

    assert exc.value.code == "invalid_date_range"


def test_list_adjustments_excludes_deliveries_and_reports_entry_numbers(
    db: Session, ledger: dict[str, Product]
) -> None:
    a, b = ledger["a"], ledger["b"]

    items, total = stock.list_adjustments(db, MovementFilters(product_id=a.id), limit=50, offset=0)
    zero_cost, _ = stock.list_adjustments(db, MovementFilters(product_id=b.id), limit=50, offset=0)
    damages, damage_total = stock.list_adjustments(
        db, MovementFilters(reason=R.DAMAGE, product_id=a.id), limit=50, offset=0
    )

    assert total == 2
    assert [i.movement.reason for i in items] == [R.DAMAGE, R.INITIAL_LOAD]
    assert all(i.journal_entry_number is not None for i in items)
    assert [i.journal_entry_number for i in zero_cost] == [None]
    assert damage_total == 1
    assert damages[0].movement.quantity == D("-1")
