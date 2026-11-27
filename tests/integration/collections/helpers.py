"""Shared helpers for the collections integration tests."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import JournalEntry
from app.models.catalog import Customer, Product
from app.models.enums import JournalSourceType
from app.models.sales import Invoice
from tests.factories import make_invoice, make_product

D = Decimal
DAY = dt.date(2026, 11, 10)


def invoice_of(
    db: Session, customer: Customer, net: str = "100.00", *, day: dt.date = DAY
) -> Invoice:
    """Invoice with one 21% line: total = net x 1.21 (100.00 -> 121.00)."""
    product: Product = make_product(db, unit_price=D(net))
    return make_invoice(db, customer=customer, lines=[(product, D("1"))], invoice_date=day)


def receipt_entry_lines(db: Session, receipt_id: int) -> dict[str, tuple[Decimal, Decimal]]:
    entry = db.scalars(
        select(JournalEntry).where(
            JournalEntry.source_type == JournalSourceType.RECEIPT,
            JournalEntry.source_id == receipt_id,
        )
    ).one()
    return {line.account.code: (line.debit, line.credit) for line in entry.lines}
