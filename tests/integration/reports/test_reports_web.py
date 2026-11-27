import pytest

from tests.conftest import ClientAs
from tests.integration.reports.conftest import LowStockData, ReceivablesData, SalesData

HTMX = {"HX-Request": "true"}


def test_sales_page_renders_full_layout_with_ars_amounts(
    client_as: ClientAs, sales_data: SalesData
) -> None:
    response = client_as("seller").get(
        "/reports/sales", params={"from": "2026-10-01", "to": "2026-10-31", "group_by": "customer"}
    )

    assert response.status_code == 200
    html = response.text
    assert "<html" in html
    assert 'id="sales-filters"' in html
    assert "Alfa SA" in html
    assert "1.983,50" in html  # Alfa total
    assert "2.104,50" in html  # grand total
    assert 'value="2026-10-01"' in html


def test_sales_page_htmx_request_returns_only_the_table(
    client_as: ClientAs, sales_data: SalesData
) -> None:
    response = client_as("accountant").get(
        "/reports/sales",
        params={"from": "2026-10-01", "to": "2026-10-31", "group_by": "product"},
        headers=HTMX,
    )

    assert response.status_code == 200
    assert "<html" not in response.text
    assert 'id="sales-table"' in response.text
    assert "P105 · Food" in response.text
    assert "773,50" in response.text
    assert "Facturas" not in response.text  # no invoice count when grouping by product


def test_sales_page_with_blank_dates_uses_default_month(client_as: ClientAs) -> None:
    response = client_as("seller").get(
        "/reports/sales", params={"from": "", "to": "", "group_by": "month"}
    )

    assert response.status_code == 200
    assert "Sin facturas en el período." in response.text


def test_sales_page_with_from_after_to_shows_error(client_as: ClientAs) -> None:
    response = client_as("seller").get(
        "/reports/sales", params={"from": "2026-10-31", "to": "2026-10-01"}, headers=HTMX
    )

    assert response.status_code == 200
    assert 'role="alert"' in response.text
    assert "must not be after" in response.text


def test_low_stock_page_lists_products(client_as: ClientAs, low_stock_data: LowStockData) -> None:
    response = client_as("seller").get("/reports/low-stock")

    assert response.status_code == 200
    assert "LOW-ZERO" in response.text
    assert "Acme" in response.text
    assert "INACTIVE" not in response.text
    assert ">OK<" not in response.text


def test_receivables_page_shows_buckets(
    client_as: ClientAs, receivables_data: ReceivablesData
) -> None:
    response = client_as("accountant").get("/reports/receivables", params={"as_of": "2026-11-27"})

    assert response.status_code == 200
    html = response.text
    assert "Alfa SA" in html
    assert "1.089,00" in html
    assert "2.915,00" in html
    assert "Gamma SA" not in html


def test_admin_sees_every_report_tab(client_as: ClientAs) -> None:
    html = client_as("admin").get("/reports/receivables").text

    assert 'href="/reports/sales"' in html
    assert 'href="/reports/low-stock"' in html


def test_seller_tabs_hide_receivables(client_as: ClientAs) -> None:
    html = client_as("seller").get("/reports/sales").text

    assert 'href="/reports/low-stock"' in html
    assert 'href="/reports/receivables"' not in html


@pytest.mark.parametrize(
    ("role", "path"), [("seller", "/reports/receivables"), ("accountant", "/reports/low-stock")]
)
def test_report_page_for_wrong_role_is_403(client_as: ClientAs, role: str, path: str) -> None:
    assert client_as(role).get(path).status_code == 403
