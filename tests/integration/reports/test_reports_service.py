import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation
from app.schemas.reports import SalesAmounts, SalesGroupBy
from app.services import reports
from app.services.invoice_balance import invoice_balances_subquery
from tests.factories import make_customer, make_invoice, make_product, make_receipt
from tests.integration.reports.conftest import (
    AS_OF,
    NOV_1,
    OCT_1,
    OCT_31,
    SEP_30,
    LowStockData,
    ReceivablesData,
    SalesData,
)

D = Decimal


def _amounts(row: SalesAmounts) -> tuple[int | None, Decimal, Decimal, Decimal, Decimal]:
    return (row.invoice_count, row.net, row.vat_21, row.vat_10_5, row.total)


# Sales ------------------------------------------------------------------------------------


def test_sales_by_day_includes_both_bounds_and_sums_mixed_vat(
    db: Session, sales_data: SalesData
) -> None:
    report = reports.sales_report(db, date_from=OCT_1, date_to=OCT_31, group_by=SalesGroupBy.DAY)

    assert [(r.key, r.label) for r in report.rows] == [
        ("2026-10-01", "2026-10-01"),
        ("2026-10-31", "2026-10-31"),
    ]
    assert [_amounts(r) for r in report.rows] == [
        (2, D("1600.00"), D("231.00"), D("52.50"), D("1883.50")),
        (1, D("200.00"), D("0.00"), D("21.00"), D("221.00")),
    ]
    assert _amounts(report.totals) == (3, D("1800.00"), D("231.00"), D("73.50"), D("2104.50"))
    assert (report.date_from, report.date_to, report.group_by) == (OCT_1, OCT_31, "day")


def test_sales_by_month_groups_across_months(db: Session, sales_data: SalesData) -> None:
    report = reports.sales_report(db, date_from=SEP_30, date_to=NOV_1, group_by=SalesGroupBy.MONTH)

    assert [(r.key, *_amounts(r)) for r in report.rows] == [
        ("2026-09", 1, D("100.00"), D("21.00"), D("0.00"), D("121.00")),
        ("2026-10", 3, D("1800.00"), D("231.00"), D("73.50"), D("2104.50")),
        ("2026-11", 1, D("300.00"), D("63.00"), D("0.00"), D("363.00")),
    ]
    assert _amounts(report.totals) == (5, D("2200.00"), D("315.00"), D("73.50"), D("2588.50"))


def test_sales_by_customer_orders_by_total_desc(db: Session, sales_data: SalesData) -> None:
    report = reports.sales_report(
        db, date_from=OCT_1, date_to=OCT_31, group_by=SalesGroupBy.CUSTOMER
    )

    assert [(r.key, r.label, *_amounts(r)) for r in report.rows] == [
        (
            str(sales_data.alfa.id),
            "Alfa SA",
            2,
            D("1700.00"),
            D("210.00"),
            D("73.50"),
            D("1983.50"),
        ),
        (str(sales_data.beta.id), "Beta SRL", 1, D("100.00"), D("21.00"), D("0.00"), D("121.00")),
    ]
    assert _amounts(report.totals) == (3, D("1800.00"), D("231.00"), D("73.50"), D("2104.50"))


def test_sales_by_product_uses_line_amounts_without_invoice_count(
    db: Session, sales_data: SalesData
) -> None:
    report = reports.sales_report(
        db, date_from=OCT_1, date_to=OCT_31, group_by=SalesGroupBy.PRODUCT
    )

    assert [(r.key, r.label, *_amounts(r)) for r in report.rows] == [
        (
            str(sales_data.p21.id),
            "P21 · Widget",
            None,
            D("1100.00"),
            D("231.00"),
            D("0.00"),
            D("1331.00"),
        ),
        (
            str(sales_data.p105.id),
            "P105 · Food",
            None,
            D("700.00"),
            D("0.00"),
            D("73.50"),
            D("773.50"),
        ),
    ]
    assert _amounts(report.totals) == (None, D("1800.00"), D("231.00"), D("73.50"), D("2104.50"))


def test_sales_single_day_range_excludes_neighbouring_days(
    db: Session, sales_data: SalesData
) -> None:
    report = reports.sales_report(db, date_from=OCT_31, date_to=OCT_31)

    assert [r.key for r in report.rows] == ["2026-10-31"]
    assert report.totals.total == D("221.00")


def test_sales_without_range_defaults_to_current_month(
    db: Session, sales_data: SalesData, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reports, "today", lambda: dt.date(2026, 10, 15))

    report = reports.sales_report(db, group_by=SalesGroupBy.MONTH)

    assert (report.date_from, report.date_to) == (OCT_1, OCT_31)
    assert [r.key for r in report.rows] == ["2026-10"]


def test_sales_with_no_invoices_returns_zero_totals(db: Session) -> None:
    report = reports.sales_report(db, date_from=OCT_1, date_to=OCT_31)

    assert report.rows == []
    assert _amounts(report.totals) == (0, D("0.00"), D("0.00"), D("0.00"), D("0.00"))


def test_sales_with_from_after_to_is_rejected(db: Session) -> None:
    with pytest.raises(BusinessRuleViolation) as excinfo:
        reports.sales_report(db, date_from=OCT_31, date_to=OCT_1)

    assert excinfo.value.code == "invalid_date_range"


def test_sales_totals_match_invoice_header_vat(db: Session, sales_data: SalesData) -> None:
    """Grouping by product (lines) and by day (headers) must agree on every total."""
    by_product = reports.sales_report(
        db, date_from=SEP_30, date_to=NOV_1, group_by=SalesGroupBy.PRODUCT
    )
    by_day = reports.sales_report(db, date_from=SEP_30, date_to=NOV_1, group_by=SalesGroupBy.DAY)

    assert _amounts(by_product.totals)[1:] == _amounts(by_day.totals)[1:]


# Low stock --------------------------------------------------------------------------------


def test_low_stock_includes_equal_to_minimum_and_excludes_inactive(
    db: Session, low_stock_data: LowStockData
) -> None:
    report = reports.low_stock_report(db)

    assert report.total == 3
    assert [
        (i.sku, i.stock_qty, i.min_stock, i.shortfall, i.supplier_name) for i in report.items
    ] == [
        ("LOW-ZERO", D("0.000"), D("10.000"), D("10.000"), None),
        ("LOW-A", D("2.000"), D("5.000"), D("3.000"), "Acme"),
        ("LOW-EQ", D("5.000"), D("5.000"), D("0.000"), None),
    ]
    assert report.items[1].product_id == low_stock_data.products["LOW-A"].id


def test_low_stock_paginates_with_total(db: Session, low_stock_data: LowStockData) -> None:
    report = reports.low_stock_report(db, limit=1, offset=1)

    assert [i.sku for i in report.items] == ["LOW-A"]
    assert report.total == 3


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (201, 0), (10, -1)])
def test_low_stock_with_invalid_pagination_is_rejected(
    db: Session, limit: int, offset: int
) -> None:
    with pytest.raises(BusinessRuleViolation) as excinfo:
        reports.low_stock_report(db, limit=limit, offset=offset)

    assert excinfo.value.code == "invalid_pagination"


# Receivables ------------------------------------------------------------------------------


def test_receivables_aging_puts_each_invoice_in_its_bucket(
    db: Session, receivables_data: ReceivablesData
) -> None:
    report = reports.receivables_report(db, as_of=AS_OF)

    assert report.as_of == AS_OF
    assert [
        (
            r.customer_id,
            r.customer_name,
            r.invoice_count,
            r.days_0_30,
            r.days_31_60,
            r.days_61_90,
            r.days_over_90,
            r.balance,
        )
        for r in report.rows
    ] == [
        (
            receivables_data.alfa.id,
            "Alfa SA",
            6,
            D("121.00"),
            D("605.00"),
            D("1089.00"),
            D("1000.00"),
            D("2815.00"),
        ),
        (
            receivables_data.beta.id,
            "Beta SRL",
            1,
            D("100.00"),
            D("0.00"),
            D("0.00"),
            D("0.00"),
            D("100.00"),
        ),
    ]
    totals = report.totals
    assert (
        totals.days_0_30,
        totals.days_31_60,
        totals.days_61_90,
        totals.days_over_90,
        totals.balance,
    ) == (D("221.00"), D("605.00"), D("1089.00"), D("1000.00"), D("2915.00"))


def test_receivables_total_equals_sum_of_invoice_balances(
    db: Session, receivables_data: ReceivablesData
) -> None:
    balances = invoice_balances_subquery(as_of=AS_OF)
    expected = db.scalar(select(func.sum(balances.c.balance)))

    assert reports.receivables_report(db, as_of=AS_OF).totals.balance == expected


def test_receivables_as_of_ignores_later_invoices_and_receipts(db: Session) -> None:
    customer = make_customer(db, payment_terms_days=0)
    invoice = make_invoice(
        db, customer=customer, lines=[(make_product(db), D("1"))], invoice_date=AS_OF
    )  # 121.00
    make_receipt(
        db,
        customer=customer,
        allocations=[(invoice, D("100.00"))],
        receipt_date=AS_OF + dt.timedelta(days=40),
    )

    before_invoice = reports.receivables_report(db, as_of=AS_OF - dt.timedelta(days=1))
    before_receipt = reports.receivables_report(db, as_of=AS_OF + dt.timedelta(days=39))
    after_receipt = reports.receivables_report(db, as_of=AS_OF + dt.timedelta(days=40))

    assert before_invoice.rows == []
    assert before_invoice.totals.balance == D("0.00")
    assert [(r.balance, r.days_31_60) for r in before_receipt.rows] == [(D("121.00"), D("121.00"))]
    assert [(r.balance, r.days_31_60) for r in after_receipt.rows] == [(D("21.00"), D("21.00"))]


def test_receivables_without_as_of_defaults_to_today(
    db: Session, receivables_data: ReceivablesData, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reports, "today", lambda: AS_OF)

    report = reports.receivables_report(db)

    assert report.as_of == AS_OF
    assert report.totals.balance == D("2915.00")
