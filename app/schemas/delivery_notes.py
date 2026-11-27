"""Delivery note API schemas. Owner: agent D1 · Orders & delivery notes."""

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.sales import DeliveryNote
from app.schemas.orders import QuantityOut


class DeliveryLineIn(BaseModel):
    order_line_id: int
    quantity: Decimal


class DeliveryNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date | None = None
    lines: list[DeliveryLineIn]


class DeliveryNoteLineOut(BaseModel):
    id: int
    order_line_id: int
    product_id: int
    product_sku: str
    product_name: str
    quantity: QuantityOut


class DeliveryNoteOut(BaseModel):
    id: int
    point_of_sale: int
    number: int
    order_id: int
    order_number: int
    customer_id: int
    customer_name: str
    date: dt.date
    created_by: int
    created_at: dt.datetime
    invoiced: bool
    invoice_id: int | None
    lines: list[DeliveryNoteLineOut]

    @classmethod
    def from_note(cls, note: DeliveryNote) -> "DeliveryNoteOut":
        return cls(
            id=note.id,
            point_of_sale=note.point_of_sale,
            number=note.number,
            order_id=note.order_id,
            order_number=note.order.number,
            customer_id=note.order.customer_id,
            customer_name=note.order.customer.name,
            date=note.date,
            created_by=note.created_by,
            created_at=note.created_at,
            invoiced=note.invoice is not None,
            invoice_id=note.invoice.id if note.invoice is not None else None,
            lines=[
                DeliveryNoteLineOut(
                    id=line.id,
                    order_line_id=line.order_line_id,
                    product_id=line.product_id,
                    product_sku=line.product.sku,
                    product_name=line.product.name,
                    quantity=line.quantity,
                )
                for line in note.lines
            ],
        )


class DeliveryNotePage(BaseModel):
    items: list[DeliveryNoteOut]
    total: int
