"""Queries of the collections module: receipts, allocations, open invoices and statements.

No business rules here: the service decides what the numbers mean.
"""

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import ColumnElement, Row, func, select
from sqlalchemy.orm import QueryableAttribute, Session, selectinload

from app.core.money import ZERO
from app.models.catalog import Customer
from app.models.collections import Receipt, ReceiptAllocation
from app.models.sales import Invoice
from app.repositories.base import Repository
from app.services.invoice_balance import invoice_balances_subquery


class ReceiptRepository(Repository[Receipt]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Receipt)

    def get_with_allocations(self, receipt_id: int) -> Receipt | None:
        return self.session.scalars(
            select(Receipt)
            .where(Receipt.id == receipt_id)
            .options(
                selectinload(Receipt.customer),
                selectinload(Receipt.allocations).selectinload(ReceiptAllocation.invoice),
            )
        ).one_or_none()

    def search(
        self,
        *,
        customer_id: int | None,
        date_from: dt.date | None,
        date_to: dt.date | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Receipt], int]:
        where: list[ColumnElement[bool]] = []
        if customer_id is not None:
            where.append(Receipt.customer_id == customer_id)
        if date_from is not None:
            where.append(Receipt.date >= date_from)
        if date_to is not None:
            where.append(Receipt.date <= date_to)
        total = self.session.scalar(select(func.count()).select_from(Receipt).where(*where)) or 0
        items = self.session.scalars(
            select(Receipt)
            .where(*where)
            .options(selectinload(Receipt.customer))
            .order_by(Receipt.date.desc(), Receipt.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(items), total

    def receipts_between(
        self, customer_id: int, date_from: dt.date | None, date_to: dt.date | None
    ) -> list[Receipt]:
        where = [Receipt.customer_id == customer_id, *_date_range(Receipt.date, date_from, date_to)]
        return list(
            self.session.scalars(select(Receipt).where(*where).order_by(Receipt.date, Receipt.id))
        )

    def receipts_total_before(self, customer_id: int, day: dt.date) -> Decimal:
        value = self.session.scalar(
            select(func.coalesce(func.sum(Receipt.amount), ZERO)).where(
                Receipt.customer_id == customer_id, Receipt.date < day
            )
        )
        return Decimal(value if value is not None else ZERO)


class InvoiceReadRepository(Repository[Invoice]):
    """Read/lock access to invoices (documents owned by the sales module)."""

    def __init__(self, session: Session) -> None:
        super().__init__(session, Invoice)

    def lock_many(self, invoice_ids: Sequence[int]) -> list[Invoice]:
        """SELECT ... FOR UPDATE, always in invoice id order (no deadlocks between receipts)."""
        if not invoice_ids:
            return []
        return list(
            self.session.scalars(
                select(Invoice)
                .where(Invoice.id.in_(invoice_ids))
                .order_by(Invoice.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def open_for_customer(
        self, customer_id: int, *, limit: int | None = None, offset: int | None = None
    ) -> tuple[list[Row[Invoice, Decimal, Decimal]], int]:
        """(invoice, paid, balance) of the customer's invoices with balance > 0, oldest first."""
        balances = invoice_balances_subquery()
        where = [Invoice.customer_id == customer_id, balances.c.balance > 0]
        base = select(Invoice, balances.c.paid, balances.c.balance).join(
            balances, balances.c.invoice_id == Invoice.id
        )
        total = (
            self.session.scalar(
                select(func.count())
                .select_from(Invoice)
                .join(balances, balances.c.invoice_id == Invoice.id)
                .where(*where)
            )
            or 0
        )
        rows = self.session.execute(
            base.where(*where)
            .order_by(Invoice.due_date, Invoice.date, Invoice.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return list(rows), total

    def invoices_between(
        self, customer_id: int, date_from: dt.date | None, date_to: dt.date | None
    ) -> list[Invoice]:
        where = [Invoice.customer_id == customer_id, *_date_range(Invoice.date, date_from, date_to)]
        return list(
            self.session.scalars(select(Invoice).where(*where).order_by(Invoice.date, Invoice.id))
        )

    def invoices_total_before(self, customer_id: int, day: dt.date) -> Decimal:
        value = self.session.scalar(
            select(func.coalesce(func.sum(Invoice.total), ZERO)).where(
                Invoice.customer_id == customer_id, Invoice.date < day
            )
        )
        return Decimal(value if value is not None else ZERO)


class CustomerReadRepository(Repository[Customer]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Customer)

    def active(self) -> list[Customer]:
        return self.list(Customer.is_active.is_(True), order_by=(Customer.name, Customer.id))


def _date_range(
    column: QueryableAttribute[dt.date], date_from: dt.date | None, date_to: dt.date | None
) -> list[ColumnElement[bool]]:
    where: list[ColumnElement[bool]] = []
    if date_from is not None:
        where.append(column >= date_from)
    if date_to is not None:
        where.append(column <= date_to)
    return where
