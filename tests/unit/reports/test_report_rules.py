import datetime as dt
from decimal import Decimal

import pytest

from app.core.errors import BusinessRuleViolation
from app.schemas.reports import AgingAmounts, SalesAmounts
from app.services.reports import (
    AGING_BUCKETS,
    aging_bucket_for,
    days_overdue,
    last_day_of_month,
    resolve_sales_range,
    sum_aging,
    sum_sales,
)

D = Decimal
REF = dt.date(2026, 11, 27)


# Sales date range -------------------------------------------------------------------------


def test_resolve_sales_range_without_bounds_returns_current_month() -> None:
    assert resolve_sales_range(None, None, reference=REF) == (
        dt.date(2026, 11, 1),
        dt.date(2026, 11, 30),
    )


def test_resolve_sales_range_with_only_from_ends_at_end_of_that_month() -> None:
    assert resolve_sales_range(dt.date(2024, 2, 10), None, reference=REF) == (
        dt.date(2024, 2, 10),
        dt.date(2024, 2, 29),
    )


def test_resolve_sales_range_with_only_to_starts_at_first_of_that_month() -> None:
    assert resolve_sales_range(None, dt.date(2026, 12, 15), reference=REF) == (
        dt.date(2026, 12, 1),
        dt.date(2026, 12, 15),
    )


def test_resolve_sales_range_with_both_bounds_keeps_them() -> None:
    bounds = (dt.date(2026, 1, 5), dt.date(2026, 3, 7))

    assert resolve_sales_range(*bounds, reference=REF) == bounds


def test_resolve_sales_range_with_equal_bounds_is_a_single_day() -> None:
    day = dt.date(2026, 10, 31)

    assert resolve_sales_range(day, day, reference=REF) == (day, day)


def test_resolve_sales_range_with_from_after_to_is_rejected() -> None:
    with pytest.raises(BusinessRuleViolation) as excinfo:
        resolve_sales_range(dt.date(2026, 11, 2), dt.date(2026, 11, 1), reference=REF)

    assert excinfo.value.code == "invalid_date_range"


def test_last_day_of_month_handles_december() -> None:
    assert last_day_of_month(dt.date(2026, 12, 3)) == dt.date(2026, 12, 31)


# Sales totals -----------------------------------------------------------------------------


def _amounts(count: int | None, net: str, v21: str, v105: str) -> SalesAmounts:
    return SalesAmounts(
        invoice_count=count,
        net=D(net),
        vat_21=D(v21),
        vat_10_5=D(v105),
        total=D(net) + D(v21) + D(v105),
    )


def test_sum_sales_adds_every_column() -> None:
    rows = [_amounts(2, "1600.00", "231.00", "52.50"), _amounts(1, "200.00", "0.00", "21.00")]

    totals = sum_sales(rows, count_invoices=True)

    assert totals == SalesAmounts(
        invoice_count=3,
        net=D("1800.00"),
        vat_21=D("231.00"),
        vat_10_5=D("73.50"),
        total=D("2104.50"),
    )


def test_sum_sales_without_invoice_count_leaves_it_null() -> None:
    totals = sum_sales([_amounts(None, "10.00", "2.10", "0.00")], count_invoices=False)

    assert totals.invoice_count is None
    assert totals.total == D("12.10")


def test_sum_sales_of_no_rows_is_zero() -> None:
    totals = sum_sales([], count_invoices=True)

    assert (totals.invoice_count, totals.net, totals.total) == (0, D("0.00"), D("0.00"))


# Aging ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "bucket"),
    [
        (-15, "days_0_30"),  # not yet due
        (0, "days_0_30"),
        (30, "days_0_30"),
        (31, "days_31_60"),
        (60, "days_31_60"),
        (61, "days_61_90"),
        (90, "days_61_90"),
        (91, "days_over_90"),
        (400, "days_over_90"),
    ],
)
def test_aging_bucket_for_boundaries(days: int, bucket: str) -> None:
    due = REF - dt.timedelta(days=days)

    assert days_overdue(due, REF) == days
    assert aging_bucket_for(due, REF).name == bucket


@pytest.mark.parametrize("days", range(-5, 100))
def test_due_date_ranges_match_the_bucket_rule(days: int) -> None:
    """The SQL uses due-date ranges; they must classify exactly like the days rule."""
    due = REF - dt.timedelta(days=days)
    expected = aging_bucket_for(due, REF).name

    matches = []
    for bucket in AGING_BUCKETS:
        rng = bucket.due_date_range(REF)
        if (rng.earliest is None or due >= rng.earliest) and (
            rng.latest is None or due <= rng.latest
        ):
            matches.append(rng.name)

    assert matches == [expected]


def test_sum_aging_adds_balance_and_buckets() -> None:
    rows = [
        AgingAmounts(
            balance=D("2815.00"),
            days_0_30=D("121.00"),
            days_31_60=D("605.00"),
            days_61_90=D("1089.00"),
            days_over_90=D("1000.00"),
        ),
        AgingAmounts(
            balance=D("100.00"),
            days_0_30=D("100.00"),
            days_31_60=D("0.00"),
            days_61_90=D("0.00"),
            days_over_90=D("0.00"),
        ),
    ]

    assert sum_aging(rows) == AgingAmounts(
        balance=D("2915.00"),
        days_0_30=D("221.00"),
        days_31_60=D("605.00"),
        days_61_90=D("1089.00"),
        days_over_90=D("1000.00"),
    )
