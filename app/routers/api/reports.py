"""Reports JSON API (/api/v1). Read-only.

Owner: agent F · Reports (docs/plan.md).
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db import DbSession
from app.core.deps import require_roles
from app.models.enums import UserRole
from app.schemas.reports import LowStockReport, ReceivablesReport, SalesGroupBy, SalesReport
from app.services import reports as service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get(
    "/sales",
    response_model=SalesReport,
    dependencies=[Depends(require_roles(UserRole.SELLER, UserRole.ACCOUNTANT))],
)
def sales(
    db: DbSession,
    date_from: Annotated[dt.date | None, Query(alias="from")] = None,
    date_to: Annotated[dt.date | None, Query(alias="to")] = None,
    group_by: SalesGroupBy = SalesGroupBy.DAY,
) -> SalesReport:
    return service.sales_report(db, date_from=date_from, date_to=date_to, group_by=group_by)


@router.get(
    "/low-stock",
    response_model=LowStockReport,
    dependencies=[Depends(require_roles(UserRole.SELLER))],
)
def low_stock(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LowStockReport:
    return service.low_stock_report(db, limit=limit, offset=offset)


@router.get(
    "/receivables",
    response_model=ReceivablesReport,
    dependencies=[Depends(require_roles(UserRole.ACCOUNTANT))],
)
def receivables(db: DbSession, as_of: dt.date | None = None) -> ReceivablesReport:
    return service.receivables_report(db, as_of=as_of)
