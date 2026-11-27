import datetime as dt

import pytest
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, NotFound
from app.services.collections import (
    MovementKind,
    active_customers,
    customer_statement,
    open_invoices,
)
from tests.factories import make_customer, make_receipt
from tests.integration.collections.helpers import D, invoice_of

OCT_1, OCT_15, NOV_1, NOV_10, NOV_20 = (
    dt.date(2026, 10, 1),
    dt.date(2026, 10, 15),
    dt.date(2026, 11, 1),
    dt.date(2026, 11, 10),
    dt.date(2026, 11, 20),
)


# Open invoices ------------------------------------------------------------------------------


def test_open_invoices_lists_only_positive_balances_with_days_overdue(db: Session) -> None:
    customer = make_customer(db, payment_terms_days=30)
    paid = invoice_of(db, customer, day=OCT_1)  # 121.00, due 31/10
    partial = invoice_of(db, customer, "200.00", day=OCT_15)  # 242.00, due 14/11
    untouched = invoice_of(db, customer, "50.00", day=NOV_10)  # 60.50, due 10/12
    invoice_of(db, make_customer(db))  # another customer's invoice
    make_receipt(db, customer=customer, allocations=[(paid, D("121.00")), (partial, D("42.00"))])

    page = open_invoices(db, customer.id, as_of=NOV_20)

    assert [(i.invoice.id, i.paid, i.balance, i.days_overdue) for i in page.items] == [
        (partial.id, D("42.00"), D("200.00"), 6),
        (untouched.id, D("0"), D("60.50"), 0),
    ]
    assert page.total == 2
    assert page.total_balance == D("260.50")
    assert page.items[0].document_number == f"A 0001-{partial.number:08d}"


def test_open_invoices_paginates(db: Session) -> None:
    customer = make_customer(db)
    first = invoice_of(db, customer, day=OCT_1)
    invoice_of(db, customer, day=OCT_15)

    page = open_invoices(db, customer.id, limit=1, offset=0)

    assert page.total == 2
    assert [i.invoice.id for i in page.items] == [first.id]


def test_open_invoices_unknown_customer_is_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        open_invoices(db, 999_999)


def test_active_customers_excludes_inactive(db: Session) -> None:
    active = make_customer(db, name="Zeta active")
    inactive = make_customer(db, name="Alpha inactive")
    inactive.is_active = False
    db.flush()

    ids = [c.id for c in active_customers(db)]

    assert active.id in ids
    assert inactive.id not in ids


# Statement ----------------------------------------------------------------------------------


def statement_fixture(db: Session) -> dict[str, int]:
    """Oct: invoice 121.00, receipt 100.00. Nov: invoice 242.00 (10/11), receipt 50.00 (20/11)."""
    customer = make_customer(db)
    oct_inv = invoice_of(db, customer, day=OCT_1)
    nov_inv = invoice_of(db, customer, "200.00", day=NOV_10)
    oct_rec = make_receipt(
        db, customer=customer, allocations=[(oct_inv, D("100.00"))], receipt_date=OCT_15
    )
    nov_rec = make_receipt(
        db, customer=customer, allocations=[(nov_inv, D("50.00"))], receipt_date=NOV_20
    )
    other = make_customer(db)
    make_receipt(
        db,
        customer=other,
        allocations=[(invoice_of(db, other, day=OCT_1), D("1.00"))],
        receipt_date=OCT_15,
    )
    return {
        "customer": customer.id,
        "oct_inv": oct_inv.id,
        "nov_inv": nov_inv.id,
        "oct_rec": oct_rec.id,
        "nov_rec": nov_rec.id,
    }


def test_statement_without_filters_runs_from_zero(db: Session) -> None:
    ids = statement_fixture(db)

    statement = customer_statement(db, ids["customer"])

    assert statement.opening_balance == D("0")
    assert [(m.kind, m.document_id, m.debit, m.credit, m.balance) for m in statement.movements] == [
        (MovementKind.INVOICE, ids["oct_inv"], D("121.00"), D("0"), D("121.00")),
        (MovementKind.RECEIPT, ids["oct_rec"], D("0"), D("100.00"), D("21.00")),
        (MovementKind.INVOICE, ids["nov_inv"], D("242.00"), D("0"), D("263.00")),
        (MovementKind.RECEIPT, ids["nov_rec"], D("0"), D("50.00"), D("213.00")),
    ]
    assert statement.closing_balance == D("213.00")
    assert (statement.total_debit, statement.total_credit) == (D("363.00"), D("150.00"))


def test_statement_from_date_carries_opening_balance(db: Session) -> None:
    ids = statement_fixture(db)

    statement = customer_statement(db, ids["customer"], date_from=NOV_1)

    assert statement.opening_balance == D("21.00")  # 121.00 - 100.00
    assert [(m.document_id, m.balance) for m in statement.movements] == [
        (ids["nov_inv"], D("263.00")),
        (ids["nov_rec"], D("213.00")),
    ]
    assert statement.closing_balance == D("213.00")


def test_statement_date_range_filters_movements(db: Session) -> None:
    ids = statement_fixture(db)

    statement = customer_statement(db, ids["customer"], date_from=NOV_1, date_to=NOV_10)

    assert statement.opening_balance == D("21.00")
    assert [m.document_id for m in statement.movements] == [ids["nov_inv"]]
    assert statement.closing_balance == D("263.00")
    assert (statement.date_from, statement.date_to) == (NOV_1, NOV_10)


def test_statement_period_without_movements_closes_at_opening(db: Session) -> None:
    ids = statement_fixture(db)

    statement = customer_statement(
        db, ids["customer"], date_from=dt.date(2026, 12, 1), date_to=dt.date(2026, 12, 31)
    )

    assert statement.movements == []
    assert statement.opening_balance == statement.closing_balance == D("213.00")


def test_statement_unknown_customer_is_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        customer_statement(db, 999_999)


def test_statement_inverted_range_is_rejected(db: Session) -> None:
    customer = make_customer(db)

    with pytest.raises(BusinessRuleViolation) as exc_info:
        customer_statement(db, customer.id, date_from=NOV_10, date_to=NOV_1)

    assert exc_info.value.code == "invalid_date_range"
