"""Delivery notes (remitos): partial deliveries of a confirmed order.

Owner: agent D1 · Orders & delivery notes.

Creating a delivery note, atomically (one SAVEPOINT: any failure leaves nothing behind, not even
a consumed number):

1. lock the order row (serializes concurrent deliveries of the same order) and check status;
2. validate each line: belongs to the order, quantity > 0, quantity <= pending;
3. number the note (``sequences``) and insert it with its lines;
4. ``stock_ledger.apply_movements`` (DELIVERY, negative quantity, locks products ordered by
   id; insufficient stock -> 422);
5. post the COGS entry ``build_delivery_entry(cost = sum(movement_value))`` via ``post_entry``;
6. move the order to partially_delivered / delivered.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.config import get_settings
from app.core.errors import BusinessRuleViolation
from app.core.money import ZERO
from app.models.enums import DocumentType, StockMovementKind
from app.models.sales import DeliveryNote, DeliveryNoteLine, Order
from app.repositories.delivery_notes import DeliveryNoteRepository
from app.repositories.orders import OrderRepository
from app.services.orders import (
    DELIVERABLE_STATUSES,
    QUANTITY_ZERO,
    ensure_status,
    status_after_delivery,
    validate_quantity,
)
from app.services.posting import build_delivery_entry, post_entry
from app.services.sequences import next_number
from app.services.stock_ledger import MovementDraft, apply_movements, movement_value


@dataclass(frozen=True, slots=True)
class DeliveryLineInput:
    order_line_id: int
    quantity: Decimal


def format_number(point_of_sale: int, number: int) -> str:
    """Argentine document number: 0001-00000042."""
    return f"{point_of_sale:04d}-{number:08d}"


def _validated_lines(
    session: Session, order: Order, lines: Sequence[DeliveryLineInput]
) -> list[DeliveryNoteLine]:
    if not lines:
        raise BusinessRuleViolation("A delivery note needs at least one line", code="no_lines")
    line_ids = [line.order_line_id for line in lines]
    if len(set(line_ids)) != len(line_ids):
        raise BusinessRuleViolation(
            "Each order line can appear only once per delivery note", code="duplicate_line"
        )
    order_lines = {line.id: line for line in order.lines}
    delivered = OrderRepository(session).delivered_by_line(order.id)
    built: list[DeliveryNoteLine] = []
    for requested in lines:
        order_line = order_lines.get(requested.order_line_id)
        if order_line is None:
            raise BusinessRuleViolation(
                f"Line {requested.order_line_id} does not belong to order {order.number}",
                code="line_not_in_order",
            )
        quantity = validate_quantity(requested.quantity)
        pending = order_line.quantity - delivered.get(order_line.id, QUANTITY_ZERO)
        if quantity > pending:
            raise BusinessRuleViolation(
                f"Line {order_line.id}: quantity {quantity} exceeds pending {pending}",
                code="exceeds_pending",
            )
        built.append(
            DeliveryNoteLine(
                order_line_id=order_line.id, product_id=order_line.product_id, quantity=quantity
            )
        )
    return built


def create_delivery_note(
    session: Session,
    order_id: int,
    *,
    lines: Sequence[DeliveryLineInput],
    created_by: int,
    note_date: dt.date | None = None,
) -> DeliveryNote:
    orders = OrderRepository(session)
    order = orders.lock(order_id)
    ensure_status(order, DELIVERABLE_STATUSES, "deliver")
    note_lines = _validated_lines(session, order, lines)
    issued = note_date or today()
    point_of_sale = get_settings().point_of_sale

    with session.begin_nested():
        note = DeliveryNote(
            point_of_sale=point_of_sale,
            number=next_number(session, DocumentType.DELIVERY_NOTE, point_of_sale=point_of_sale),
            order_id=order.id,
            date=issued,
            created_by=created_by,
            lines=note_lines,
        )
        session.add(note)
        session.flush()

        movements = apply_movements(
            session,
            [
                MovementDraft(
                    product_id=line.product_id,
                    quantity=-line.quantity,
                    kind=StockMovementKind.DELIVERY,
                    delivery_note_line_id=line.id,
                )
                for line in note.lines
            ],
            movement_date=issued,
            created_by=created_by,
        )
        draft = build_delivery_entry(
            delivery_note_id=note.id,
            entry_date=issued,
            description=f"Remito {format_number(point_of_sale, note.number)}",
            cost=sum((movement_value(m) for m in movements), ZERO),
        )
        if draft is not None:
            post_entry(session, draft, created_by=created_by)

        delivered = orders.delivered_by_line(order.id)
        order.status = status_after_delivery(
            line.quantity - delivered.get(line.id, QUANTITY_ZERO) for line in order.lines
        )
        session.flush()

    session.expire(order)
    return get_delivery_note(session, note.id)


def get_delivery_note(session: Session, note_id: int) -> DeliveryNote:
    return DeliveryNoteRepository(session).get_detail(note_id)


def list_delivery_notes(
    session: Session,
    *,
    order_id: int | None = None,
    invoiced: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[DeliveryNote], int]:
    return DeliveryNoteRepository(session).search(
        order_id=order_id, invoiced=invoiced, limit=limit, offset=offset
    )
