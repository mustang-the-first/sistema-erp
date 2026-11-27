"""Invoices JSON API (/api/v1).

Owner: agent D2 · Invoices (docs/plan.md).
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status

from app.core.db import DbSession
from app.core.deps import require_roles
from app.models.enums import InvoiceLetter, UserRole
from app.models.users import User
from app.schemas.invoices import InvoiceDetail, InvoiceIssue, InvoiceList, InvoiceSummary
from app.services import invoices as service
from app.services.invoices import InvoiceStatus

router = APIRouter(tags=["invoices"])

Issuer = Annotated[User, Depends(require_roles(UserRole.SELLER))]
Reader = Annotated[User, Depends(require_roles(UserRole.SELLER, UserRole.ACCOUNTANT))]


@router.post(
    "/delivery-notes/{delivery_note_id}/invoice",
    response_model=InvoiceDetail,
    status_code=http_status.HTTP_201_CREATED,
)
def issue_invoice(
    delivery_note_id: int, db: DbSession, user: Issuer, payload: InvoiceIssue | None = None
) -> InvoiceDetail:
    view = service.issue_invoice(
        db,
        delivery_note_id,
        created_by=user.id,
        invoice_date=payload.date if payload else None,
    )
    return InvoiceDetail.from_view(view)


@router.get("/invoices", response_model=InvoiceList)
def list_invoices(
    db: DbSession,
    _: Reader,
    customer_id: int | None = None,
    letter: InvoiceLetter | None = None,
    date_from: Annotated[dt.date | None, Query(alias="from")] = None,
    date_to: Annotated[dt.date | None, Query(alias="to")] = None,
    status: InvoiceStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InvoiceList:
    page = service.list_invoices(
        db,
        customer_id=customer_id,
        letter=letter,
        date_from=date_from,
        date_to=date_to,
        status=status,
        limit=limit,
        offset=offset,
    )
    return InvoiceList(items=[InvoiceSummary.from_view(v) for v in page.items], total=page.total)


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: int, db: DbSession, _: Reader) -> InvoiceDetail:
    return InvoiceDetail.from_view(service.get_invoice(db, invoice_id))
