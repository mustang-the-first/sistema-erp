"""Pydantic schemas for stock movements and adjustments."""

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.money import round_money
from app.models.enums import AdjustmentReason, StockMovementKind
from app.models.stock import StockMovement


class AdjustmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_id: int
    quantity: Decimal  # signed, non-zero: > 0 in, < 0 out
    reason: AdjustmentReason
    note: str | None = Field(default=None, max_length=300)
    date: dt.date | None = None  # defaults to today (business calendar)


class MovementOut(BaseModel):
    id: int
    product_id: int
    product_sku: str
    product_name: str
    date: dt.date
    quantity: Decimal
    kind: StockMovementKind
    reason: AdjustmentReason | None
    unit_cost: Decimal
    value: Decimal  # signed quantity x snapshot unit_cost
    delivery_note_line_id: int | None
    note: str | None
    created_by: int
    created_at: dt.datetime

    @classmethod
    def from_movement(cls, movement: StockMovement) -> "MovementOut":
        return cls(
            id=movement.id,
            product_id=movement.product_id,
            product_sku=movement.product.sku,
            product_name=movement.product.name,
            date=movement.date,
            quantity=movement.quantity,
            kind=movement.kind,
            reason=movement.reason,
            unit_cost=movement.unit_cost,
            value=round_money(movement.quantity * movement.unit_cost),
            delivery_note_line_id=movement.delivery_note_line_id,
            note=movement.note,
            created_by=movement.created_by,
            created_at=movement.created_at,
        )


class AdjustmentOut(BaseModel):
    movement: MovementOut
    journal_entry_number: int | None  # None when the movement has zero value (no entry)
