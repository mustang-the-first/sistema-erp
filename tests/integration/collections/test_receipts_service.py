import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, NotFound
from app.models.accounting import JournalEntry
from app.models.collections import Receipt
from app.models.enums import PaymentMethod
from app.models.users import User
from app.services.collections import (
    AllocationRequest,
    create_receipt,
    get_receipt,
    list_receipts,
)
from app.services.invoice_balance import invoice_balance
from tests.factories import make_customer, make_receipt, make_user
from tests.integration.collections.helpers import DAY, D, invoice_of, receipt_entry_lines

CASH_CODE, BANK_CODE, RECEIVABLES_CODE = "1.1.1.01", "1.1.1.02", "1.1.3.01"


@pytest.fixture
def user(db: Session) -> User:
    return make_user(db)


def pay(
    db: Session,
    user: User,
    customer_id: int,
    *allocations: tuple[int, str],
    method: PaymentMethod = PaymentMethod.CASH,
    day: dt.date = DAY,
    reference: str | None = None,
) -> Receipt:
    return create_receipt(
        db,
        customer_id=customer_id,
        receipt_date=day,
        method=method,
        reference=reference,
        allocations=[AllocationRequest(i, D(a)) for i, a in allocations],
        created_by=user.id,
    )


def receipt_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Receipt)) or 0


# Happy paths --------------------------------------------------------------------------------


def test_create_receipt_partial_payment_leaves_balance(db: Session, user: User) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)  # 121.00

    receipt = pay(db, user, customer.id, (inv.id, "21.00"), reference="  REC-1  ")

    assert receipt.amount == D("21.00")
    assert receipt.reference == "REC-1"
    assert receipt.created_by == user.id
    assert receipt.point_of_sale == 1
    assert [(a.invoice_id, a.amount) for a in receipt.allocations] == [(inv.id, D("21.00"))]
    assert invoice_balance(db, inv.id) == D("100.00")


def test_create_receipt_full_payment_in_two_receipts_zeroes_balance(
    db: Session, user: User
) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)

    first = pay(db, user, customer.id, (inv.id, "21.00"))
    second = pay(db, user, customer.id, (inv.id, "100.00"))

    assert invoice_balance(db, inv.id) == D("0.00")
    assert second.number == first.number + 1


def test_create_receipt_several_invoices_amount_is_sum(db: Session, user: User) -> None:
    customer = make_customer(db)
    a = invoice_of(db, customer, "100.00")  # 121.00
    b = invoice_of(db, customer, "200.00")  # 242.00

    receipt = pay(db, user, customer.id, (b.id, "242.00"), (a.id, "60.50"))

    assert receipt.amount == D("302.50")
    assert {a_.invoice_id: a_.amount for a_ in receipt.allocations} == {
        a.id: D("60.50"),
        b.id: D("242.00"),
    }
    assert invoice_balance(db, a.id) == D("60.50")
    assert invoice_balance(db, b.id) == D("0.00")


def test_create_receipt_cash_posts_cash_against_receivables(db: Session, user: User) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)

    receipt = pay(db, user, customer.id, (inv.id, "121.00"), method=PaymentMethod.CASH)

    assert receipt_entry_lines(db, receipt.id) == {
        CASH_CODE: (D("121.00"), D("0.00")),
        RECEIVABLES_CODE: (D("0.00"), D("121.00")),
    }


def test_create_receipt_transfer_posts_bank_against_receivables(db: Session, user: User) -> None:
    customer = make_customer(db)
    a = invoice_of(db, customer, "100.00")
    b = invoice_of(db, customer, "50.00")  # 60.50

    receipt = pay(
        db, user, customer.id, (a.id, "121.00"), (b.id, "30.25"), method=PaymentMethod.BANK_TRANSFER
    )

    assert receipt_entry_lines(db, receipt.id) == {
        BANK_CODE: (D("151.25"), D("0.00")),
        RECEIVABLES_CODE: (D("0.00"), D("151.25")),
    }
    entry = db.scalars(select(JournalEntry).where(JournalEntry.source_id == receipt.id)).one()
    assert entry.date == DAY
    assert entry.created_by == user.id
    assert f"0001-{receipt.number:08d}" in entry.description


def test_create_receipt_without_date_uses_today(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.collections.today", lambda: dt.date(2026, 11, 20))
    customer = make_customer(db)
    inv = invoice_of(db, customer)

    receipt = create_receipt(
        db,
        customer_id=customer.id,
        method=PaymentMethod.CASH,
        allocations=[AllocationRequest(inv.id, D("1.00"))],
        created_by=user.id,
    )

    assert receipt.date == dt.date(2026, 11, 20)
    assert receipt.reference is None


def test_create_receipt_invoice_dated_same_day_is_accepted(db: Session, user: User) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer, day=DAY)

    receipt = pay(db, user, customer.id, (inv.id, "1.00"), day=DAY)

    assert receipt.date == DAY


# Rejections ---------------------------------------------------------------------------------


def test_create_receipt_overpayment_is_rejected(db: Session, user: User) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)

    with pytest.raises(BusinessRuleViolation) as exc_info:
        pay(db, user, customer.id, (inv.id, "121.01"))

    assert exc_info.value.code == "overpayment"
    assert receipt_count(db) == 0


def test_create_receipt_overpayment_after_previous_receipt_is_rejected(
    db: Session, user: User
) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)
    make_receipt(db, customer=customer, allocations=[(inv, D("100.00"))], receipt_date=DAY)

    with pytest.raises(BusinessRuleViolation) as exc_info:
        pay(db, user, customer.id, (inv.id, "21.01"))

    assert exc_info.value.code == "overpayment"


def test_create_receipt_invoice_of_another_customer_is_rejected(db: Session, user: User) -> None:
    customer, other = make_customer(db), make_customer(db)
    own = invoice_of(db, customer)
    foreign = invoice_of(db, other)

    with pytest.raises(BusinessRuleViolation) as exc_info:
        pay(db, user, customer.id, (own.id, "10.00"), (foreign.id, "10.00"))

    assert exc_info.value.code == "invoice_not_owned"
    assert receipt_count(db) == 0


def test_create_receipt_invoice_dated_after_receipt_is_rejected(db: Session, user: User) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer, day=DAY + dt.timedelta(days=1))

    with pytest.raises(BusinessRuleViolation) as exc_info:
        pay(db, user, customer.id, (inv.id, "10.00"), day=DAY)

    assert exc_info.value.code == "invoice_after_receipt"


@pytest.mark.parametrize(
    ("amounts", "code"),
    [
        ((), "no_allocations"),
        (("10.00", "5.00"), "duplicate_invoice"),
        (("0",), "invalid_amount"),
        (("-1.00",), "invalid_amount"),
        (("10.005",), "invalid_amount"),
    ],
)
def test_create_receipt_invalid_allocations_are_rejected(
    db: Session, user: User, amounts: tuple[str, ...], code: str
) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)

    with pytest.raises(BusinessRuleViolation) as exc_info:
        pay(db, user, customer.id, *[(inv.id, a) for a in amounts])

    assert exc_info.value.code == code
    assert receipt_count(db) == 0


def test_create_receipt_unknown_customer_is_not_found(db: Session, user: User) -> None:
    with pytest.raises(NotFound) as exc_info:
        pay(db, user, 999_999, (1, "10.00"))

    assert exc_info.value.code == "customer_not_found"


def test_create_receipt_unknown_invoice_is_not_found(db: Session, user: User) -> None:
    customer = make_customer(db)

    with pytest.raises(NotFound) as exc_info:
        pay(db, user, customer.id, (999_999, "10.00"))

    assert exc_info.value.code == "invoice_not_found"


# Reads --------------------------------------------------------------------------------------


def test_get_receipt_returns_allocations(db: Session, user: User) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)
    created = pay(db, user, customer.id, (inv.id, "21.00"))
    db.expire_all()

    receipt = get_receipt(db, created.id)

    assert receipt.customer.id == customer.id
    assert [(a.invoice.id, a.amount) for a in receipt.allocations] == [(inv.id, D("21.00"))]


def test_get_receipt_unknown_is_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        get_receipt(db, 999_999)


def test_list_receipts_filters_by_customer_and_dates(db: Session) -> None:
    customer, other = make_customer(db), make_customer(db)
    inv = invoice_of(db, customer, day=dt.date(2026, 10, 1))
    other_inv = invoice_of(db, other, day=dt.date(2026, 10, 1))
    oct_ = make_receipt(
        db, customer=customer, allocations=[(inv, D("1.00"))], receipt_date=dt.date(2026, 10, 5)
    )
    nov = make_receipt(
        db, customer=customer, allocations=[(inv, D("2.00"))], receipt_date=dt.date(2026, 11, 5)
    )
    make_receipt(
        db, customer=other, allocations=[(other_inv, D("3.00"))], receipt_date=dt.date(2026, 11, 5)
    )

    by_customer = list_receipts(db, customer_id=customer.id)
    november = list_receipts(
        db, customer_id=customer.id, date_from=dt.date(2026, 11, 1), date_to=dt.date(2026, 11, 30)
    )
    october = list_receipts(db, customer_id=customer.id, date_to=dt.date(2026, 10, 31))

    assert [r.id for r in by_customer.items] == [nov.id, oct_.id]  # newest first
    assert by_customer.total == 2
    assert [r.id for r in november.items] == [nov.id]
    assert [r.id for r in october.items] == [oct_.id]


def test_list_receipts_paginates_with_total(db: Session) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer, day=dt.date(2026, 10, 1))
    for day in (1, 2, 3):
        make_receipt(
            db,
            customer=customer,
            allocations=[(inv, D("1.00"))],
            receipt_date=dt.date(2026, 10, day),
        )

    page = list_receipts(db, customer_id=customer.id, limit=2, offset=2)

    assert page.total == 3
    assert [r.date for r in page.items] == [dt.date(2026, 10, 1)]


def test_list_receipts_inverted_range_is_rejected(db: Session) -> None:
    with pytest.raises(BusinessRuleViolation) as exc_info:
        list_receipts(db, date_from=dt.date(2026, 11, 2), date_to=dt.date(2026, 11, 1))

    assert exc_info.value.code == "invalid_date_range"


def test_balance_checks_use_decimal_amounts(db: Session, user: User) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer, "0.10")  # 0.12 total (0.021 rounds to 0.02)

    pay(db, user, customer.id, (inv.id, "0.12"))

    assert invoice_balance(db, inv.id) == Decimal("0.00")
