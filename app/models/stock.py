import datetime as dt

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt, Money, Quantity, fk, str_enum
from app.models.catalog import Product
from app.models.enums import AdjustmentReason, StockMovementKind


class StockMovement(Base):
    """Stock ledger. Signed quantity; unit_cost is a snapshot used to value the movement."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("quantity <> 0", name="quantity_non_zero"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_non_negative"),
        CheckConstraint(
            "(kind = 'adjustment' AND reason IS NOT NULL AND delivery_note_line_id IS NULL)"
            " OR (kind = 'delivery' AND reason IS NULL AND delivery_note_line_id IS NOT NULL"
            " AND quantity < 0)",
            name="kind_consistency",
        ),
        CheckConstraint(
            "reason IS DISTINCT FROM 'initial_load' OR quantity > 0", name="initial_load_positive"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(fk("products.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    quantity: Mapped[Quantity]
    kind: Mapped[StockMovementKind] = mapped_column(
        str_enum(StockMovementKind, "stock_movement_kind")
    )
    reason: Mapped[AdjustmentReason | None] = mapped_column(
        str_enum(AdjustmentReason, "adjustment_reason")
    )
    unit_cost: Mapped[Money]
    delivery_note_line_id: Mapped[int | None] = mapped_column(
        fk("delivery_note_lines.id"), unique=True
    )
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[int] = mapped_column(fk("users.id"), index=True)
    created_at: Mapped[CreatedAt]

    product: Mapped[Product] = relationship()
