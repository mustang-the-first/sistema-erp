import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.errors import BusinessRuleViolation, NotFound
from app.models.enums import OrderStatus, VatRate
from app.models.sales import OrderLine
from app.models.users import User
from app.services import orders as svc
from app.services.orders import OrderLineInput
from tests.factories import make_customer, make_delivery_note, make_order, make_product, make_user

D = Decimal


@pytest.fixture
def seller(db: Session) -> User:
    return make_user(db)


def create(db: Session, seller: User, **kwargs: object) -> svc.OrderView:
    if "customer_id" not in kwargs:
        kwargs["customer_id"] = make_customer(db).id
    if "lines" not in kwargs:
        kwargs["lines"] = [OrderLineInput(make_product(db).id, D("1"))]
    return svc.create_order(db, created_by=seller.id, **kwargs)  # type: ignore[arg-type]


def expect_rule(code: str) -> pytest.RaisesExc[BusinessRuleViolation]:
    return pytest.raises(BusinessRuleViolation, check=lambda e: e.code == code)


# Create ------------------------------------------------------------------------------------


def test_create_order_valid_input_creates_draft_with_snapshots(db: Session, seller: User) -> None:
    customer = make_customer(db)
    p21 = make_product(db, unit_price=D("100.00"), vat_rate=VatRate.VAT_21)
    p105 = make_product(db, unit_price=D("100.00"), vat_rate=VatRate.VAT_10_5)

    view = svc.create_order(
        db,
        customer_id=customer.id,
        lines=[OrderLineInput(p21.id, D("10")), OrderLineInput(p105.id, D("5"))],
        notes="Deliver in the morning",
        created_by=seller.id,
    )

    order = view.order
    assert order.status is OrderStatus.DRAFT
    assert order.date == today()
    assert order.created_by == seller.id
    assert order.notes == "Deliver in the morning"
    assert [(ln.product_id, ln.unit_price, ln.vat_rate) for ln in order.lines] == [
        (p21.id, D("100.00"), VatRate.VAT_21),
        (p105.id, D("100.00"), VatRate.VAT_10_5),
    ]
    assert (view.totals.net_total, view.totals.vat_21, view.totals.vat_10_5) == (
        D("1500.00"),
        D("210.00"),
        D("52.50"),
    )
    assert view.totals.total == D("1762.50")
    assert [lv.pending_quantity for lv in view.lines] == [D("10"), D("5")]
    assert [lv.delivered_quantity for lv in view.lines] == [D("0"), D("0")]


def test_create_order_numbers_are_correlative(db: Session, seller: User) -> None:
    first = create(db, seller).order.number
    second = create(db, seller).order.number

    assert second == first + 1


def test_create_order_explicit_date_is_kept(db: Session, seller: User) -> None:
    view = create(db, seller, order_date=dt.date(2026, 1, 15))

    assert view.order.date == dt.date(2026, 1, 15)


def test_create_order_price_change_later_keeps_snapshot(db: Session, seller: User) -> None:
    product = make_product(db, unit_price=D("100.00"))
    view = create(db, seller, lines=[OrderLineInput(product.id, D("1"))])

    product.unit_price = D("999.00")
    db.flush()

    assert svc.get_order(db, view.order.id).lines[0].line.unit_price == D("100.00")


def test_create_order_unknown_customer_is_not_found(db: Session, seller: User) -> None:
    with pytest.raises(NotFound):
        create(db, seller, customer_id=999_999)


def test_create_order_inactive_customer_is_rejected(db: Session, seller: User) -> None:
    customer = make_customer(db)
    customer.is_active = False
    db.flush()

    with expect_rule("inactive_customer"):
        create(db, seller, customer_id=customer.id)


def test_create_order_inactive_product_is_rejected(db: Session, seller: User) -> None:
    product = make_product(db, is_active=False)

    with expect_rule("inactive_product"):
        create(db, seller, lines=[OrderLineInput(product.id, D("1"))])


def test_create_order_unknown_product_is_not_found(db: Session, seller: User) -> None:
    with pytest.raises(NotFound):
        create(db, seller, lines=[OrderLineInput(999_999, D("1"))])


def test_create_order_without_lines_is_rejected(db: Session, seller: User) -> None:
    with expect_rule("no_lines"):
        create(db, seller, lines=[])


def test_create_order_duplicate_product_is_rejected(db: Session, seller: User) -> None:
    product = make_product(db)

    with expect_rule("duplicate_product"):
        create(
            db,
            seller,
            lines=[OrderLineInput(product.id, D("1")), OrderLineInput(product.id, D("2"))],
        )


@pytest.mark.parametrize("quantity", ["0", "-2", "1.2345"])
def test_create_order_bad_quantity_is_rejected(db: Session, seller: User, quantity: str) -> None:
    with expect_rule("bad_quantity"):
        create(db, seller, lines=[OrderLineInput(make_product(db).id, D(quantity))])


def test_create_order_rejected_consumes_no_number(db: Session, seller: User) -> None:
    first = create(db, seller).order.number
    with expect_rule("no_lines"):
        create(db, seller, lines=[])

    assert create(db, seller).order.number == first + 1


# Read --------------------------------------------------------------------------------------


def test_get_order_unknown_is_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        svc.get_order(db, 999_999)


def test_list_orders_filters_by_status_customer_and_dates(db: Session, seller: User) -> None:
    customer = make_customer(db)
    old = make_order(db, customer=customer, order_date=dt.date(2026, 1, 10))
    new = make_order(db, customer=customer, order_date=dt.date(2026, 2, 10))
    draft = make_order(
        db, customer=customer, order_date=dt.date(2026, 2, 11), status=OrderStatus.DRAFT
    )
    make_order(db, order_date=dt.date(2026, 2, 10))  # other customer

    def ids(**filters: object) -> list[int]:
        items, _ = svc.list_orders(db, customer_id=customer.id, **filters)  # type: ignore[arg-type]
        return [order.id for order, _ in items]

    assert ids() == [draft.id, new.id, old.id]
    assert ids(status=OrderStatus.DRAFT) == [draft.id]
    assert ids(date_from=dt.date(2026, 2, 1)) == [draft.id, new.id]
    assert ids(date_to=dt.date(2026, 2, 10)) == [new.id, old.id]


def test_list_orders_paginates_and_counts(db: Session) -> None:
    customer = make_customer(db)
    for _ in range(3):
        make_order(db, customer=customer)

    items, total = svc.list_orders(db, customer_id=customer.id, limit=2, offset=1)

    assert total == 3
    assert len(items) == 2


def test_list_orders_includes_totals(db: Session) -> None:
    customer = make_customer(db)
    make_order(db, customer=customer, lines=[(make_product(db, unit_price=D("10.00")), D("3"))])

    [(_, totals)], _ = svc.list_orders(db, customer_id=customer.id)

    assert totals.total == D("36.30")


# Update ------------------------------------------------------------------------------------


def test_update_order_notes_only_keeps_lines(db: Session, seller: User) -> None:
    view = create(db, seller, notes="old")
    line_ids = [ln.id for ln in view.order.lines]

    updated = svc.update_order(db, view.order.id, notes="new")

    assert updated.order.notes == "new"
    assert [ln.id for ln in updated.order.lines] == line_ids


def test_update_order_notes_none_clears_notes(db: Session, seller: User) -> None:
    view = create(db, seller, notes="old")

    assert svc.update_order(db, view.order.id, notes=None).order.notes is None


def test_update_order_unset_notes_keeps_notes(db: Session, seller: User) -> None:
    view = create(db, seller, notes="keep")

    updated = svc.update_order(db, view.order.id, lines=[OrderLineInput(make_product(db).id, D(2))])

    assert updated.order.notes == "keep"


def test_update_order_lines_replaces_all_lines(db: Session, seller: User) -> None:
    kept = make_product(db, unit_price=D("10.00"))
    dropped = make_product(db)
    view = create(
        db, seller, lines=[OrderLineInput(kept.id, D("1")), OrderLineInput(dropped.id, D("1"))]
    )
    kept.unit_price = D("12.00")  # replacement takes a fresh snapshot
    added = make_product(db, unit_price=D("5.00"))

    updated = svc.update_order(
        db,
        view.order.id,
        lines=[OrderLineInput(kept.id, D("4")), OrderLineInput(added.id, D("2.5"))],
    )

    assert [(ln.product_id, ln.quantity, ln.unit_price) for ln in updated.order.lines] == [
        (kept.id, D("4"), D("12.00")),
        (added.id, D("2.5"), D("5.00")),
    ]
    remaining = db.scalars(select(OrderLine).where(OrderLine.order_id == view.order.id)).all()
    assert len(remaining) == 2
    assert updated.totals.net_total == D("60.50")


def test_update_order_invalid_lines_change_nothing(db: Session, seller: User) -> None:
    view = create(db, seller, notes="old")
    line_ids = [ln.id for ln in view.order.lines]

    with expect_rule("no_lines"):
        svc.update_order(db, view.order.id, notes="new", lines=[])

    current = svc.get_order(db, view.order.id).order
    db.refresh(current)
    assert current.notes == "old"
    assert [ln.id for ln in current.lines] == line_ids


@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.CONFIRMED,
        OrderStatus.PARTIALLY_DELIVERED,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
    ],
)
def test_update_order_not_draft_is_invalid_status(db: Session, status: OrderStatus) -> None:
    order = make_order(db, status=status)

    with expect_rule("invalid_status"):
        svc.update_order(db, order.id, notes="x")


def test_update_order_unknown_is_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        svc.update_order(db, 999_999, notes="x")


# Transitions -------------------------------------------------------------------------------


def test_confirm_order_draft_becomes_confirmed(db: Session) -> None:
    order = make_order(db, status=OrderStatus.DRAFT)

    assert svc.confirm_order(db, order.id).order.status is OrderStatus.CONFIRMED


@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.CONFIRMED,
        OrderStatus.PARTIALLY_DELIVERED,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
    ],
)
def test_confirm_order_not_draft_is_invalid_status(db: Session, status: OrderStatus) -> None:
    order = make_order(db, status=status)

    with expect_rule("invalid_status"):
        svc.confirm_order(db, order.id)


@pytest.mark.parametrize("status", [OrderStatus.DRAFT, OrderStatus.CONFIRMED])
def test_cancel_order_draft_or_confirmed_becomes_cancelled(
    db: Session, status: OrderStatus
) -> None:
    order = make_order(db, status=status)

    assert svc.cancel_order(db, order.id).order.status is OrderStatus.CANCELLED


@pytest.mark.parametrize(
    "status", [OrderStatus.PARTIALLY_DELIVERED, OrderStatus.DELIVERED, OrderStatus.CANCELLED]
)
def test_cancel_order_other_status_is_invalid_status(db: Session, status: OrderStatus) -> None:
    order = make_order(db, status=status)

    with expect_rule("invalid_status"):
        svc.cancel_order(db, order.id)


def test_cancel_order_with_delivery_notes_is_rejected(db: Session) -> None:
    order = make_order(db, status=OrderStatus.CONFIRMED)
    make_delivery_note(db, order=order)

    with expect_rule("has_delivery_notes"):
        svc.cancel_order(db, order.id)

    db.refresh(order)
    assert order.status is OrderStatus.CONFIRMED


def test_confirm_order_unknown_is_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        svc.confirm_order(db, 999_999)
