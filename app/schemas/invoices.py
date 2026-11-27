import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import InvoiceLetter, TaxCondition, VatRate
from app.services.invoices import InvoiceStatus, InvoiceView


class InvoiceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date | None = None


class InvoiceLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    vat_rate: VatRate
    net_amount: Decimal
    vat_amount: Decimal


class InvoiceSummary(BaseModel):
    id: int
    letter: InvoiceLetter
    point_of_sale: int
    number: int
    formatted_number: str
    customer_id: int
    customer_name: str
    delivery_note_id: int
    date: dt.date
    due_date: dt.date
    net_total: Decimal
    vat_21: Decimal
    vat_10_5: Decimal
    total: Decimal
    balance: Decimal
    status: InvoiceStatus

    @classmethod
    def from_view(cls, view: InvoiceView) -> "InvoiceSummary":
        return cls(**_summary_fields(view))


class InvoiceDetail(InvoiceSummary):
    customer_cuit: str | None
    customer_tax_condition: TaxCondition
    created_by: int
    lines: list[InvoiceLineOut]
    legend: str | None

    @classmethod
    def from_view(cls, view: InvoiceView) -> "InvoiceDetail":
        inv = view.invoice
        return cls(
            **_summary_fields(view),
            customer_cuit=inv.customer_cuit,
            customer_tax_condition=inv.customer_tax_condition,
            created_by=inv.created_by,
            lines=[InvoiceLineOut.model_validate(line) for line in inv.lines],
            legend=view.legend,
        )


class InvoiceList(BaseModel):
    items: list[InvoiceSummary]
    total: int


def _summary_fields(view: InvoiceView) -> dict[str, object]:
    inv = view.invoice
    return {
        "id": inv.id,
        "letter": inv.letter,
        "point_of_sale": inv.point_of_sale,
        "number": inv.number,
        "formatted_number": view.formatted_number,
        "customer_id": inv.customer_id,
        "customer_name": inv.customer_name,
        "delivery_note_id": inv.delivery_note_id,
        "date": inv.date,
        "due_date": inv.due_date,
        "net_total": inv.net_total,
        "vat_21": inv.vat_21,
        "vat_10_5": inv.vat_10_5,
        "total": inv.total,
        "balance": view.balance,
        "status": view.status,
    }
