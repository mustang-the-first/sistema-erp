"""Reports HTML views (Jinja2 + HTMX). Read-only.

Owner: agent F · Reports (docs/plan.md). Filters are submitted with ``hx-get``; HTMX requests
get only the results partial, plain requests the full page.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.core.db import DbSession
from app.core.deps import require_roles
from app.core.errors import BusinessRuleViolation
from app.core.templating import templates
from app.models.enums import UserRole
from app.models.users import User
from app.schemas.reports import OptionalDate, SalesGroupBy
from app.services import reports as service

router = APIRouter(prefix="/reports", tags=["reports"])

S, C, A = UserRole.SELLER, UserRole.ACCOUNTANT, UserRole.ADMIN
SALES_ROLES = (S, C)
LOW_STOCK_ROLES = (S,)
RECEIVABLES_ROLES = (C,)

# (label, href, roles) for the tabs shown on every report page; admin sees all of them.
REPORT_TABS = (
    ("Ventas", "/reports/sales", SALES_ROLES),
    ("Stock bajo", "/reports/low-stock", LOW_STOCK_ROLES),
    ("Cuentas a cobrar", "/reports/receivables", RECEIVABLES_ROLES),
)
GROUP_BY_LABELS = {
    SalesGroupBy.DAY: "Día",
    SalesGroupBy.MONTH: "Mes",
    SalesGroupBy.CUSTOMER: "Cliente",
    SalesGroupBy.PRODUCT: "Producto",
}


def _tabs(user: User) -> list[tuple[str, str]]:
    return [
        (label, href) for label, href, roles in REPORT_TABS if user.role is A or user.role in roles
    ]


def _render(
    request: Request, user: User, page: str, partial: str, context: dict[str, Any]
) -> HTMLResponse:
    is_htmx = request.headers.get("HX-Request") == "true"
    template = partial if is_htmx else page
    return templates.TemplateResponse(
        request, template, {"tabs": _tabs(user), "partial": partial, **context}
    )


@router.get("/sales", response_class=HTMLResponse)
def sales_page(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_roles(*SALES_ROLES))],
    date_from: Annotated[OptionalDate, Query(alias="from")] = None,
    date_to: Annotated[OptionalDate, Query(alias="to")] = None,
    group_by: SalesGroupBy = SalesGroupBy.DAY,
) -> HTMLResponse:
    context: dict[str, Any] = {
        "group_by": group_by,
        "group_by_labels": GROUP_BY_LABELS,
        "date_from": date_from,
        "date_to": date_to,
        "report": None,
        "error": None,
    }
    try:
        report = service.sales_report(db, date_from=date_from, date_to=date_to, group_by=group_by)
    except BusinessRuleViolation as exc:
        context["error"] = exc.message
    else:
        context.update(report=report, date_from=report.date_from, date_to=report.date_to)
    return _render(request, user, "reports/sales.html", "reports/_sales_table.html", context)


@router.get("/low-stock", response_class=HTMLResponse)
def low_stock_page(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_roles(*LOW_STOCK_ROLES))],
) -> HTMLResponse:
    report = service.low_stock_report(db, limit=service.MAX_PAGE_SIZE)
    return _render(
        request, user, "reports/low_stock.html", "reports/_low_stock_table.html", {"report": report}
    )


@router.get("/receivables", response_class=HTMLResponse)
def receivables_page(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_roles(*RECEIVABLES_ROLES))],
    as_of: OptionalDate = None,
) -> HTMLResponse:
    report = service.receivables_report(db, as_of=as_of)
    return _render(
        request,
        user,
        "reports/receivables.html",
        "reports/_receivables_table.html",
        {"report": report},
    )
