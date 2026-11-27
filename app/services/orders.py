"""Order lifecycle: draft -> confirmed -> partially_delivered -> delivered, or cancelled.

Owner: agent D1 · Orders & delivery notes. Delivery notes live in
``app/services/delivery_notes.py``; this module also exposes the read model (lines with
delivered/pending quantities and informative totals) shared by the API and web views.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.errors import BusinessRuleViolation
from app.core.money import MILLI, ZERO, compute_line_amounts
from app.models.catalog import Customer, Product
from app.models.enums import DocumentType, OrderStatus, VatRate
from app.models.sales import Order, OrderLine
from app.models.sequences import GLOBAL_POINT_OF_SALE
from app.repositories.base import Repository
from app.repositories.orders import OrderRepository
from app.services.sequences import next_number

QUANTITY_ZERO = Decimal("0.000")
# Numeric(12, 3): at most 9 integer digits.
MAX_QUANTITY = Decimal("1000000000")

EDITABLE_STATUSES = frozenset({OrderStatus.DRAFT})
CANCELLABLE_STATUSES = frozenset({OrderStatus.DRAFT, OrderStatus.CONFIRMED})
DELIVERABLE_STATUSES = frozenset({OrderStatus.CONFIRMED, OrderStatus.PARTIALLY_DELIVERED})


class _Unset:
    """Sentinel for "field not sent" in partial updates (``None`` clears the notes)."""


UNSET = _Unset()


# Inputs -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrderLineInput:
    product_id: int
    quantity: Decimal


# Read model -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrderTotals:
    net_total: Decimal
    vat_21: Decimal
    vat_10_5: Decimal

    @property
    def total(self) -> Decimal:
        return self.net_total + self.vat_21 + self.vat_10_5


@dataclass(frozen=True, slots=True)
class OrderLineView:
    line: OrderLine
    net_amount: Decimal
    vat_amount: Decimal
    delivered_quantity: Decimal

    @property
    def pending_quantity(self) -> Decimal:
        return self.line.quantity - self.delivered_quantity


@dataclass(frozen=True, slots=True)
class OrderView:
    order: Order
    lines: list[OrderLineView]
    totals: OrderTotals

    @property
    def has_pending(self) -> bool:
        return any(line.pending_quantity > 0 for line in self.lines)


# Pure rules -------------------------------------------------------------------------------


def validate_quantity(value: Decimal) -> Decimal:
    """Positive, finite, at most three decimals and within Numeric(12, 3)."""
    try:
        valid = value.is_finite() and ZERO < value < MAX_QUANTITY and value == value.quantize(MILLI)
    except InvalidOperation:
        valid = False
    if not valid:
        raise BusinessRuleViolation(
            f"Quantity {value} must be positive with at most three decimals", code="bad_quantity"
        )
    return value.quantize(MILLI)


def compute_totals(lines: Iterable[tuple[Decimal, Decimal, VatRate]]) -> OrderTotals:
    """Totals = sum of rounded line amounts, VAT split by rate (same rule as invoices)."""
    net = ZERO
    vat = {VatRate.VAT_21: ZERO, VatRate.VAT_10_5: ZERO}
    for quantity, unit_price, vat_rate in lines:
        amounts = compute_line_amounts(quantity, unit_price, vat_rate.fraction)
        net += amounts.net
        vat[vat_rate] += amounts.vat
    return OrderTotals(net_total=net, vat_21=vat[VatRate.VAT_21], vat_10_5=vat[VatRate.VAT_10_5])


def status_after_delivery(pending: Iterable[Decimal]) -> OrderStatus:
    return (
        OrderStatus.PARTIALLY_DELIVERED
        if any(qty > 0 for qty in pending)
        else OrderStatus.DELIVERED
    )


def ensure_status(order: Order, allowed: frozenset[OrderStatus], action: str) -> None:
    if order.status not in allowed:
        raise BusinessRuleViolation(
            f"Cannot {action} order {order.number} in status {order.status}",
            code="invalid_status",
        )


# Queries ----------------------------------------------------------------------------------


def build_view(session: Session, order: Order) -> OrderView:
    delivered = OrderRepository(session).delivered_by_line(order.id)
    views: list[OrderLineView] = []
    for line in order.lines:
        amounts = compute_line_amounts(line.quantity, line.unit_price, line.vat_rate.fraction)
        views.append(
            OrderLineView(
                line=line,
                net_amount=amounts.net,
                vat_amount=amounts.vat,
                delivered_quantity=delivered.get(line.id, QUANTITY_ZERO),
            )
        )
    totals = compute_totals((ln.quantity, ln.unit_price, ln.vat_rate) for ln in order.lines)
    return OrderView(order=order, lines=views, totals=totals)


def get_order(session: Session, order_id: int) -> OrderView:
    return build_view(session, OrderRepository(session).get_detail(order_id))


def list_orders(
    session: Session,
    *,
    status: OrderStatus | None = None,
    customer_id: int | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[tuple[Order, OrderTotals]], int]:
    orders, total = OrderRepository(session).search(
        status=status,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    items = [
        (o, compute_totals((ln.quantity, ln.unit_price, ln.vat_rate) for ln in o.lines))
        for o in orders
    ]
    return items, total


# Commands ---------------------------------------------------------------------------------


def _active_customer(session: Session, customer_id: int) -> Customer:
    customer = Repository(session, Customer).get_or_raise(customer_id)
    if not customer.is_active:
        raise BusinessRuleViolation(
            f"Customer {customer.name} is inactive", code="inactive_customer"
        )
    return customer


def _build_lines(session: Session, lines: Sequence[OrderLineInput]) -> list[OrderLine]:
    """Validate the requested lines and snapshot price and VAT rate from each product."""
    if not lines:
        raise BusinessRuleViolation("An order needs at least one line", code="no_lines")
    product_ids = [line.product_id for line in lines]
    if len(set(product_ids)) != len(product_ids):
        raise BusinessRuleViolation(
            "Each product can appear only once per order", code="duplicate_product"
        )
    products = Repository(session, Product)
    built: list[OrderLine] = []
    for line in lines:
        quantity = validate_quantity(line.quantity)
        product = products.get_or_raise(line.product_id)
        if not product.is_active:
            raise BusinessRuleViolation(
                f"Product {product.sku} is inactive", code="inactive_product"
            )
        built.append(
            OrderLine(
                product_id=product.id,
                quantity=quantity,
                unit_price=product.unit_price,
                vat_rate=product.vat_rate,
            )
        )
    return built


def create_order(
    session: Session,
    *,
    customer_id: int,
    lines: Sequence[OrderLineInput],
    created_by: int,
    order_date: dt.date | None = None,
    notes: str | None = None,
) -> OrderView:
    customer = _active_customer(session, customer_id)
    order_lines = _build_lines(session, lines)
    order = Order(
        number=next_number(session, DocumentType.ORDER, point_of_sale=GLOBAL_POINT_OF_SALE),
        customer_id=customer.id,
        date=order_date or today(),
        status=OrderStatus.DRAFT,
        notes=notes,
        created_by=created_by,
        lines=order_lines,
    )
    session.add(order)
    session.flush()
    return get_order(session, order.id)


def update_order(
    session: Session,
    order_id: int,
    *,
    notes: str | _Unset | None = UNSET,
    lines: Sequence[OrderLineInput] | None = None,
) -> OrderView:
    """Draft only. ``lines`` (when given) replaces every line, with fresh price snapshots."""
    order = OrderRepository(session).lock(order_id)
    ensure_status(order, EDITABLE_STATUSES, "edit")
    new_lines = _build_lines(session, lines) if lines is not None else None

    if not isinstance(notes, _Unset):
        order.notes = notes
    if new_lines is not None:
        # Delete the old lines first: UNIQUE(order_id, product_id) would reject an insert of
        # the same product before the delete is flushed.
        order.lines.clear()
        session.flush()
        order.lines.extend(new_lines)
    session.flush()
    session.expire(order)
    return get_order(session, order.id)


def confirm_order(session: Session, order_id: int) -> OrderView:
    order = OrderRepository(session).lock(order_id)
    ensure_status(order, EDITABLE_STATUSES, "confirm")
    order.status = OrderStatus.CONFIRMED
    session.flush()
    return get_order(session, order.id)


def cancel_order(session: Session, order_id: int) -> OrderView:
    repo = OrderRepository(session)
    order = repo.lock(order_id)
    ensure_status(order, CANCELLABLE_STATUSES, "cancel")
    if repo.has_delivery_notes(order.id):
        raise BusinessRuleViolation(
            f"Order {order.number} has delivery notes and cannot be cancelled",
            code="has_delivery_notes",
        )
    order.status = OrderStatus.CANCELLED
    session.flush()
    return get_order(session, order.id)
