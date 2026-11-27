"""Delivery note queries. Owner: agent D1 · Orders & delivery notes."""

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.base import ExecutableOption

from app.core.errors import NotFound
from app.models.sales import DeliveryNote, DeliveryNoteLine, Order
from app.repositories.base import Repository


def _detail_options() -> tuple[ExecutableOption, ...]:
    return (
        selectinload(DeliveryNote.order).selectinload(Order.customer),
        selectinload(DeliveryNote.lines).selectinload(DeliveryNoteLine.product),
        selectinload(DeliveryNote.invoice),
    )


class DeliveryNoteRepository(Repository[DeliveryNote]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, DeliveryNote)

    def get_detail(self, note_id: int) -> DeliveryNote:
        note = self.session.scalars(
            select(DeliveryNote).where(DeliveryNote.id == note_id).options(*_detail_options())
        ).one_or_none()
        if note is None:
            raise NotFound(f"Delivery note {note_id} not found")
        return note

    def search(
        self,
        *,
        order_id: int | None = None,
        invoiced: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DeliveryNote], int]:
        where: list[ColumnElement[bool]] = []
        if order_id is not None:
            where.append(DeliveryNote.order_id == order_id)
        if invoiced is True:
            where.append(DeliveryNote.invoice.has())
        elif invoiced is False:
            where.append(~DeliveryNote.invoice.has())
        total = (
            self.session.scalar(select(func.count()).select_from(DeliveryNote).where(*where)) or 0
        )
        items = self.session.scalars(
            select(DeliveryNote)
            .where(*where)
            .options(*_detail_options())
            .order_by(DeliveryNote.date.desc(), DeliveryNote.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return list(items), total
