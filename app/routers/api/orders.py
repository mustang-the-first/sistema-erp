"""Orders JSON API (/api/v1).

Owner: agent D1 · Orders & delivery notes (docs/plan.md). Roles: seller (admin always passes).
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.db import DbSession
from app.core.deps import require_roles
from app.models.enums import OrderStatus, UserRole
from app.models.users import User
from app.schemas.delivery_notes import DeliveryNoteCreate, DeliveryNoteOut
from app.schemas.orders import OrderCreate, OrderOut, OrderPage, OrderSummaryOut, OrderUpdate
from app.services import delivery_notes as delivery_service
from app.services import orders as order_service

router = APIRouter(tags=["orders"])

Seller = Annotated[User, Depends(require_roles(UserRole.SELLER))]


@router.get("/orders", response_model=OrderPage)
def list_orders(
    db: DbSession,
    _: Seller,
    status_: Annotated[OrderStatus | None, Query(alias="status")] = None,
    customer_id: int | None = None,
    date_from: Annotated[dt.date | None, Query(alias="from")] = None,
    date_to: Annotated[dt.date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderPage:
    items, total = order_service.list_orders(
        db,
        status=status_,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return OrderPage(
        items=[OrderSummaryOut.from_order(order, totals) for order, totals in items], total=total
    )


@router.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(body: OrderCreate, db: DbSession, user: Seller) -> OrderOut:
    view = order_service.create_order(
        db,
        customer_id=body.customer_id,
        order_date=body.date,
        notes=body.notes,
        lines=[order_service.OrderLineInput(ln.product_id, ln.quantity) for ln in body.lines],
        created_by=user.id,
    )
    return OrderOut.from_view(view)


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: DbSession, _: Seller) -> OrderOut:
    return OrderOut.from_view(order_service.get_order(db, order_id))


@router.patch("/orders/{order_id}", response_model=OrderOut)
def update_order(order_id: int, body: OrderUpdate, db: DbSession, _: Seller) -> OrderOut:
    sent = body.model_fields_set
    view = order_service.update_order(
        db,
        order_id,
        notes=body.notes if "notes" in sent else order_service.UNSET,
        lines=(
            [order_service.OrderLineInput(ln.product_id, ln.quantity) for ln in body.lines]
            if body.lines is not None
            else None
        ),
    )
    return OrderOut.from_view(view)


@router.post("/orders/{order_id}/confirm", response_model=OrderOut)
def confirm_order(order_id: int, db: DbSession, _: Seller) -> OrderOut:
    return OrderOut.from_view(order_service.confirm_order(db, order_id))


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, db: DbSession, _: Seller) -> OrderOut:
    return OrderOut.from_view(order_service.cancel_order(db, order_id))


@router.post(
    "/orders/{order_id}/delivery-notes",
    response_model=DeliveryNoteOut,
    status_code=status.HTTP_201_CREATED,
)
def create_delivery_note(
    order_id: int, body: DeliveryNoteCreate, db: DbSession, user: Seller
) -> DeliveryNoteOut:
    note = delivery_service.create_delivery_note(
        db,
        order_id,
        note_date=body.date,
        lines=[
            delivery_service.DeliveryLineInput(ln.order_line_id, ln.quantity) for ln in body.lines
        ],
        created_by=user.id,
    )
    return DeliveryNoteOut.from_note(note)
