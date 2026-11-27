import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.config import get_settings
from app.core.errors import BusinessRuleViolation, NotFound
from app.models.accounting import JournalEntry, SystemAccount
from app.models.catalog import Product
from app.models.enums import (
    DocumentType,
    JournalSourceType,
    OrderStatus,
    StockMovementKind,
    SystemAccountKey,
)
from app.models.sales import DeliveryNote, DeliveryNoteLine, Order
from app.models.stock import StockMovement
from app.models.users import User
from app.services import delivery_notes as svc
from app.services.delivery_notes import DeliveryLineInput
from app.services.orders import get_order
from app.services.sequences import next_number
from tests.factories import make_delivery_note, make_invoice, make_order, make_product, make_user

D = Decimal


@pytest.fixture
def seller(db: Session) -> User:
    return make_user(db)


def deliver(
    db: Session, seller: User, order: Order, *pairs: tuple[int, str], **kwargs: object
) -> DeliveryNote:
    return svc.create_delivery_note(
        db,
        order.id,
        lines=[DeliveryLineInput(line_id, D(qty)) for line_id, qty in pairs],
        created_by=seller.id,
        **kwargs,  # type: ignore[arg-type]
    )


def expect_rule(code: str) -> pytest.RaisesExc[BusinessRuleViolation]:
    return pytest.raises(BusinessRuleViolation, check=lambda e: e.code == code)


def ledger_sum(db: Session, product: Product) -> Decimal:
    return D(
        db.scalar(
            select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
                StockMovement.product_id == product.id
            )
        )
    )


def delivery_entry(db: Session, note: DeliveryNote) -> JournalEntry | None:
    return db.scalars(
        select(JournalEntry).where(
            JournalEntry.source_type == JournalSourceType.DELIVERY_NOTE,
            JournalEntry.source_id == note.id,
        )
    ).one_or_none()


def account_id(db: Session, key: SystemAccountKey) -> int:
    return db.get_one(SystemAccount, key).account_id


# Happy path --------------------------------------------------------------------------------


def test_create_delivery_note_full_delivery_moves_stock_posts_cogs_and_delivers(
    db: Session, seller: User
) -> None:
    a = make_product(db, unit_cost=D("60.00"), stock=D("10"))
    b = make_product(db, unit_cost=D("33.33"), stock=D("4"))
    order = make_order(db, lines=[(a, D("3")), (b, D("2.5"))])
    line_a, line_b = order.lines

    note = deliver(db, seller, order, (line_a.id, "3"), (line_b.id, "2.5"))

    # Document
    assert note.point_of_sale == get_settings().point_of_sale
    assert note.date == today()
    assert note.created_by == seller.id
    assert [(ln.order_line_id, ln.product_id, ln.quantity) for ln in note.lines] == [
        (line_a.id, a.id, D("3")),
        (line_b.id, b.id, D("2.5")),
    ]
    # Stock: one DELIVERY movement per line, cost snapshot, cache == ledger
    movements = db.scalars(
        select(StockMovement)
        .where(StockMovement.delivery_note_line_id.in_([ln.id for ln in note.lines]))
        .order_by(StockMovement.id)
    ).all()
    assert [(m.product_id, m.quantity, m.kind, m.unit_cost) for m in movements] == [
        (a.id, D("-3"), StockMovementKind.DELIVERY, D("60.00")),
        (b.id, D("-2.5"), StockMovementKind.DELIVERY, D("33.33")),
    ]
    db.refresh(a)
    db.refresh(b)
    assert a.stock_qty == D("7") == ledger_sum(db, a)
    assert b.stock_qty == D("1.5") == ledger_sum(db, b)
    # COGS entry: 3 x 60.00 = 180.00; 2.5 x 33.33 = 83.325 -> 83.33; total 263.33
    entry = delivery_entry(db, note)
    assert entry is not None
    assert entry.date == note.date
    assert entry.description == f"Remito {svc.format_number(note.point_of_sale, note.number)}"
    assert [(ln.account_id, ln.debit, ln.credit) for ln in entry.lines] == [
        (account_id(db, SystemAccountKey.COGS), D("263.33"), D("0")),
        (account_id(db, SystemAccountKey.INVENTORY), D("0"), D("263.33")),
    ]
    assert sum(ln.debit for ln in entry.lines) == sum(ln.credit for ln in entry.lines)
    # Order
    db.refresh(order)
    assert order.status is OrderStatus.DELIVERED


def test_create_delivery_note_partial_deliveries_update_status_and_pending(
    db: Session, seller: User
) -> None:
    a = make_product(db, unit_cost=D("10.00"), stock=D("20"))
    b = make_product(db, unit_cost=D("5.00"), stock=D("20"))
    order = make_order(db, lines=[(a, D("10")), (b, D("4"))])
    line_a, line_b = order.lines

    first = deliver(db, seller, order, (line_a.id, "6"))

    view = get_order(db, order.id)
    assert view.order.status is OrderStatus.PARTIALLY_DELIVERED
    assert [lv.delivered_quantity for lv in view.lines] == [D("6"), D("0")]
    assert [lv.pending_quantity for lv in view.lines] == [D("4"), D("4")]

    second = deliver(db, seller, order, (line_a.id, "4"), (line_b.id, "4"))

    view = get_order(db, order.id)
    assert view.order.status is OrderStatus.DELIVERED
    assert [lv.pending_quantity for lv in view.lines] == [D("0"), D("0")]
    assert second.number == first.number + 1
    assert [n.id for n in view.order.delivery_notes] == [first.id, second.id]
    first_entry, second_entry = delivery_entry(db, first), delivery_entry(db, second)
    assert first_entry is not None
    assert second_entry is not None
    assert first_entry.lines[0].debit == D("60.00")  # 6 x 10.00
    assert second_entry.lines[0].debit == D("60.00")  # 4 x 10.00 + 4 x 5.00
    db.refresh(a)
    db.refresh(b)
    assert a.stock_qty == D("10") == ledger_sum(db, a)
    assert b.stock_qty == D("16") == ledger_sum(db, b)


def test_create_delivery_note_partial_then_partial_stays_partially_delivered(
    db: Session, seller: User
) -> None:
    product = make_product(db, stock=D("10"))
    order = make_order(db, lines=[(product, D("5"))])
    line = order.lines[0]

    deliver(db, seller, order, (line.id, "1"))
    deliver(db, seller, order, (line.id, "1.5"))

    view = get_order(db, order.id)
    assert view.order.status is OrderStatus.PARTIALLY_DELIVERED
    assert view.lines[0].pending_quantity == D("2.5")


def test_create_delivery_note_uses_snapshot_cost_not_later_changes(
    db: Session, seller: User
) -> None:
    product = make_product(db, unit_cost=D("60.00"), stock=D("10"))
    order = make_order(db, lines=[(product, D("2"))])
    note = deliver(db, seller, order, (order.lines[0].id, "2"))

    product.unit_cost = D("99.00")
    db.flush()

    entry = delivery_entry(db, note)
    assert entry is not None
    assert entry.lines[0].debit == D("120.00")


def test_create_delivery_note_zero_cost_skips_entry(db: Session, seller: User) -> None:
    product = make_product(db, unit_cost=D("0.00"), stock=D("5"))
    order = make_order(db, lines=[(product, D("2"))])

    note = deliver(db, seller, order, (order.lines[0].id, "2"))

    assert delivery_entry(db, note) is None
    db.refresh(product)
    assert product.stock_qty == D("3")


def test_create_delivery_note_explicit_date_dates_note_movements_and_entry(
    db: Session, seller: User
) -> None:
    product = make_product(db, stock=D("5"))
    order = make_order(db, lines=[(product, D("1"))])
    when = dt.date(2026, 3, 1)

    note = deliver(db, seller, order, (order.lines[0].id, "1"), note_date=when)

    movement = db.scalars(
        select(StockMovement).where(StockMovement.delivery_note_line_id == note.lines[0].id)
    ).one()
    entry = delivery_entry(db, note)
    assert entry is not None
    assert note.date == movement.date == entry.date == when


# Rejections --------------------------------------------------------------------------------


def test_create_delivery_note_insufficient_stock_persists_nothing(
    db: Session, seller: User
) -> None:
    enough = make_product(db, stock=D("10"))
    short = make_product(db, stock=D("1"))
    order = make_order(db, lines=[(enough, D("2")), (short, D("2"))])
    line_ok, line_short = order.lines
    movements_before = db.scalar(select(func.count()).select_from(StockMovement))
    entries_before = db.scalar(select(func.count()).select_from(JournalEntry))

    with expect_rule("insufficient_stock"):
        deliver(db, seller, order, (line_ok.id, "2"), (line_short.id, "2"))

    assert db.scalar(select(func.count()).where(DeliveryNote.order_id == order.id)) == 0
    assert db.scalar(select(func.count()).select_from(DeliveryNoteLine)) == 0
    assert db.scalar(select(func.count()).select_from(StockMovement)) == movements_before
    assert db.scalar(select(func.count()).select_from(JournalEntry)) == entries_before
    db.refresh(enough)
    db.refresh(short)
    db.refresh(order)
    assert enough.stock_qty == D("10") == ledger_sum(db, enough)
    assert short.stock_qty == D("1") == ledger_sum(db, short)
    assert order.status is OrderStatus.CONFIRMED


def test_create_delivery_note_insufficient_stock_consumes_no_number(
    db: Session, seller: User
) -> None:
    pos = get_settings().point_of_sale
    product = make_product(db, stock=D("1"))
    order = make_order(db, lines=[(product, D("5"))])
    expected = next_number(db, DocumentType.DELIVERY_NOTE, point_of_sale=pos) + 1

    with expect_rule("insufficient_stock"):
        deliver(db, seller, order, (order.lines[0].id, "5"))
    note = deliver(db, seller, order, (order.lines[0].id, "1"))

    assert note.number == expected


def test_create_delivery_note_over_delivery_is_rejected(db: Session, seller: User) -> None:
    product = make_product(db, stock=D("100"))
    order = make_order(db, lines=[(product, D("5"))])
    line = order.lines[0]

    with expect_rule("exceeds_pending"):
        deliver(db, seller, order, (line.id, "5.001"))


def test_create_delivery_note_over_delivery_after_partial_is_rejected(
    db: Session, seller: User
) -> None:
    product = make_product(db, stock=D("100"))
    order = make_order(db, lines=[(product, D("5"))])
    line = order.lines[0]
    deliver(db, seller, order, (line.id, "3"))

    with expect_rule("exceeds_pending"):
        deliver(db, seller, order, (line.id, "2.5"))

    assert get_order(db, order.id).lines[0].pending_quantity == D("2")


def test_create_delivery_note_line_of_other_order_is_rejected(db: Session, seller: User) -> None:
    order = make_order(db, lines=[(make_product(db, stock=D("5")), D("1"))])
    other = make_order(db, lines=[(make_product(db, stock=D("5")), D("1"))])

    with expect_rule("line_not_in_order"):
        deliver(db, seller, order, (other.lines[0].id, "1"))


def test_create_delivery_note_without_lines_is_rejected(db: Session, seller: User) -> None:
    order = make_order(db)

    with expect_rule("no_lines"):
        deliver(db, seller, order)


def test_create_delivery_note_duplicate_line_is_rejected(db: Session, seller: User) -> None:
    order = make_order(db, lines=[(make_product(db, stock=D("5")), D("4"))])
    line = order.lines[0]

    with expect_rule("duplicate_line"):
        deliver(db, seller, order, (line.id, "1"), (line.id, "1"))


@pytest.mark.parametrize("quantity", ["0", "-1", "0.0005"])
def test_create_delivery_note_bad_quantity_is_rejected(
    db: Session, seller: User, quantity: str
) -> None:
    order = make_order(db, lines=[(make_product(db, stock=D("5")), D("4"))])

    with expect_rule("bad_quantity"):
        deliver(db, seller, order, (order.lines[0].id, quantity))


@pytest.mark.parametrize(
    "status", [OrderStatus.DRAFT, OrderStatus.DELIVERED, OrderStatus.CANCELLED]
)
def test_create_delivery_note_order_not_deliverable_is_invalid_status(
    db: Session, seller: User, status: OrderStatus
) -> None:
    order = make_order(db, status=status, lines=[(make_product(db, stock=D("5")), D("1"))])

    with expect_rule("invalid_status"):
        deliver(db, seller, order, (order.lines[0].id, "1"))


def test_create_delivery_note_unknown_order_is_not_found(db: Session, seller: User) -> None:
    with pytest.raises(NotFound):
        svc.create_delivery_note(
            db, 999_999, lines=[DeliveryLineInput(1, D("1"))], created_by=seller.id
        )


# Reads -------------------------------------------------------------------------------------


def test_get_delivery_note_unknown_is_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        svc.get_delivery_note(db, 999_999)


def test_list_delivery_notes_filters_by_order_and_invoiced(db: Session) -> None:
    invoice = make_invoice(db)
    invoiced_note = invoice.delivery_note
    order = make_order(db)
    pending_note = make_delivery_note(db, order=order)

    def ids(**filters: object) -> set[int]:
        notes, _ = svc.list_delivery_notes(db, limit=200, **filters)  # type: ignore[arg-type]
        return {n.id for n in notes}

    assert {invoiced_note.id, pending_note.id} <= ids()
    assert ids(order_id=order.id) == {pending_note.id}
    assert invoiced_note.id in ids(invoiced=True)
    assert pending_note.id not in ids(invoiced=True)
    assert pending_note.id in ids(invoiced=False)
    assert invoiced_note.id not in ids(invoiced=False)


def test_list_delivery_notes_paginates_and_counts(db: Session) -> None:
    order = make_order(db, lines=[(make_product(db), D("3"))])
    line = order.lines[0]
    for _ in range(3):
        make_delivery_note(db, order=order, quantities={line.id: D("1")})

    notes, total = svc.list_delivery_notes(db, order_id=order.id, limit=2, offset=0)

    assert total == 3
    assert len(notes) == 2
