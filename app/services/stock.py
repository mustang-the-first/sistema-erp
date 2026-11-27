"""Stock: movement queries and manual adjustments with their journal entry.

An adjustment is one ``stock_ledger`` movement (kind ``adjustment``) plus, when it has value,
one journal entry built by ``posting.build_adjustment_entry``:

- initial_load (+):               D Mercaderías         / C Capital
- count_difference (+):           D Mercaderías         / C Sobrantes de inventario
- count_difference / damage (-):  D Faltantes y roturas / C Mercaderías

valued at |quantity| x the unit cost snapshotted on the movement. Products with zero cost move
stock without an entry.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.errors import BusinessRuleViolation
from app.models.accounting import JournalEntry
from app.models.enums import AdjustmentReason, StockMovementKind
from app.models.stock import StockMovement
from app.repositories.stock import MovementFilters, StockRepository
from app.schemas.stock import AdjustmentCreate
from app.services.posting import build_adjustment_entry, post_entry
from app.services.stock_ledger import MovementDraft, apply_movements, movement_value

REASON_LABELS = {
    AdjustmentReason.INITIAL_LOAD: "carga inicial",
    AdjustmentReason.COUNT_DIFFERENCE: "diferencia de conteo",
    AdjustmentReason.DAMAGE: "rotura",
}


@dataclass(frozen=True, slots=True)
class Adjustment:
    movement: StockMovement
    journal_entry_number: int | None


def _check_range(filters: MovementFilters) -> None:
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise BusinessRuleViolation("'from' must not be after 'to'", code="invalid_date_range")


def list_movements(
    session: Session, filters: MovementFilters, *, limit: int, offset: int
) -> tuple[list[StockMovement], int]:
    _check_range(filters)
    repo = StockRepository(session)
    return repo.list_movements(filters, limit=limit, offset=offset), repo.count_movements(filters)


def list_adjustments(
    session: Session, filters: MovementFilters, *, limit: int, offset: int
) -> tuple[list[Adjustment], int]:
    _check_range(filters)
    only_adjustments = MovementFilters(
        product_id=filters.product_id,
        date_from=filters.date_from,
        date_to=filters.date_to,
        kind=StockMovementKind.ADJUSTMENT,
        reason=filters.reason,
    )
    repo = StockRepository(session)
    rows = repo.list_adjustments(only_adjustments, limit=limit, offset=offset)
    items = [Adjustment(movement, number) for movement, number in rows]
    return items, repo.count_movements(only_adjustments)


def entry_description(reason: AdjustmentReason, sku: str, quantity: Decimal) -> str:
    return f"Ajuste de stock ({REASON_LABELS[reason]}) {sku} {quantity:+}"


def create_adjustment(session: Session, data: AdjustmentCreate, *, created_by: int) -> Adjustment:
    """Move the stock and post the entry in the caller's transaction (flush, no commit)."""
    movement_date: dt.date = data.date or today()
    [movement] = apply_movements(
        session,
        [
            MovementDraft(
                product_id=data.product_id,
                quantity=data.quantity,
                kind=StockMovementKind.ADJUSTMENT,
                reason=data.reason,
                note=data.note or None,
            )
        ],
        movement_date=movement_date,
        created_by=created_by,
    )
    draft = build_adjustment_entry(
        movement_id=movement.id,
        entry_date=movement_date,
        description=entry_description(data.reason, movement.product.sku, movement.quantity),
        reason=data.reason,
        quantity=movement.quantity,
        value=movement_value(movement),
    )
    entry: JournalEntry | None = None
    if draft is not None:
        entry = post_entry(session, draft, created_by=created_by)
    session.refresh(movement)  # stored values (Numeric scale, created_at)
    return Adjustment(movement, entry.number if entry else None)
