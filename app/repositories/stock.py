"""Read queries over the stock ledger. Writes go through app/services/stock_ledger.py only."""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session, joinedload

from app.models.accounting import JournalEntry
from app.models.enums import AdjustmentReason, JournalSourceType, StockMovementKind
from app.models.stock import StockMovement


@dataclass(frozen=True, slots=True)
class MovementFilters:
    product_id: int | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    kind: StockMovementKind | None = None
    reason: AdjustmentReason | None = None


def _conditions(filters: MovementFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if filters.product_id is not None:
        conditions.append(StockMovement.product_id == filters.product_id)
    if filters.date_from is not None:
        conditions.append(StockMovement.date >= filters.date_from)
    if filters.date_to is not None:
        conditions.append(StockMovement.date <= filters.date_to)
    if filters.kind is not None:
        conditions.append(StockMovement.kind == filters.kind)
    if filters.reason is not None:
        conditions.append(StockMovement.reason == filters.reason)
    return conditions


_ADJUSTMENT_ENTRY = and_(
    JournalEntry.source_type == JournalSourceType.STOCK_ADJUSTMENT,
    JournalEntry.source_id == StockMovement.id,
)


class StockRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def count_movements(self, filters: MovementFilters) -> int:
        stmt = select(func.count()).select_from(StockMovement).where(*_conditions(filters))
        return self.session.scalar(stmt) or 0

    def list_movements(
        self, filters: MovementFilters, *, limit: int, offset: int
    ) -> list[StockMovement]:
        stmt = (
            select(StockMovement)
            .options(joinedload(StockMovement.product))
            .where(*_conditions(filters))
            .order_by(StockMovement.date.desc(), StockMovement.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt))

    def list_adjustments(
        self, filters: MovementFilters, *, limit: int, offset: int
    ) -> list[tuple[StockMovement, int | None]]:
        """Adjustment movements with the number of their journal entry (None: zero value)."""
        stmt = (
            select(StockMovement, JournalEntry.number)
            .outerjoin(JournalEntry, _ADJUSTMENT_ENTRY)
            .options(joinedload(StockMovement.product))
            .where(*_conditions(filters))
            .order_by(StockMovement.date.desc(), StockMovement.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [(movement, number) for movement, number in self.session.execute(stmt).all()]
