"""Report datasets with concrete, hand-computed amounts (see each fixture's docstring)."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.catalog import Customer, Product
from app.models.enums import VatRate
from app.models.sales import Invoice
from tests.factories import make_customer, make_invoice, make_product, make_receipt, make_supplier

D = Decimal
OCT_1, OCT_31 = dt.date(2026, 10, 1), dt.date(2026, 10, 31)
SEP_30, NOV_1 = dt.date(2026, 9, 30), dt.date(2026, 11, 1)
AS_OF = dt.date(2026, 11, 27)


@dataclass
class SalesData:
    alfa: Customer
    beta: Customer
    p21: Product
    p105: Product


@pytest.fixture
def sales_data(db: Session) -> SalesData:
    """p21 = 100.00 at 21% · p105 = 200.00 at 10.5%.

    | date       | customer | lines                 | net     | vat 21 | vat 10.5 | total   |
    | 2026-09-30 | Alfa     | p21 x1                | 100.00  | 21.00  | 0.00     | 121.00  |
    | 2026-10-01 | Alfa     | p21 x10, p105 x2.5    | 1500.00 | 210.00 | 52.50    | 1762.50 |
    | 2026-10-01 | Beta     | p21 x1                | 100.00  | 21.00  | 0.00     | 121.00  |
    | 2026-10-31 | Alfa     | p105 x1               | 200.00  | 0.00   | 21.00    | 221.00  |
    | 2026-11-01 | Beta     | p21 x3                | 300.00  | 63.00  | 0.00     | 363.00  |
    """
    alfa = make_customer(db, name="Alfa SA")
    beta = make_customer(db, name="Beta SRL")
    p21 = make_product(db, sku="P21", name="Widget", unit_price=D("100.00"))
    p105 = make_product(
        db, sku="P105", name="Food", unit_price=D("200.00"), vat_rate=VatRate.VAT_10_5
    )
    make_invoice(db, customer=alfa, lines=[(p21, D("1"))], invoice_date=SEP_30)
    make_invoice(db, customer=alfa, lines=[(p21, D("10")), (p105, D("2.5"))], invoice_date=OCT_1)
    make_invoice(db, customer=beta, lines=[(p21, D("1"))], invoice_date=OCT_1)
    make_invoice(db, customer=alfa, lines=[(p105, D("1"))], invoice_date=OCT_31)
    make_invoice(db, customer=beta, lines=[(p21, D("3"))], invoice_date=NOV_1)
    return SalesData(alfa=alfa, beta=beta, p21=p21, p105=p105)


@dataclass
class LowStockData:
    products: dict[str, Product]


@pytest.fixture
def low_stock_data(db: Session) -> LowStockData:
    """LOW-A 2/5 (Acme) · LOW-EQ 5/5 · OK 6/5 · INACTIVE 0/10 · LOW-ZERO 0/10 (stock/min)."""
    acme = make_supplier(db, name="Acme")
    specs = {
        "LOW-A": (D("2"), D("5"), acme, True),
        "LOW-EQ": (D("5"), D("5"), None, True),
        "OK": (D("6"), D("5"), None, True),
        "INACTIVE": (D("0"), D("10"), None, False),
        "LOW-ZERO": (D("0"), D("10"), None, True),
    }
    products = {
        sku: make_product(
            db, sku=sku, stock=stock, min_stock=minimum, supplier=supplier, is_active=active
        )
        for sku, (stock, minimum, supplier, active) in specs.items()
    }
    return LowStockData(products=products)


@dataclass
class ReceivablesData:
    alfa: Customer
    beta: Customer
    invoices: dict[str, Invoice]


def _days_ago(days: int) -> dt.date:
    return AS_OF - dt.timedelta(days=days)


@pytest.fixture
def receivables_data(db: Session) -> ReceivablesData:
    """As of 2026-11-27. Product = 100.00 at 21%, so qty n -> total 121.00 x n.

    Alfa (payment terms 0 days: due date = invoice date):
      d30   qty 1  121.00                      -> 0-30
      d31   qty 2  242.00 (42.00 paid AFTER as_of, ignored) -> 31-60
      d60   qty 3  363.00                      -> 31-60
      d61   qty 4  484.00                      -> 61-90
      d90   qty 5  605.00                      -> 61-90
      d91   qty 10 1210.00 - 210.00 paid = 1000.00 -> +90
      paid  qty 1  121.00 fully paid           -> excluded
      later qty 1  issued AFTER as_of          -> excluded
      => 0-30 121.00 · 31-60 605.00 · 61-90 1089.00 · +90 1000.00 · balance 2815.00 (6 invoices)
    Beta (30 days): issued 10 days ago, not yet due, 121.00 - 21.00 = 100.00 -> 0-30
    Gamma: one invoice fully paid -> no row
    Totals: 0-30 221.00 · 31-60 605.00 · 61-90 1089.00 · +90 1000.00 · balance 2915.00
    """
    product = make_product(db, unit_price=D("100.00"))
    alfa = make_customer(db, name="Alfa SA", payment_terms_days=0)
    beta = make_customer(db, name="Beta SRL", payment_terms_days=30)
    gamma = make_customer(db, name="Gamma SA", payment_terms_days=0)

    def invoice(customer: Customer, days: int, qty: str) -> Invoice:
        return make_invoice(
            db, customer=customer, lines=[(product, D(qty))], invoice_date=_days_ago(days)
        )

    invoices = {
        "d30": invoice(alfa, 30, "1"),
        "d31": invoice(alfa, 31, "2"),
        "d60": invoice(alfa, 60, "3"),
        "d61": invoice(alfa, 61, "4"),
        "d90": invoice(alfa, 90, "5"),
        "d91": invoice(alfa, 91, "10"),
        "paid": invoice(alfa, 31, "1"),
        "later": invoice(alfa, -1, "1"),
        "beta": invoice(beta, 10, "1"),
        "gamma": invoice(gamma, 45, "1"),
    }
    make_receipt(
        db,
        customer=alfa,
        allocations=[(invoices["d91"], D("210.00")), (invoices["paid"], D("121.00"))],
        receipt_date=_days_ago(5),
    )
    make_receipt(
        db, customer=alfa, allocations=[(invoices["d31"], D("42.00"))], receipt_date=_days_ago(-1)
    )
    make_receipt(
        db, customer=beta, allocations=[(invoices["beta"], D("21.00"))], receipt_date=AS_OF
    )
    make_receipt(
        db, customer=gamma, allocations=[(invoices["gamma"], D("121.00"))], receipt_date=AS_OF
    )
    return ReceivablesData(alfa=alfa, beta=beta, invoices=invoices)
