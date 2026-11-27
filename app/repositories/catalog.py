"""Queries for products, customers and suppliers. No business rules here."""

from typing import Any

from sqlalchemy import ColumnElement, Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.catalog import Customer, Product, Supplier
from app.repositories.base import Repository


def paginate[M: Base](
    session: Session, stmt: Select[M], *, limit: int, offset: int
) -> tuple[list[M], int]:
    """Run ``stmt`` (already filtered and ordered) for one page and count all matching rows."""
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = list(session.scalars(stmt.limit(limit).offset(offset)))
    return items, total


def _contains(column: Any, q: str) -> ColumnElement[bool]:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    condition: ColumnElement[bool] = column.ilike(f"%{escaped}%", escape="\\")
    return condition


class ProductRepository(Repository[Product]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Product)

    def search(
        self, *, q: str | None, active: bool | None, limit: int, offset: int
    ) -> tuple[list[Product], int]:
        stmt = select(Product)
        if q:
            stmt = stmt.where(or_(_contains(Product.sku, q), _contains(Product.name, q)))
        if active is not None:
            stmt = stmt.where(Product.is_active.is_(active))
        stmt = stmt.order_by(Product.sku, Product.id)
        return paginate(self.session, stmt, limit=limit, offset=offset)

    def sku_taken(self, sku: str, *, exclude_id: int | None = None) -> bool:
        condition = [func.lower(Product.sku) == sku.lower()]
        if exclude_id is not None:
            condition.append(Product.id != exclude_id)
        return bool(self.session.scalar(select(exists().where(*condition))))


class CustomerRepository(Repository[Customer]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Customer)

    def search(
        self, *, q: str | None, active: bool | None, limit: int, offset: int
    ) -> tuple[list[Customer], int]:
        stmt = select(Customer)
        if q:
            stmt = stmt.where(or_(_contains(Customer.name, q), _contains(Customer.cuit, q)))
        if active is not None:
            stmt = stmt.where(Customer.is_active.is_(active))
        stmt = stmt.order_by(Customer.name, Customer.id)
        return paginate(self.session, stmt, limit=limit, offset=offset)

    def cuit_taken(self, cuit: str, *, exclude_id: int | None = None) -> bool:
        condition = [Customer.cuit == cuit]
        if exclude_id is not None:
            condition.append(Customer.id != exclude_id)
        return bool(self.session.scalar(select(exists().where(*condition))))


class SupplierRepository(Repository[Supplier]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Supplier)

    def search(
        self, *, q: str | None, active: bool | None, limit: int, offset: int
    ) -> tuple[list[Supplier], int]:
        stmt = select(Supplier)
        if q:
            stmt = stmt.where(or_(_contains(Supplier.name, q), _contains(Supplier.cuit, q)))
        if active is not None:
            stmt = stmt.where(Supplier.is_active.is_(active))
        stmt = stmt.order_by(Supplier.name, Supplier.id)
        return paginate(self.session, stmt, limit=limit, offset=offset)

    def cuit_taken(self, cuit: str, *, exclude_id: int | None = None) -> bool:
        condition = [Supplier.cuit == cuit]
        if exclude_id is not None:
            condition.append(Supplier.id != exclude_id)
        return bool(self.session.scalar(select(exists().where(*condition))))

    def list_active(self) -> list[Supplier]:
        return self.list(Supplier.is_active.is_(True), order_by=(Supplier.name, Supplier.id))
