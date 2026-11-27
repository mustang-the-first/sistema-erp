"""Stock JSON API (/api/v1): movements (read, everyone) and adjustments (write, admin).

Owner: agent B · Catalog & stock (docs/plan.md).
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db import DbSession
from app.core.deps import CurrentUser, require_roles
from app.models.enums import AdjustmentReason, StockMovementKind
from app.models.users import User
from app.repositories.stock import MovementFilters
from app.schemas.catalog import Page
from app.schemas.stock import AdjustmentCreate, AdjustmentOut, MovementOut
from app.services import stock
from app.services.stock import Adjustment

router = APIRouter(tags=["stock"])

AdminOnly = Annotated[User, Depends(require_roles())]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
DateFrom = Annotated[dt.date | None, Query(alias="from")]
DateTo = Annotated[dt.date | None, Query(alias="to")]


def _adjustment_out(adjustment: Adjustment) -> AdjustmentOut:
    return AdjustmentOut(
        movement=MovementOut.from_movement(adjustment.movement),
        journal_entry_number=adjustment.journal_entry_number,
    )


@router.get("/stock/movements", response_model=Page[MovementOut])
def list_movements(
    db: DbSession,
    _: CurrentUser,
    product_id: int | None = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    kind: StockMovementKind | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[MovementOut]:
    filters = MovementFilters(
        product_id=product_id, date_from=date_from, date_to=date_to, kind=kind
    )
    items, total = stock.list_movements(db, filters, limit=limit, offset=offset)
    return Page(items=[MovementOut.from_movement(m) for m in items], total=total)


@router.get("/stock/adjustments", response_model=Page[AdjustmentOut])
def list_adjustments(
    db: DbSession,
    _: CurrentUser,
    product_id: int | None = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    reason: AdjustmentReason | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[AdjustmentOut]:
    filters = MovementFilters(
        product_id=product_id, date_from=date_from, date_to=date_to, reason=reason
    )
    items, total = stock.list_adjustments(db, filters, limit=limit, offset=offset)
    return Page(items=[_adjustment_out(a) for a in items], total=total)


@router.post("/stock/adjustments", response_model=AdjustmentOut, status_code=201)
def create_adjustment(body: AdjustmentCreate, db: DbSession, user: AdminOnly) -> AdjustmentOut:
    return _adjustment_out(stock.create_adjustment(db, body, created_by=user.id))
