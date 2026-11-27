"""Collections HTML views (Jinja2 + HTMX).

Owner: agent E · Collections (docs/plan.md). Pages: /receipts/new (customer -> open invoices ->
allocation form -> hx-post), /receipts/{id} and /customers/{id}/statement.
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from app.core.clock import today
from app.core.db import DbSession
from app.core.deps import CurrentUser, require_roles
from app.core.errors import BusinessRuleViolation, DomainError
from app.core.templating import templates
from app.models.enums import PaymentMethod, UserRole
from app.schemas.collections import parse_receipt_form
from app.services import collections as service
from app.services.collections import AllocationRequest

router = APIRouter(
    tags=["collections"],
    dependencies=[Depends(require_roles(UserRole.SELLER, UserRole.ACCOUNTANT))],
)

METHOD_LABELS = {PaymentMethod.CASH: "Efectivo", PaymentMethod.BANK_TRANSFER: "Transferencia"}


def _error(request: Request, detail: str) -> HTMLResponse:
    """Domain error for an HTMX form: swap the message into #flash (2xx so HTMX swaps it)."""
    return templates.TemplateResponse(
        request,
        "partials/error.html",
        {"detail": detail},
        headers={"HX-Retarget": "#flash", "HX-Reswap": "innerHTML"},
    )


@router.get("/receipts/new", response_class=HTMLResponse)
def new_receipt(request: Request, db: DbSession, customer_id: int | None = None) -> HTMLResponse:
    page = service.open_invoices(db, customer_id) if customer_id is not None else None
    return templates.TemplateResponse(
        request,
        "collections/new.html",
        {
            "customers": service.active_customers(db),
            "selected_customer_id": customer_id,
            "page": page,
            "today": today(),
            "methods": METHOD_LABELS,
        },
    )


@router.get("/receipts/new/open-invoices", response_class=HTMLResponse)
def open_invoices_partial(
    request: Request, db: DbSession, customer_id: Annotated[str, Query()] = ""
) -> HTMLResponse:
    page = service.open_invoices(db, int(customer_id)) if customer_id.isdigit() else None
    return templates.TemplateResponse(
        request,
        "collections/_allocation_form.html",
        {"page": page, "today": today(), "methods": METHOD_LABELS},
    )


@router.post("/receipts", response_class=HTMLResponse)
async def create_receipt(request: Request, db: DbSession, user: CurrentUser) -> Response:
    form = await request.form()
    try:
        body = parse_receipt_form({k: v for k, v in form.items() if isinstance(v, str)})
    except ValidationError as exc:
        return _error(
            request,
            "Datos inválidos: "
            + "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
            ),
        )
    try:
        # Savepoint: a rejected receipt leaves nothing behind before the request commits.
        with db.begin_nested():
            receipt = service.create_receipt(
                db,
                customer_id=body.customer_id,
                receipt_date=body.date,
                method=body.method,
                reference=body.reference,
                allocations=[AllocationRequest(a.invoice_id, a.amount) for a in body.allocations],
                created_by=user.id,
            )
    except DomainError as exc:
        return _error(request, exc.message)
    return Response(
        status_code=status.HTTP_200_OK, headers={"HX-Redirect": f"/receipts/{receipt.id}"}
    )


@router.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def receipt_detail(request: Request, receipt_id: int, db: DbSession) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "collections/detail.html",
        {"receipt": service.get_receipt(db, receipt_id), "methods": METHOD_LABELS},
    )


@router.get("/customers/{customer_id}/statement", response_class=HTMLResponse)
def customer_statement(
    request: Request,
    customer_id: int,
    db: DbSession,
    date_from: Annotated[str, Query(alias="from")] = "",
    date_to: Annotated[str, Query(alias="to")] = "",
) -> HTMLResponse:
    statement = service.customer_statement(
        db, customer_id, date_from=_parse_date(date_from), date_to=_parse_date(date_to)
    )
    return templates.TemplateResponse(
        request, "collections/statement.html", {"statement": statement}
    )


def _parse_date(value: str) -> dt.date | None:
    """Empty filter inputs from a plain GET form mean "no filter"."""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise BusinessRuleViolation(f"Invalid date: {value}", code="invalid_date") from exc
