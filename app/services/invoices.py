"""Invoices: issue an invoice from a delivery note, list and read them.

- Letter (issuer is Responsable Inscripto): RI / Monotributo -> A (CUIT required);
  Exento / Consumidor Final -> B.
- Amounts: per line with ``compute_line_amounts``; totals are the sum of the lines.
- Every invoice posts its journal entry through ``posting.post_entry``.
- Balance and open/paid status come ONLY from ``app/services/invoice_balance.py``.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.config import get_settings
from app.core.errors import BusinessRuleViolation, NotFound
from app.core.money import ZERO, compute_line_amounts
from app.models.catalog import Customer
from app.models.enums import DocumentType, InvoiceLetter, TaxCondition, VatRate
from app.models.sales import DeliveryNote, Invoice, InvoiceLine
from app.repositories.invoices import (
    CustomerReadRepository,
    DeliveryNoteReadRepository,
    InvoiceFilters,
    InvoiceRepository,
)
from app.services.invoice_balance import invoice_balance
from app.services.posting import build_invoice_entry, post_entry
from app.services.sequences import next_number

# Ley 27.618: mandatory legend on invoices A issued to Monotributo customers.
LEY_27618_LEGEND = (
    "El crédito fiscal discriminado en el presente comprobante, sólo podrá ser computado a "
    "efectos del Régimen de Sostenimiento e Inclusión Fiscal para Pequeños Contribuyentes de la "
    "Ley Nº 27.618"
)

_LETTER_A_CONDITIONS = frozenset({TaxCondition.RESPONSABLE_INSCRIPTO, TaxCondition.MONOTRIBUTO})


class InvoiceStatus(StrEnum):
    OPEN = "open"
    PAID = "paid"


# Pure rules -------------------------------------------------------------------------------


def resolve_letter(tax_condition: TaxCondition, cuit: str | None) -> InvoiceLetter:
    """Invoice letter for a customer, issuer being Responsable Inscripto."""
    if tax_condition in _LETTER_A_CONDITIONS:
        if not cuit:
            raise BusinessRuleViolation(
                "Invoice A requires the customer's CUIT", code="cuit_required"
            )
        return InvoiceLetter.A
    return InvoiceLetter.B


def legend_for(letter: InvoiceLetter, tax_condition: TaxCondition) -> str | None:
    """Ley 27.618 legend: only on invoices A to Monotributo customers."""
    if letter is InvoiceLetter.A and tax_condition is TaxCondition.MONOTRIBUTO:
        return LEY_27618_LEGEND
    return None


def format_number(point_of_sale: int, number: int) -> str:
    return f"{point_of_sale:04d}-{number:08d}"


def status_for(balance: Decimal) -> InvoiceStatus:
    return InvoiceStatus.OPEN if balance > 0 else InvoiceStatus.PAID


@dataclass(frozen=True, slots=True)
class LineInput:
    product_id: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: VatRate


@dataclass(frozen=True, slots=True)
class InvoiceTotals:
    net_total: Decimal
    vat_21: Decimal
    vat_10_5: Decimal

    @property
    def total(self) -> Decimal:
        return self.net_total + self.vat_21 + self.vat_10_5


def build_lines(inputs: Iterable[LineInput]) -> tuple[list[InvoiceLine], InvoiceTotals]:
    """Invoice lines with per-line net/VAT, and totals as the sum of the lines."""
    lines: list[InvoiceLine] = []
    net_total = ZERO
    vat = {VatRate.VAT_21: ZERO, VatRate.VAT_10_5: ZERO}
    for item in inputs:
        amounts = compute_line_amounts(item.quantity, item.unit_price, item.vat_rate.fraction)
        net_total += amounts.net
        vat[item.vat_rate] += amounts.vat
        lines.append(
            InvoiceLine(
                product_id=item.product_id,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                vat_rate=item.vat_rate,
                net_amount=amounts.net,
                vat_amount=amounts.vat,
            )
        )
    return lines, InvoiceTotals(net_total, vat[VatRate.VAT_21], vat[VatRate.VAT_10_5])


# Views ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InvoiceView:
    invoice: Invoice
    balance: Decimal

    @property
    def status(self) -> InvoiceStatus:
        return status_for(self.balance)

    @property
    def formatted_number(self) -> str:
        return format_number(self.invoice.point_of_sale, self.invoice.number)

    @property
    def legend(self) -> str | None:
        return legend_for(self.invoice.letter, self.invoice.customer_tax_condition)


@dataclass(frozen=True, slots=True)
class InvoicePage:
    items: list[InvoiceView]
    total: int


# Use cases --------------------------------------------------------------------------------


def _line_inputs(note: DeliveryNote) -> list[LineInput]:
    return [
        LineInput(
            product_id=line.product_id,
            description=line.product.name,
            quantity=line.quantity,
            unit_price=line.order_line.unit_price,  # order snapshot, not the current price
            vat_rate=line.order_line.vat_rate,
        )
        for line in note.lines
    ]


def issue_invoice(
    session: Session,
    delivery_note_id: int,
    *,
    created_by: int,
    invoice_date: dt.date | None = None,
) -> InvoiceView:
    invoices = InvoiceRepository(session)
    # Row lock: two concurrent requests for the same delivery note are serialized here.
    note = DeliveryNoteReadRepository(session).get_for_update(delivery_note_id)
    if invoices.exists_for_delivery_note(note.id):
        raise BusinessRuleViolation(
            f"Delivery note {note.id} is already invoiced", code="already_invoiced"
        )

    issued = invoice_date or today()
    if issued < note.date:
        raise BusinessRuleViolation(
            "Invoice date cannot be earlier than the delivery note date",
            code="invoice_date_before_delivery",
        )

    customer: Customer = note.order.customer
    letter = resolve_letter(customer.tax_condition, customer.cuit)
    lines, totals = build_lines(_line_inputs(note))
    if totals.total <= 0:
        raise BusinessRuleViolation("Invoice total must be positive", code="invoice_total_zero")

    point_of_sale = get_settings().point_of_sale
    invoice = invoices.add(
        Invoice(
            letter=letter,
            point_of_sale=point_of_sale,
            number=next_number(
                session, DocumentType.INVOICE, point_of_sale=point_of_sale, letter=letter
            ),
            customer_id=customer.id,
            delivery_note_id=note.id,
            date=issued,
            due_date=issued + dt.timedelta(days=customer.payment_terms_days),
            customer_name=customer.name,
            customer_cuit=customer.cuit,
            customer_tax_condition=customer.tax_condition,
            net_total=totals.net_total,
            vat_21=totals.vat_21,
            vat_10_5=totals.vat_10_5,
            total=totals.total,
            created_by=created_by,
            lines=lines,
        )
    )
    view = InvoiceView(invoice, invoice_balance(session, invoice.id))
    post_entry(
        session,
        build_invoice_entry(
            invoice_id=invoice.id,
            entry_date=invoice.date,
            description=f"Factura {letter} {view.formatted_number}",
            net_total=invoice.net_total,
            vat_21=invoice.vat_21,
            vat_10_5=invoice.vat_10_5,
            total=invoice.total,
        ),
        created_by=created_by,
    )
    return view


def get_invoice(session: Session, invoice_id: int) -> InvoiceView:
    invoice = InvoiceRepository(session).get_with_lines(invoice_id)
    if invoice is None:
        raise NotFound(f"Invoice {invoice_id} not found")
    return InvoiceView(invoice, invoice_balance(session, invoice.id))


def list_invoices(
    session: Session,
    *,
    customer_id: int | None = None,
    letter: InvoiceLetter | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    status: InvoiceStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> InvoicePage:
    filters = InvoiceFilters(
        customer_id=customer_id,
        letter=letter,
        date_from=date_from,
        date_to=date_to,
        only_open=None if status is None else status is InvoiceStatus.OPEN,
    )
    rows, total = InvoiceRepository(session).search(filters, limit=limit, offset=offset)
    return InvoicePage([InvoiceView(invoice, balance) for invoice, balance in rows], total)


def uninvoiced_delivery_notes(session: Session, *, limit: int = 50) -> list[DeliveryNote]:
    return DeliveryNoteReadRepository(session).uninvoiced(limit=limit)


def customer_choices(session: Session) -> list[Customer]:
    return CustomerReadRepository(session).all_by_name()
