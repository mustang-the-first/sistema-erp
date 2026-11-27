"""Invoices HTML views (Jinja2 + HTMX).

Owner: agent D2 · Invoices (docs/plan.md).

- GET  /invoices                        list with filters (+ delivery notes pending invoicing)
- GET  /invoices/{id}                   printable invoice
- POST /delivery-notes/{id}/invoice     "Facturar" (hx-post) -> HX-Redirect to the invoice
"""

import datetime as dt
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse

from app.core.db import DbSession
from app.core.deps import require_roles
from app.core.errors import BusinessRuleViolation
from app.core.templating import templates
from app.models.enums import InvoiceLetter, UserRole
from app.models.users import User
from app.services import invoices as service
from app.services.invoices import InvoiceStatus, format_number

router = APIRouter(tags=["invoices"])

Issuer = Annotated[User, Depends(require_roles(UserRole.SELLER))]
Reader = Annotated[User, Depends(require_roles(UserRole.SELLER, UserRole.ACCOUNTANT))]

PAGE_SIZE = 50

# Placeholder issuer data for the printable invoice (no ARCA integration).
ISSUER = {
    "name": "Sistema ERP S.A.",
    "cuit": "30-00000000-0",
    "tax_condition": "IVA Responsable Inscripto",
    "address": "Av. Siempreviva 742, CABA",
    "start_date": "01/01/2026",
}

TAX_CONDITION_LABELS = {
    "responsable_inscripto": "IVA Responsable Inscripto",
    "monotributo": "Responsable Monotributo",
    "exento": "IVA Sujeto Exento",
    "consumidor_final": "Consumidor Final",
}


def _parse[T](parser: Callable[[str], T], raw: str | None) -> T | None:
    if not raw:
        return None
    try:
        return parser(raw)
    except ValueError:
        return None


@router.get("/invoices", response_class=HTMLResponse)
def invoice_list(
    request: Request,
    db: DbSession,
    user: Reader,
    customer_id: Annotated[str | None, Query()] = None,
    letter: Annotated[str | None, Query()] = None,
    date_from: Annotated[str | None, Query(alias="from")] = None,
    date_to: Annotated[str | None, Query(alias="to")] = None,
    status: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HTMLResponse:
    # Empty form fields arrive as "" and bad values are ignored: both mean "no filter".
    page = service.list_invoices(
        db,
        customer_id=_parse(int, customer_id),
        letter=_parse(InvoiceLetter, letter),
        date_from=_parse(dt.date.fromisoformat, date_from),
        date_to=_parse(dt.date.fromisoformat, date_to),
        status=_parse(InvoiceStatus, status),
        limit=PAGE_SIZE,
        offset=offset,
    )
    can_issue = user.role in {UserRole.ADMIN, UserRole.SELLER}
    return templates.TemplateResponse(
        request,
        "invoices/list.html",
        {
            "page": page,
            "filters": {
                "customer_id": customer_id or "",
                "letter": letter or "",
                "from": date_from or "",
                "to": date_to or "",
                "status": status or "",
            },
            "customers": service.customer_choices(db),
            "pending_notes": service.uninvoiced_delivery_notes(db) if can_issue else [],
            "format_number": format_number,
            "offset": offset,
            "page_size": PAGE_SIZE,
        },
    )


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int, db: DbSession, _: Reader) -> HTMLResponse:
    view = service.get_invoice(db, invoice_id)
    return templates.TemplateResponse(
        request,
        "invoices/detail.html",
        {"view": view, "issuer": ISSUER, "tax_labels": TAX_CONDITION_LABELS},
    )


@router.post("/delivery-notes/{delivery_note_id}/invoice")
def invoice_delivery_note(
    request: Request,
    delivery_note_id: int,
    db: DbSession,
    user: Issuer,
    date: Annotated[str | None, Form()] = None,
) -> Response:
    invoice_date = _parse(dt.date.fromisoformat, date)
    if date and invoice_date is None:
        raise BusinessRuleViolation("Fecha inválida", code="invalid_date")
    # Errors propagate: the shared handler renders them into #flash and the request rolls back.
    view = service.issue_invoice(
        db, delivery_note_id, created_by=user.id, invoice_date=invoice_date
    )
    return Response(status_code=204, headers={"HX-Redirect": f"/invoices/{view.invoice.id}"})
