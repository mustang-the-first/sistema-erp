"""Order queries. Owner: agent D1 · Orders & delivery notes."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFound
from app.models.enums import OrderStatus
from app.models.sales import DeliveryNote, DeliveryNoteLine, Order, OrderLine
from app.repositories.base import Repository


class OrderRepository(Repository[Order]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Order)

    def get_detail(self, order_id: int) -> Order:
        order = self.session.scalars(
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.customer),
                selectinload(Order.lines).selectinload(OrderLine.product),
                selectinload(Order.delivery_notes).selectinload(DeliveryNote.invoice),
            )
        ).one_or_none()
        if order is None:
            raise NotFound(f"Order {order_id} not found")
        return order

    def lock(self, order_id: int) -> Order:
        """SELECT ... FOR UPDATE on the order row: serializes writes on the same order."""
        return self.get_for_update(order_id)

    def search(
        self,
        *,
        status: OrderStatus | None = None,
        customer_id: int | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Order], int]:
        where: list[ColumnElement[bool]] = []
        if status is not None:
            where.append(Order.status == status)
        if customer_id is not None:
            where.append(Order.customer_id == customer_id)
        if date_from is not None:
            where.append(Order.date >= date_from)
        if date_to is not None:
            where.append(Order.date <= date_to)
        total = self.session.scalar(select(func.count()).select_from(Order).where(*where)) or 0
        items = self.session.scalars(
            select(Order)
            .where(*where)
            .options(selectinload(Order.customer), selectinload(Order.lines))
            .order_by(Order.date.desc(), Order.number.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return list(items), total

    def delivered_by_line(self, order_id: int) -> dict[int, Decimal]:
        """Quantity already delivered per order line (lines without deliveries are absent)."""
        rows = self.session.execute(
            select(DeliveryNoteLine.order_line_id, func.sum(DeliveryNoteLine.quantity))
            .join(OrderLine, OrderLine.id == DeliveryNoteLine.order_line_id)
            .where(OrderLine.order_id == order_id)
            .group_by(DeliveryNoteLine.order_line_id)
        ).all()
        return {line_id: qty for line_id, qty in rows}

    def has_delivery_notes(self, order_id: int) -> bool:
        return bool(self.session.scalar(select(exists().where(DeliveryNote.order_id == order_id))))
