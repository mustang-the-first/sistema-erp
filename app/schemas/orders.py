"""Order API schemas. Owner: agent D1 · Orders & delivery notes.

Business rules (positive quantities, three decimals, active customer/products, ...) are enforced
by ``app/services/orders.py`` so every rejection has the same ``{"detail", "code"}`` shape.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.core.money import CENT, MILLI
from app.models.enums import OrderStatus, VatRate
from app.models.sales import Order
from app.services.orders import OrderTotals, OrderView

# Output types: fixed scale whatever the in-memory Decimal looks like ("2" -> "2.000").
QuantityOut = Annotated[Decimal, PlainSerializer(lambda v: str(v.quantize(MILLI)), return_type=str)]
MoneyOut = Annotated[Decimal, PlainSerializer(lambda v: str(v.quantize(CENT)), return_type=str)]


class OrderLineIn(BaseModel):
    product_id: int
    quantity: Decimal


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int
    date: dt.date | None = None
    notes: str | None = None
    lines: list[OrderLineIn]


class OrderUpdate(BaseModel):
    """Partial update: omitted fields are left as they are; ``lines`` replaces all lines."""

    model_config = ConfigDict(extra="forbid")

    notes: str | None = None
    lines: list[OrderLineIn] | None = None


class TotalsOut(BaseModel):
    net_total: MoneyOut
    vat_21: MoneyOut
    vat_10_5: MoneyOut
    total: MoneyOut

    @classmethod
    def from_totals(cls, totals: OrderTotals) -> "TotalsOut":
        return cls(
            net_total=totals.net_total,
            vat_21=totals.vat_21,
            vat_10_5=totals.vat_10_5,
            total=totals.total,
        )


class OrderLineOut(BaseModel):
    id: int
    product_id: int
    product_sku: str
    product_name: str
    quantity: QuantityOut
    unit_price: MoneyOut
    vat_rate: VatRate
    net_amount: MoneyOut
    vat_amount: MoneyOut
    delivered_quantity: QuantityOut
    pending_quantity: QuantityOut


class OrderDeliveryNoteRef(BaseModel):
    id: int
    point_of_sale: int
    number: int
    date: dt.date


class OrderSummaryOut(BaseModel):
    id: int
    number: int
    customer_id: int
    customer_name: str
    date: dt.date
    status: OrderStatus
    totals: TotalsOut

    @classmethod
    def from_order(cls, order: Order, totals: OrderTotals) -> "OrderSummaryOut":
        return cls(
            id=order.id,
            number=order.number,
            customer_id=order.customer_id,
            customer_name=order.customer.name,
            date=order.date,
            status=order.status,
            totals=TotalsOut.from_totals(totals),
        )


class OrderOut(OrderSummaryOut):
    notes: str | None
    created_by: int
    created_at: dt.datetime
    lines: list[OrderLineOut]
    delivery_notes: list[OrderDeliveryNoteRef]

    @classmethod
    def from_view(cls, view: OrderView) -> "OrderOut":
        order = view.order
        return cls(
            **OrderSummaryOut.from_order(order, view.totals).model_dump(),
            notes=order.notes,
            created_by=order.created_by,
            created_at=order.created_at,
            lines=[
                OrderLineOut(
                    id=lv.line.id,
                    product_id=lv.line.product_id,
                    product_sku=lv.line.product.sku,
                    product_name=lv.line.product.name,
                    quantity=lv.line.quantity,
                    unit_price=lv.line.unit_price,
                    vat_rate=lv.line.vat_rate,
                    net_amount=lv.net_amount,
                    vat_amount=lv.vat_amount,
                    delivered_quantity=lv.delivered_quantity,
                    pending_quantity=lv.pending_quantity,
                )
                for lv in view.lines
            ],
            delivery_notes=[
                OrderDeliveryNoteRef(
                    id=note.id, point_of_sale=note.point_of_sale, number=note.number, date=note.date
                )
                for note in sorted(order.delivery_notes, key=lambda n: n.id)
            ],
        )


class OrderPage(BaseModel):
    items: list[OrderSummaryOut]
    total: int
