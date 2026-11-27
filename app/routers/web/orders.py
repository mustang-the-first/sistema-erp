"""Orders HTML views (Jinja2 + HTMX).

Owner: agent D1 · Orders & delivery notes (docs/plan.md). Roles: seller (admin always passes).

Writes go through ``hx-post``/``hx-patch`` (CSRF header). On success the response carries
``HX-Redirect``; domain errors propagate and the shared handler in ``app/core/errors.py``
renders them into ``#flash`` (and the request transaction is rolled back).
Services are atomic: a rejected command leaves nothing behind.
"""

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response

from app.core.db import DbSession
from app.core.deps import require_roles
from app.core.errors import BusinessRuleViolation
from app.core.templating import templates
from app.models.enums import OrderStatus, UserRole
from app.models.users import User
from app.services import delivery_notes as delivery_service
from app.services import orders as order_service

router = APIRouter(tags=["orders"])

Seller = Annotated[User, Depends(require_roles(UserRole.SELLER))]
FormList = Annotated[list[str], Form(default_factory=list)]
PAGE_SIZE = 50


# Form parsing -----------------------------------------------------------------------------


def _parse_date(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise BusinessRuleViolation(f"Invalid date {raw!r}", code="bad_date") from exc


def _parse_pairs(ids: list[str], quantities: list[str]) -> list[tuple[int, Decimal]]:
    """Rows of (id, quantity) from parallel form lists; rows with a blank quantity are skipped."""
    if len(ids) != len(quantities):
        raise BusinessRuleViolation("Malformed form", code="bad_form")
    pairs: list[tuple[int, Decimal]] = []
    for raw_id, raw_qty in zip(ids, quantities, strict=True):
        text = raw_qty.strip().replace(",", ".")
        if not text:
            continue
        try:
            pairs.append((int(raw_id), Decimal(text)))
        except (ValueError, InvalidOperation) as exc:
            raise BusinessRuleViolation(
                f"Invalid quantity {raw_qty!r}", code="bad_quantity"
            ) from exc
    return pairs


def _redirect(url: str) -> Response:
    return Response(status_code=204, headers={"HX-Redirect": url})


# Pages ------------------------------------------------------------------------------------


@router.get("/orders", response_class=HTMLResponse)
def list_page(
    request: Request,
    db: DbSession,
    _: Seller,
    status: OrderStatus | None = None,
    page: int = 1,
) -> HTMLResponse:
    page = max(page, 1)
    items, total = order_service.list_orders(
        db, status=status, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    return templates.TemplateResponse(
        request,
        "orders/list.html",
        {
            "items": items,
            "total": total,
            "page": page,
            "pages": max(1, -(-total // PAGE_SIZE)),
            "status": status,
            "statuses": list(OrderStatus),
        },
    )


@router.get("/orders/new", response_class=HTMLResponse)
def new_page(request: Request, db: DbSession, _: Seller) -> HTMLResponse:
    customers, products = order_service.form_options(db)
    return templates.TemplateResponse(
        request,
        "orders/new.html",
        {"customers": customers, "products": products, "quantities": {}},
    )


@router.get("/orders/{order_id}", response_class=HTMLResponse)
def detail_page(request: Request, order_id: int, db: DbSession, _: Seller) -> HTMLResponse:
    view = order_service.get_order(db, order_id)
    products = []
    if view.order.status is OrderStatus.DRAFT:
        _, products = order_service.form_options(db)
    return templates.TemplateResponse(
        request,
        "orders/detail.html",
        {
            "view": view,
            "order": view.order,
            "products": products,
            "quantities": {lv.line.product_id: lv.line.quantity for lv in view.lines},
            "deliverable": view.order.status in order_service.DELIVERABLE_STATUSES
            and view.has_pending,
        },
    )


# Commands ---------------------------------------------------------------------------------


@router.post("/orders")
def create(
    request: Request,
    db: DbSession,
    user: Seller,
    customer_id: Annotated[int, Form()],
    product_id: FormList,
    quantity: FormList,
    date: Annotated[str | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
) -> Response:
    view = order_service.create_order(
        db,
        customer_id=customer_id,
        order_date=_parse_date(date),
        notes=notes or None,
        lines=[
            order_service.OrderLineInput(pid, qty)
            for pid, qty in _parse_pairs(product_id, quantity)
        ],
        created_by=user.id,
    )
    return _redirect(f"/orders/{view.order.id}")


@router.patch("/orders/{order_id}")
def update(
    request: Request,
    order_id: int,
    db: DbSession,
    _: Seller,
    product_id: FormList,
    quantity: FormList,
    notes: Annotated[str | None, Form()] = None,
) -> Response:
    order_service.update_order(
        db,
        order_id,
        notes=notes or None,
        lines=[
            order_service.OrderLineInput(pid, qty)
            for pid, qty in _parse_pairs(product_id, quantity)
        ],
    )
    return _redirect(f"/orders/{order_id}")


@router.post("/orders/{order_id}/confirm")
def confirm(request: Request, order_id: int, db: DbSession, _: Seller) -> Response:
    order_service.confirm_order(db, order_id)
    return _redirect(f"/orders/{order_id}")


@router.post("/orders/{order_id}/cancel")
def cancel(request: Request, order_id: int, db: DbSession, _: Seller) -> Response:
    order_service.cancel_order(db, order_id)
    return _redirect(f"/orders/{order_id}")


@router.post("/orders/{order_id}/delivery-notes")
def deliver(
    request: Request,
    order_id: int,
    db: DbSession,
    user: Seller,
    order_line_id: FormList,
    quantity: FormList,
    date: Annotated[str | None, Form()] = None,
) -> Response:
    note = delivery_service.create_delivery_note(
        db,
        order_id,
        note_date=_parse_date(date),
        lines=[
            delivery_service.DeliveryLineInput(line_id, qty)
            for line_id, qty in _parse_pairs(order_line_id, quantity)
        ],
        created_by=user.id,
    )
    return _redirect(f"/delivery-notes/{note.id}")
