"""Stock ledger: the ONLY writer of ``stock_movements`` and ``products.stock_qty``.

Locking rule: products are locked with SELECT ... FOR UPDATE one by one in ascending
``product_id`` order. Every caller goes through ``lock_products``, so two transactions touching
the same products always acquire locks in the same order and cannot deadlock.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, NotFound
from app.core.money import MILLI, round_money
from app.models.catalog import Product
from app.models.enums import AdjustmentReason, StockMovementKind
from app.models.stock import StockMovement


@dataclass(frozen=True, slots=True)
class MovementDraft:
    product_id: int
    quantity: Decimal  # signed: > 0 in, < 0 out
    kind: StockMovementKind
    reason: AdjustmentReason | None = None
    delivery_note_line_id: int | None = None
    note: str | None = None


def lock_products(session: Session, product_ids: Iterable[int]) -> dict[int, Product]:
    """Lock the given products in ascending id order and return them refreshed from the DB."""
    locked: dict[int, Product] = {}
    for product_id in sorted(set(product_ids)):
        product = session.scalars(
            select(Product)
            .where(Product.id == product_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if product is None:
            raise NotFound(f"Product {product_id} not found")
        locked[product_id] = product
    return locked


def _validate(draft: MovementDraft) -> None:
    if draft.quantity == 0 or draft.quantity != draft.quantity.quantize(MILLI):
        raise BusinessRuleViolation(
            "Quantity must be non-zero with at most three decimals", code="bad_quantity"
        )
    if draft.kind is StockMovementKind.DELIVERY:
        if draft.reason is not None or draft.delivery_note_line_id is None or draft.quantity > 0:
            raise BusinessRuleViolation(
                "A delivery is an outgoing movement linked to a delivery note line",
                code="bad_movement",
            )
    else:
        if draft.reason is None or draft.delivery_note_line_id is not None:
            raise BusinessRuleViolation("An adjustment needs a reason", code="bad_movement")
        if draft.reason is AdjustmentReason.INITIAL_LOAD and draft.quantity < 0:
            raise BusinessRuleViolation("Initial load must be positive", code="bad_movement")
        if draft.reason is AdjustmentReason.DAMAGE and draft.quantity > 0:
            raise BusinessRuleViolation("Damage must be negative", code="bad_movement")


def apply_movements(
    session: Session,
    drafts: Sequence[MovementDraft],
    *,
    movement_date: dt.date,
    created_by: int,
) -> list[StockMovement]:
    """Validate, lock (ordered), check non-negative stock and persist the movements.

    Each movement snapshots the product's current ``unit_cost``. Raises
    ``BusinessRuleViolation(code="insufficient_stock")`` if any product would go negative;
    nothing is written in that case.
    """
    if not drafts:
        raise BusinessRuleViolation("No stock movements given", code="bad_movement")
    for draft in drafts:
        _validate(draft)

    products = lock_products(session, (d.product_id for d in drafts))

    resulting = {pid: p.stock_qty for pid, p in products.items()}
    for draft in drafts:
        resulting[draft.product_id] += draft.quantity
    short = sorted(pid for pid, qty in resulting.items() if qty < 0)
    if short:
        details = ", ".join(
            f"{products[pid].sku} (available {products[pid].stock_qty})" for pid in short
        )
        raise BusinessRuleViolation(f"Insufficient stock: {details}", code="insufficient_stock")

    movements = [
        StockMovement(
            product_id=d.product_id,
            date=movement_date,
            quantity=d.quantity,
            kind=d.kind,
            reason=d.reason,
            unit_cost=products[d.product_id].unit_cost,
            delivery_note_line_id=d.delivery_note_line_id,
            note=d.note,
            created_by=created_by,
        )
        for d in drafts
    ]
    for pid, qty in resulting.items():
        products[pid].stock_qty = qty
    session.add_all(movements)
    session.flush()
    return movements


def movement_value(movement: StockMovement) -> Decimal:
    """|quantity| x snapshot unit_cost, rounded to cents. Used to value accounting entries."""
    return round_money(abs(movement.quantity) * movement.unit_cost)
