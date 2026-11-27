"""Stock HTML views (Jinja2 + HTMX): /stock (movements + adjustment form).

Owner: agent B · Catalog & stock (docs/plan.md).
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response

from app.core.clock import today
from app.core.db import DbSession
from app.core.deps import CurrentUser, require_roles
from app.core.errors import BusinessRuleViolation
from app.models.enums import AdjustmentReason, StockMovementKind
from app.models.users import User
from app.repositories.stock import MovementFilters
from app.routers.web.catalog import CreateForm, handle_form, parse, render
from app.schemas.stock import AdjustmentCreate
from app.services import catalog, stock
from app.services.stock import REASON_LABELS

# Stock is admin-only (docs/plan.md): enforced for every route of this router.
router = APIRouter(tags=["stock"], dependencies=[Depends(require_roles())])

AdminOnly = Annotated[User, Depends(require_roles())]
PAGE_SIZE = 100
KIND_LABELS = {StockMovementKind.DELIVERY: "Remito", StockMovementKind.ADJUSTMENT: "Ajuste"}


def _optional_int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def _optional_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise BusinessRuleViolation(f"Invalid date: {value}", code="invalid_date") from None


@router.get("/stock", response_class=HTMLResponse)
def stock_page(
    request: Request,
    db: DbSession,
    _: CurrentUser,
    product_id: str | None = None,
    date_from: Annotated[str | None, Query(alias="from")] = None,
    date_to: Annotated[str | None, Query(alias="to")] = None,
    kind: str | None = None,
    partial: bool = False,
) -> Response:
    filters = MovementFilters(
        product_id=_optional_int(product_id),
        date_from=_optional_date(date_from),
        date_to=_optional_date(date_to),
        kind=StockMovementKind(kind) if kind in set(StockMovementKind) else None,
    )
    movements, total = stock.list_movements(db, filters, limit=PAGE_SIZE, offset=0)
    context = {
        "movements": movements,
        "total": total,
        "filters": filters,
        "kind_labels": KIND_LABELS,
        "reason_labels": REASON_LABELS,
    }
    if partial:
        return render(request, "stock/_movements_table.html", context)
    products, _total = catalog.list_products(db, q=None, active=None, limit=1000, offset=0)
    context |= {"products": products, "reasons": list(AdjustmentReason), "today": today()}
    return render(request, "stock/index.html", context)


@router.post("/stock/adjustments", response_class=HTMLResponse)
def create_adjustment(
    request: Request, data: CreateForm, db: DbSession, user: AdminOnly
) -> Response:
    def action() -> str:
        result = stock.create_adjustment(
            db, parse(AdjustmentCreate, data, drop_none=True), created_by=user.id
        )
        entry = result.journal_entry_number
        suffix = f" (asiento N° {entry})" if entry is not None else " (sin asiento: costo cero)"
        return f"Ajuste de {result.movement.product.sku} registrado{suffix}"

    return handle_form(request, db, action, event="stock-changed")
