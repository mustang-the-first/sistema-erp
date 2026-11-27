"""Pydantic v2 schemas of the collections API. Money is serialized as a decimal string."""

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.models.collections import Receipt
from app.models.enums import PaymentMethod
from app.services.collections import (
    MovementKind,
    OpenInvoicePage,
    ReceiptPage,
    Statement,
    format_document_number,
    format_invoice_number,
)

MoneyOut = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str)]

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


# Input ------------------------------------------------------------------------------------


class AllocationIn(BaseModel):
    invoice_id: int
    amount: Decimal


class ReceiptCreate(BaseModel):
    """Business rules (>= 1 allocation, duplicates, amount > 0 with cents, balances) are
    checked by the service so they come back as 422 with a domain ``code``."""

    model_config = ConfigDict(extra="forbid")

    customer_id: int
    date: dt.date | None = None
    method: PaymentMethod
    reference: str | None = Field(default=None, max_length=100)
    allocations: list[AllocationIn]


AMOUNT_FIELD_PREFIX = "amount_"


def parse_receipt_form(form: Mapping[str, str]) -> ReceiptCreate:
    """HTML allocation form -> ReceiptCreate. One ``amount_<invoice_id>`` input per open
    invoice; blank inputs mean "not paid by this receipt". Raises ``ValidationError``."""
    allocations = [
        {"invoice_id": key.removeprefix(AMOUNT_FIELD_PREFIX), "amount": value.strip()}
        for key, value in form.items()
        if key.startswith(AMOUNT_FIELD_PREFIX) and value.strip()
    ]
    return ReceiptCreate.model_validate(
        {
            "customer_id": form.get("customer_id", ""),
            "date": form.get("date") or None,
            "method": form.get("method", ""),
            "reference": form.get("reference") or None,
            "allocations": allocations,
        }
    )


# Output -----------------------------------------------------------------------------------


class AllocationOut(BaseModel):
    id: int
    invoice_id: int
    invoice_number: str
    invoice_date: dt.date
    invoice_total: MoneyOut
    amount: MoneyOut


class ReceiptSummary(BaseModel):
    id: int
    point_of_sale: int
    number: int
    document_number: str
    customer_id: int
    customer_name: str
    date: dt.date
    method: PaymentMethod
    reference: str | None
    amount: MoneyOut
    created_by: int

    @classmethod
    def from_model(cls, receipt: Receipt) -> "ReceiptSummary":
        return cls(
            id=receipt.id,
            point_of_sale=receipt.point_of_sale,
            number=receipt.number,
            document_number=format_document_number(receipt.point_of_sale, receipt.number),
            customer_id=receipt.customer_id,
            customer_name=receipt.customer.name,
            date=receipt.date,
            method=receipt.method,
            reference=receipt.reference,
            amount=receipt.amount,
            created_by=receipt.created_by,
        )


class ReceiptOut(ReceiptSummary):
    allocations: list[AllocationOut]

    @classmethod
    def from_model(cls, receipt: Receipt) -> "ReceiptOut":
        summary = ReceiptSummary.from_model(receipt)
        return cls(
            **summary.model_dump(),
            allocations=[
                AllocationOut(
                    id=a.id,
                    invoice_id=a.invoice_id,
                    invoice_number=format_invoice_number(a.invoice),
                    invoice_date=a.invoice.date,
                    invoice_total=a.invoice.total,
                    amount=a.amount,
                )
                for a in receipt.allocations
            ],
        )


class ReceiptList(BaseModel):
    items: list[ReceiptSummary]
    total: int

    @classmethod
    def from_page(cls, page: ReceiptPage) -> "ReceiptList":
        return cls(items=[ReceiptSummary.from_model(r) for r in page.items], total=page.total)


class OpenInvoiceOut(BaseModel):
    invoice_id: int
    letter: str
    point_of_sale: int
    number: int
    document_number: str
    date: dt.date
    due_date: dt.date
    total: MoneyOut
    paid: MoneyOut
    balance: MoneyOut
    days_overdue: int


class OpenInvoiceList(BaseModel):
    customer_id: int
    items: list[OpenInvoiceOut]
    total: int

    @classmethod
    def from_page(cls, page: OpenInvoicePage) -> "OpenInvoiceList":
        return cls(
            customer_id=page.customer.id,
            items=[
                OpenInvoiceOut(
                    invoice_id=item.invoice.id,
                    letter=item.invoice.letter,
                    point_of_sale=item.invoice.point_of_sale,
                    number=item.invoice.number,
                    document_number=item.document_number,
                    date=item.invoice.date,
                    due_date=item.invoice.due_date,
                    total=item.invoice.total,
                    paid=item.paid,
                    balance=item.balance,
                    days_overdue=item.days_overdue,
                )
                for item in page.items
            ],
            total=page.total,
        )


class StatementMovementOut(BaseModel):
    date: dt.date
    kind: MovementKind
    document_id: int
    document_number: str
    debit: MoneyOut
    credit: MoneyOut
    balance: MoneyOut


class StatementOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    customer_id: int
    customer_name: str
    date_from: dt.date | None = Field(serialization_alias="from")
    date_to: dt.date | None = Field(serialization_alias="to")
    opening_balance: MoneyOut
    movements: list[StatementMovementOut]
    total_debit: MoneyOut
    total_credit: MoneyOut
    closing_balance: MoneyOut

    @classmethod
    def from_statement(cls, statement: Statement) -> "StatementOut":
        return cls(
            customer_id=statement.customer.id,
            customer_name=statement.customer.name,
            date_from=statement.date_from,
            date_to=statement.date_to,
            opening_balance=statement.opening_balance,
            movements=[
                StatementMovementOut(
                    date=m.date,
                    kind=m.kind,
                    document_id=m.document_id,
                    document_number=m.document_number,
                    debit=m.debit,
                    credit=m.credit,
                    balance=m.balance,
                )
                for m in statement.movements
            ],
            total_debit=statement.total_debit,
            total_credit=statement.total_credit,
            closing_balance=statement.closing_balance,
        )
