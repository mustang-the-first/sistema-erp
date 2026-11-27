import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import JournalEntry
from app.models.catalog import Customer, Product
from app.models.enums import JournalSourceType, TaxCondition, VatRate
from app.models.sales import DeliveryNote
from tests.factories import make_customer, make_delivery_note, make_order, make_product

D = Decimal
NOTE_DATE = dt.date(2026, 11, 2)


def delivered_note(
    db: Session,
    *,
    customer: Customer | None = None,
    tax_condition: TaxCondition = TaxCondition.RESPONSABLE_INSCRIPTO,
    lines: list[tuple[Product, Decimal]] | None = None,
    note_date: dt.date = NOTE_DATE,
) -> DeliveryNote:
    customer = customer or make_customer(db, tax_condition=tax_condition)
    if lines is None:
        lines = [(make_product(db, unit_price=D("100.00"), vat_rate=VatRate.VAT_21), D("1"))]
    order = make_order(db, customer=customer, lines=lines, order_date=note_date)
    return make_delivery_note(db, order=order, note_date=note_date)


def mixed_vat_lines(db: Session) -> list[tuple[Product, Decimal]]:
    """Plan example: net 1000 @ 21% + 500 @ 10.5% -> total 1762.50."""
    p21 = make_product(db, name="Tornillo", unit_price=D("100.00"), vat_rate=VatRate.VAT_21)
    p105 = make_product(db, name="Harina", unit_price=D("50.00"), vat_rate=VatRate.VAT_10_5)
    return [(p21, D("10")), (p105, D("10"))]


def entry_lines_by_code(db: Session, invoice_id: int) -> tuple[JournalEntry, dict[str, tuple]]:
    entry = db.scalars(
        select(JournalEntry).where(
            JournalEntry.source_type == JournalSourceType.INVOICE,
            JournalEntry.source_id == invoice_id,
        )
    ).one()
    return entry, {line.account.code: (line.debit, line.credit) for line in entry.lines}
