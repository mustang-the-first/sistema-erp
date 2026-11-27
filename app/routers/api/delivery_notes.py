"""Delivery notes JSON API (/api/v1).

Owner: agent D1 · Orders & delivery notes (docs/plan.md). Reads: seller, accountant (admin
always passes). Delivery notes are created from ``POST /orders/{id}/delivery-notes``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db import DbSession
from app.core.deps import require_roles
from app.models.enums import UserRole
from app.models.users import User
from app.schemas.delivery_notes import DeliveryNoteOut, DeliveryNotePage
from app.services import delivery_notes as delivery_service

router = APIRouter(tags=["delivery notes"])

Reader = Annotated[User, Depends(require_roles(UserRole.SELLER, UserRole.ACCOUNTANT))]


@router.get("/delivery-notes", response_model=DeliveryNotePage)
def list_delivery_notes(
    db: DbSession,
    _: Reader,
    order_id: int | None = None,
    invoiced: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DeliveryNotePage:
    notes, total = delivery_service.list_delivery_notes(
        db, order_id=order_id, invoiced=invoiced, limit=limit, offset=offset
    )
    return DeliveryNotePage(items=[DeliveryNoteOut.from_note(n) for n in notes], total=total)


@router.get("/delivery-notes/{note_id}", response_model=DeliveryNoteOut)
def get_delivery_note(note_id: int, db: DbSession, _: Reader) -> DeliveryNoteOut:
    return DeliveryNoteOut.from_note(delivery_service.get_delivery_note(db, note_id))
