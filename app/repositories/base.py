"""Generic repository. Module repositories subclass it; cross-module reads use it directly:

customers = Repository(session, Customer)
customer = customers.get_or_raise(customer_id)
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.models.base import Base


class Repository[M: Base]:
    def __init__(self, session: Session, model: type[M]) -> None:
        self.session = session
        self.model = model

    def get(self, id_: int) -> M | None:
        return self.session.get(self.model, id_)

    def get_or_raise(self, id_: int) -> M:
        obj = self.get(id_)
        if obj is None:
            raise NotFound(f"{self.model.__name__} {id_} not found")
        return obj

    def get_for_update(self, id_: int) -> M:
        obj = self.session.get(self.model, id_, with_for_update=True, populate_existing=True)
        if obj is None:
            raise NotFound(f"{self.model.__name__} {id_} not found")
        return obj

    def list(
        self,
        *where: ColumnElement[bool],
        order_by: Sequence[Any] = (),
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[M]:
        stmt = select(self.model).where(*where).order_by(*order_by).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def add(self, obj: M) -> M:
        self.session.add(obj)
        self.session.flush()
        return obj
