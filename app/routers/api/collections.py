"""Collections JSON API (/api/v1): receipts, open invoices and customer statements.

Owner: agent E · Collections (docs/plan.md). Roles: seller, accountant (admin always passes).
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.db import DbSession
from app.core.deps import CurrentUser, require_roles
from app.models.enums import UserRole
from app.schemas.collections import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    OpenInvoiceList,
    ReceiptCreate,
    ReceiptList,
    ReceiptOut,
    StatementOut,
)
from app.services import collections as service
from app.services.collections import AllocationRequest

router = APIRouter(
    tags=["collections"],
    dependencies=[Depends(require_roles(UserRole.SELLER, UserRole.ACCOUNTANT))],
)

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0)]
DateFrom = Annotated[dt.date | None, Query(alias="from")]
DateTo = Annotated[dt.date | None, Query(alias="to")]


@router.post("/receipts", status_code=status.HTTP_201_CREATED)
def create_receipt(body: ReceiptCreate, db: DbSession, user: CurrentUser) -> ReceiptOut:
    receipt = service.create_receipt(
        db,
        customer_id=body.customer_id,
        receipt_date=body.date,
        method=body.method,
        reference=body.reference,
        allocations=[AllocationRequest(a.invoice_id, a.amount) for a in body.allocations],
        created_by=user.id,
    )
    return ReceiptOut.from_model(service.get_receipt(db, receipt.id))


@router.get("/receipts")
def list_receipts(
    db: DbSession,
    customer_id: int | None = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> ReceiptList:
    page = service.list_receipts(
        db,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return ReceiptList.from_page(page)


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: int, db: DbSession) -> ReceiptOut:
    return ReceiptOut.from_model(service.get_receipt(db, receipt_id))


@router.get("/customers/{customer_id}/open-invoices")
def open_invoices(
    customer_id: int, db: DbSession, limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
) -> OpenInvoiceList:
    return OpenInvoiceList.from_page(
        service.open_invoices(db, customer_id, limit=limit, offset=offset)
    )


@router.get("/customers/{customer_id}/statement")
def customer_statement(
    customer_id: int, db: DbSession, date_from: DateFrom = None, date_to: DateTo = None
) -> StatementOut:
    return StatementOut.from_statement(
        service.customer_statement(db, customer_id, date_from=date_from, date_to=date_to)
    )
