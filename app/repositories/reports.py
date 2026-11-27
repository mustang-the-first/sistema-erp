"""Read-only aggregation queries of the reports module. All grouping happens in SQL.

No business rules here: date ranges, aging buckets and defaults come from
``app/services/reports.py``. Invoice balances come only from ``invoice_balances_subquery``.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Integer,
    Numeric,
    RowMapping,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    select,
    true,
)
from sqlalchemy.orm import Session

from app.models.catalog import Customer, Product, Supplier
from app.models.enums import VatRate
from app.models.sales import Invoice, InvoiceLine
from app.schemas.reports import SalesGroupBy
from app.services.invoice_balance import invoice_balances_subquery

MONEY = Numeric(14, 2)


@dataclass(frozen=True, slots=True)
class DueDateRange:
    """An aging bucket expressed as an inclusive range of due dates (``None`` = unbounded)."""

    name: str
    earliest: dt.date | None
    latest: dt.date | None


def _money_sum(expr: Any) -> ColumnElement[Decimal]:
    return func.coalesce(func.sum(expr), literal(0, MONEY))


class ReportsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # Sales ---------------------------------------------------------------------------------

    def sales(
        self, group_by: SalesGroupBy, date_from: dt.date, date_to: dt.date
    ) -> Sequence[RowMapping]:
        """Rows: key, label, invoice_count, net, vat_21, vat_10_5, total."""
        if group_by is SalesGroupBy.PRODUCT:
            return self._sales_by_product(date_from, date_to)
        return self._sales_by_invoice_attribute(group_by, date_from, date_to)

    def _sales_by_invoice_attribute(
        self, group_by: SalesGroupBy, date_from: dt.date, date_to: dt.date
    ) -> Sequence[RowMapping]:
        total = _money_sum(Invoice.total)
        key: Any
        label: Any
        if group_by is SalesGroupBy.CUSTOMER:
            key, label = cast(Customer.id, String), Customer.name
            group_cols: list[Any] = [Customer.id, Customer.name]
            order_by: list[Any] = [total.desc(), Customer.name, Customer.id]
        else:
            fmt = "YYYY-MM-DD" if group_by is SalesGroupBy.DAY else "YYYY-MM"
            key = label = func.to_char(Invoice.date, fmt)
            group_cols = [key]
            order_by = [key]
        stmt = (
            select(
                key.label("key"),
                label.label("label"),
                func.count(Invoice.id).label("invoice_count"),
                _money_sum(Invoice.net_total).label("net"),
                _money_sum(Invoice.vat_21).label("vat_21"),
                _money_sum(Invoice.vat_10_5).label("vat_10_5"),
                total.label("total"),
            )
            .join(Customer, Customer.id == Invoice.customer_id)
            .where(Invoice.date >= date_from, Invoice.date <= date_to)
            .group_by(*group_cols)
            .order_by(*order_by)
        )
        return self.session.execute(stmt).mappings().all()

    def _sales_by_product(self, date_from: dt.date, date_to: dt.date) -> Sequence[RowMapping]:
        zero = literal(0, MONEY)
        vat_21 = _money_sum(
            case((InvoiceLine.vat_rate == VatRate.VAT_21, InvoiceLine.vat_amount), else_=zero)
        )
        vat_10_5 = _money_sum(
            case((InvoiceLine.vat_rate == VatRate.VAT_10_5, InvoiceLine.vat_amount), else_=zero)
        )
        net = _money_sum(InvoiceLine.net_amount)
        total = net + _money_sum(InvoiceLine.vat_amount)
        stmt = (
            select(
                cast(Product.id, String).label("key"),
                (Product.sku + " · " + Product.name).label("label"),
                literal(None, Integer).label("invoice_count"),
                net.label("net"),
                vat_21.label("vat_21"),
                vat_10_5.label("vat_10_5"),
                total.label("total"),
            )
            .select_from(InvoiceLine)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .join(Product, Product.id == InvoiceLine.product_id)
            .where(Invoice.date >= date_from, Invoice.date <= date_to)
            .group_by(Product.id, Product.sku, Product.name)
            .order_by(total.desc(), Product.sku)
        )
        return self.session.execute(stmt).mappings().all()

    # Low stock -----------------------------------------------------------------------------

    def low_stock(self, *, limit: int, offset: int) -> tuple[Sequence[RowMapping], int]:
        """Active products with stock_qty <= min_stock, biggest shortfall first."""
        condition = and_(Product.is_active.is_(true()), Product.stock_qty <= Product.min_stock)
        shortfall = (Product.min_stock - Product.stock_qty).label("shortfall")
        stmt = (
            select(
                Product.id.label("product_id"),
                Product.sku,
                Product.name,
                Product.unit,
                Product.stock_qty,
                Product.min_stock,
                shortfall,
                Supplier.name.label("supplier_name"),
            )
            .outerjoin(Supplier, Supplier.id == Product.supplier_id)
            .where(condition)
            .order_by(shortfall.desc(), Product.sku)
            .limit(limit)
            .offset(offset)
        )
        rows = self.session.execute(stmt).mappings().all()
        total = self.session.scalar(select(func.count()).select_from(Product).where(condition))
        return rows, total or 0

    # Receivables ---------------------------------------------------------------------------

    def receivables_by_customer(
        self, as_of: dt.date, buckets: Sequence[DueDateRange]
    ) -> Sequence[RowMapping]:
        """Rows: customer_id, customer_name, invoice_count, balance and one column per bucket.

        Only invoices with a positive balance as of ``as_of`` count.
        """
        balances = invoice_balances_subquery(as_of=as_of)
        zero = literal(0, MONEY)
        bucket_columns = []
        for bucket in buckets:
            conditions: list[ColumnElement[bool]] = []
            if bucket.earliest is not None:
                conditions.append(balances.c.due_date >= bucket.earliest)
            if bucket.latest is not None:
                conditions.append(balances.c.due_date <= bucket.latest)
            in_bucket = and_(true(), *conditions)
            bucket_columns.append(
                _money_sum(case((in_bucket, balances.c.balance), else_=zero)).label(bucket.name)
            )
        stmt = (
            select(
                Customer.id.label("customer_id"),
                Customer.name.label("customer_name"),
                func.count(balances.c.invoice_id).label("invoice_count"),
                _money_sum(balances.c.balance).label("balance"),
                *bucket_columns,
            )
            .select_from(balances)
            .join(Customer, Customer.id == balances.c.customer_id)
            .where(balances.c.balance > 0)
            .group_by(Customer.id, Customer.name)
            .order_by(Customer.name, Customer.id)
        )
        return self.session.execute(stmt).mappings().all()
