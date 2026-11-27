"""Invoice queries. No business rules here."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.catalog import Customer
from app.models.enums import InvoiceLetter
from app.models.sales import DeliveryNote, Invoice, Order
from app.repositories.base import Repository
from app.services.invoice_balance import invoice_balances_subquery


@dataclass(frozen=True, slots=True)
class InvoiceFilters:
    customer_id: int | None = None
    letter: InvoiceLetter | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    only_open: bool | None = None  # True: balance > 0, False: balance <= 0, None: all


class InvoiceRepository(Repository[Invoice]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Invoice)

    def get_with_lines(self, invoice_id: int) -> Invoice | None:
        return self.session.scalars(
            select(Invoice).options(selectinload(Invoice.lines)).where(Invoice.id == invoice_id)
        ).one_or_none()

    def exists_for_delivery_note(self, delivery_note_id: int) -> bool:
        return bool(
            self.session.scalar(
                select(exists().where(Invoice.delivery_note_id == delivery_note_id))
            )
        )

    def search(
        self, filters: InvoiceFilters, *, limit: int, offset: int
    ) -> tuple[list[tuple[Invoice, Decimal]], int]:
        """Invoices matching ``filters`` with their balance (from invoice_balance), newest
        first, plus the total count."""
        balances = invoice_balances_subquery()
        where: list[ColumnElement[bool]] = []
        if filters.customer_id is not None:
            where.append(Invoice.customer_id == filters.customer_id)
        if filters.letter is not None:
            where.append(Invoice.letter == filters.letter)
        if filters.date_from is not None:
            where.append(Invoice.date >= filters.date_from)
        if filters.date_to is not None:
            where.append(Invoice.date <= filters.date_to)
        if filters.only_open is True:
            where.append(balances.c.balance > 0)
        elif filters.only_open is False:
            where.append(balances.c.balance <= 0)

        base = select(Invoice, balances.c.balance).join(
            balances, balances.c.invoice_id == Invoice.id
        )
        total = self.session.scalar(select(func.count()).select_from(base.where(*where).subquery()))
        rows = self.session.execute(
            base.where(*where)
            .order_by(Invoice.date.desc(), Invoice.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [(invoice, balance) for invoice, balance in rows], int(total or 0)


class DeliveryNoteReadRepository(Repository[DeliveryNote]):
    """Read-only access to delivery notes for invoicing (owned by D1; we only read)."""

    def __init__(self, session: Session) -> None:
        super().__init__(session, DeliveryNote)

    def uninvoiced(self, *, limit: int) -> list[DeliveryNote]:
        stmt = (
            select(DeliveryNote)
            .options(joinedload(DeliveryNote.order).joinedload(Order.customer))
            .where(~exists().where(Invoice.delivery_note_id == DeliveryNote.id))
            .order_by(DeliveryNote.date, DeliveryNote.id)
            .limit(limit)
        )
        return list(self.session.scalars(stmt))


class CustomerReadRepository(Repository[Customer]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Customer)

    def all_by_name(self) -> list[Customer]:
        return self.list(order_by=(Customer.name, Customer.id))
