"""Delivery notes HTML views (Jinja2 + HTMX).

Owner: agent D1 · Orders & delivery notes (docs/plan.md). Roles: seller, accountant (admin
always passes). Delivery notes are created from the order detail page.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.db import DbSession
from app.core.deps import require_roles
from app.core.templating import templates
from app.models.enums import UserRole
from app.models.users import User
from app.services import delivery_notes as delivery_service

router = APIRouter(tags=["delivery notes"])

Reader = Annotated[User, Depends(require_roles(UserRole.SELLER, UserRole.ACCOUNTANT))]
PAGE_SIZE = 50


@router.get("/delivery-notes", response_class=HTMLResponse)
def list_page(
    request: Request,
    db: DbSession,
    _: Reader,
    order_id: int | None = None,
    invoiced: bool | None = None,
    page: int = 1,
) -> HTMLResponse:
    page = max(page, 1)
    notes, total = delivery_service.list_delivery_notes(
        db, order_id=order_id, invoiced=invoiced, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    return templates.TemplateResponse(
        request,
        "delivery_notes/list.html",
        {
            "notes": notes,
            "total": total,
            "page": page,
            "pages": max(1, -(-total // PAGE_SIZE)),
            "order_id": order_id,
            "invoiced": invoiced,
            "format_number": delivery_service.format_number,
        },
    )


@router.get("/delivery-notes/{note_id}", response_class=HTMLResponse)
def detail_page(request: Request, note_id: int, db: DbSession, _: Reader) -> HTMLResponse:
    note = delivery_service.get_delivery_note(db, note_id)
    return templates.TemplateResponse(
        request,
        "delivery_notes/detail.html",
        {"note": note, "format_number": delivery_service.format_number},
    )
