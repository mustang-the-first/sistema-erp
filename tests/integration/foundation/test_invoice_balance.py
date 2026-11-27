import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.models.enums import VatRate
from app.services.invoice_balance import (
    invoice_balance,
    invoice_balances,
    invoice_balances_subquery,
)
from tests.factories import make_customer, make_invoice, make_product, make_receipt

D = Decimal
DAY1, DAY10, DAY20 = dt.date(2026, 11, 1), dt.date(2026, 11, 10), dt.date(2026, 11, 20)


def test_invoice_with_mixed_vat_matches_plan_example(db: Session) -> None:
    p21 = make_product(db, unit_price=D("100.00"), vat_rate=VatRate.VAT_21)
    p105 = make_product(db, unit_price=D("100.00"), vat_rate=VatRate.VAT_10_5)

    invoice = make_invoice(db, lines=[(p21, D("10")), (p105, D("5"))])

    assert (invoice.net_total, invoice.vat_21, invoice.vat_10_5, invoice.total) == (
        D("1500.00"),
        D("210.00"),
        D("52.50"),
        D("1762.50"),
    )
    assert invoice_balance(db, invoice.id) == D("1762.50")


def test_partial_payments_reduce_the_balance(db: Session) -> None:
    customer = make_customer(db)
    invoice = make_invoice(db, customer=customer)  # 1 x 100 + 21% = 121.00

    make_receipt(db, customer=customer, allocations=[(invoice, D("21.00"))])
    make_receipt(db, customer=customer, allocations=[(invoice, D("50.00"))])

    assert invoice_balance(db, invoice.id) == D("50.00")


def test_fully_paid_invoice_has_zero_balance(db: Session) -> None:
    customer = make_customer(db)
    invoice = make_invoice(db, customer=customer)

    make_receipt(db, customer=customer, allocations=[(invoice, invoice.total)])

    assert invoice_balance(db, invoice.id) == D("0.00")


def test_one_receipt_can_pay_several_invoices(db: Session) -> None:
    customer = make_customer(db)
    first, second = make_invoice(db, customer=customer), make_invoice(db, customer=customer)

    make_receipt(db, customer=customer, allocations=[(first, D("121.00")), (second, D("21.00"))])

    assert invoice_balances(db, [first.id, second.id]) == {
        first.id: D("0.00"),
        second.id: D("100.00"),
    }


def test_as_of_ignores_later_receipts_and_invoices(db: Session) -> None:
    customer = make_customer(db)
    early = make_invoice(db, customer=customer, invoice_date=DAY1)
    late = make_invoice(db, customer=customer, invoice_date=DAY20)
    make_receipt(db, customer=customer, allocations=[(early, D("100.00"))], receipt_date=DAY20)

    assert invoice_balances(db, [early.id, late.id], as_of=DAY10) == {early.id: D("121.00")}
    assert invoice_balance(db, early.id, as_of=DAY20) == D("21.00")


def test_subquery_exposes_columns_for_aggregation(db: Session) -> None:
    customer = make_customer(db)
    invoice = make_invoice(db, customer=customer, invoice_date=DAY1)
    make_receipt(db, customer=customer, allocations=[(invoice, D("21.00"))])
    balances = invoice_balances_subquery()

    row = db.execute(select(balances).where(balances.c.invoice_id == invoice.id)).one()

    assert row.customer_id == customer.id
    assert row.due_date == DAY1 + dt.timedelta(days=30)
    assert (row.total, row.paid, row.balance) == (D("121.00"), D("21.00"), D("100.00"))


def test_unknown_invoice_is_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        invoice_balance(db, 999_999)


def test_no_ids_returns_empty(db: Session) -> None:
    assert invoice_balances(db, []) == {}
