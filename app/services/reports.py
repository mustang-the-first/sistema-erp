"""Reports: sales by period, low stock and receivables aging. Read-only.

Rules owned here:

- **Sales date range.** Both bounds inclusive, by invoice date. A missing bound defaults to the
  month of the other bound (``from`` -> first day, ``to`` -> last day); with neither, the
  current month (``app.core.clock.today()``). ``from > to`` is a ``BusinessRuleViolation``.
- **Sales totals.** The grand total is the sum of the grouped rows (``invoice_count`` is
  ``None`` when grouping by product: one invoice spans several products).
- **Receivables aging.** Days overdue = ``as_of - due_date``. Invoices not yet due (negative
  days) go into the ``0-30`` bucket, as the plan defines only four buckets. Only invoices issued
  and receipts dated on or before ``as_of`` count (``invoice_balances_subquery(as_of)``).
"""

import calendar
import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.errors import BusinessRuleViolation
from app.core.money import ZERO, round_money, round_quantity
from app.repositories.reports import DueDateRange, ReportsRepository
from app.schemas.reports import (
    AgingAmounts,
    LowStockItem,
    LowStockReport,
    ReceivablesReport,
    ReceivablesRow,
    SalesAmounts,
    SalesGroupBy,
    SalesReport,
    SalesRow,
)

MAX_PAGE_SIZE = 200


# Pure rules -------------------------------------------------------------------------------


def first_day_of_month(day: dt.date) -> dt.date:
    return day.replace(day=1)


def last_day_of_month(day: dt.date) -> dt.date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def resolve_sales_range(
    date_from: dt.date | None, date_to: dt.date | None, *, reference: dt.date
) -> tuple[dt.date, dt.date]:
    if date_from is None:
        date_from = first_day_of_month(date_to or reference)
    if date_to is None:
        date_to = last_day_of_month(date_from)
    if date_from > date_to:
        raise BusinessRuleViolation(
            f"'from' ({date_from.isoformat()}) must not be after 'to' ({date_to.isoformat()})",
            code="invalid_date_range",
        )
    return date_from, date_to


def sum_sales(rows: Iterable[SalesAmounts], *, count_invoices: bool) -> SalesAmounts:
    net = vat_21 = vat_10_5 = total = ZERO
    invoices = 0
    for row in rows:
        net += row.net
        vat_21 += row.vat_21
        vat_10_5 += row.vat_10_5
        total += row.total
        invoices += row.invoice_count or 0
    return SalesAmounts(
        invoice_count=invoices if count_invoices else None,
        net=net,
        vat_21=vat_21,
        vat_10_5=vat_10_5,
        total=total,
    )


@dataclass(frozen=True, slots=True)
class AgingBucket:
    """Inclusive range of days overdue; ``None`` means unbounded."""

    name: str
    label: str
    min_days: int | None
    max_days: int | None

    def contains(self, days_overdue: int) -> bool:
        above_min = self.min_days is None or days_overdue >= self.min_days
        below_max = self.max_days is None or days_overdue <= self.max_days
        return above_min and below_max

    def due_date_range(self, as_of: dt.date) -> DueDateRange:
        # days_overdue = as_of - due_date, so the bounds swap when expressed as due dates.
        def shift(days: int | None) -> dt.date | None:
            return None if days is None else as_of - dt.timedelta(days=days)

        return DueDateRange(self.name, earliest=shift(self.max_days), latest=shift(self.min_days))


AGING_BUCKETS: tuple[AgingBucket, ...] = (
    AgingBucket("days_0_30", "0-30", None, 30),  # includes invoices not yet due
    AgingBucket("days_31_60", "31-60", 31, 60),
    AgingBucket("days_61_90", "61-90", 61, 90),
    AgingBucket("days_over_90", "+90", 91, None),
)


def days_overdue(due_date: dt.date, as_of: dt.date) -> int:
    return (as_of - due_date).days


def aging_bucket_for(due_date: dt.date, as_of: dt.date) -> AgingBucket:
    days = days_overdue(due_date, as_of)
    return next(bucket for bucket in AGING_BUCKETS if bucket.contains(days))


def sum_aging(rows: Iterable[AgingAmounts]) -> AgingAmounts:
    totals = {"balance": ZERO, **{bucket.name: ZERO for bucket in AGING_BUCKETS}}
    for row in rows:
        for field in totals:
            totals[field] += getattr(row, field)
    return AgingAmounts(**totals)


# Use cases --------------------------------------------------------------------------------


def sales_report(
    session: Session,
    *,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    group_by: SalesGroupBy = SalesGroupBy.DAY,
) -> SalesReport:
    start, end = resolve_sales_range(date_from, date_to, reference=today())
    records = ReportsRepository(session).sales(group_by, start, end)
    count_invoices = group_by is not SalesGroupBy.PRODUCT
    rows = [
        SalesRow(
            key=r["key"],
            label=r["label"],
            invoice_count=r["invoice_count"] if count_invoices else None,
            net=round_money(r["net"]),
            vat_21=round_money(r["vat_21"]),
            vat_10_5=round_money(r["vat_10_5"]),
            total=round_money(r["total"]),
        )
        for r in records
    ]
    return SalesReport(
        date_from=start,
        date_to=end,
        group_by=group_by,
        rows=rows,
        totals=sum_sales(rows, count_invoices=count_invoices),
    )


def low_stock_report(session: Session, *, limit: int = 50, offset: int = 0) -> LowStockReport:
    if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
        raise BusinessRuleViolation(
            f"limit must be between 1 and {MAX_PAGE_SIZE} and offset >= 0",
            code="invalid_pagination",
        )
    records, total = ReportsRepository(session).low_stock(limit=limit, offset=offset)
    items = [
        LowStockItem(
            product_id=r["product_id"],
            sku=r["sku"],
            name=r["name"],
            unit=r["unit"],
            stock_qty=round_quantity(r["stock_qty"]),
            min_stock=round_quantity(r["min_stock"]),
            shortfall=round_quantity(r["shortfall"]),
            supplier_name=r["supplier_name"],
        )
        for r in records
    ]
    return LowStockReport(items=items, total=total)


def receivables_report(session: Session, *, as_of: dt.date | None = None) -> ReceivablesReport:
    cutoff = as_of or today()
    ranges = [bucket.due_date_range(cutoff) for bucket in AGING_BUCKETS]
    records = ReportsRepository(session).receivables_by_customer(cutoff, ranges)
    rows = [
        ReceivablesRow(
            customer_id=r["customer_id"],
            customer_name=r["customer_name"],
            invoice_count=r["invoice_count"],
            balance=round_money(r["balance"]),
            **{bucket.name: round_money(r[bucket.name]) for bucket in AGING_BUCKETS},
        )
        for r in records
    ]
    return ReceivablesReport(as_of=cutoff, rows=rows, totals=sum_aging(rows))
