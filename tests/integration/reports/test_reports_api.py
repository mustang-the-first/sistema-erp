import pytest
from fastapi.testclient import TestClient

from tests.conftest import ClientAs
from tests.integration.reports.conftest import LowStockData, ReceivablesData, SalesData

SALES = "/api/v1/reports/sales"
LOW_STOCK = "/api/v1/reports/low-stock"
RECEIVABLES = "/api/v1/reports/receivables"


# Sales ------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["seller", "accountant", "admin"])
def test_sales_report_allowed_roles_get_decimal_strings(
    client_as: ClientAs, sales_data: SalesData, role: str
) -> None:
    response = client_as(role).get(
        SALES, params={"from": "2026-10-01", "to": "2026-10-31", "group_by": "day"}
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["from"], body["to"], body["group_by"]) == ("2026-10-01", "2026-10-31", "day")
    assert body["rows"][0] == {
        "key": "2026-10-01",
        "label": "2026-10-01",
        "invoice_count": 2,
        "net": "1600.00",
        "vat_21": "231.00",
        "vat_10_5": "52.50",
        "total": "1883.50",
    }
    assert body["totals"] == {
        "invoice_count": 3,
        "net": "1800.00",
        "vat_21": "231.00",
        "vat_10_5": "73.50",
        "total": "2104.50",
    }


def test_sales_report_by_product_has_null_invoice_count(
    client_as: ClientAs, sales_data: SalesData
) -> None:
    response = client_as("seller").get(
        SALES, params={"from": "2026-10-01", "to": "2026-10-31", "group_by": "product"}
    )

    body = response.json()
    assert [(r["label"], r["invoice_count"], r["total"]) for r in body["rows"]] == [
        ("P21 · Widget", None, "1331.00"),
        ("P105 · Food", None, "773.50"),
    ]
    assert body["totals"]["invoice_count"] is None


def test_sales_report_with_from_after_to_is_422(client_as: ClientAs) -> None:
    response = client_as("seller").get(SALES, params={"from": "2026-10-31", "to": "2026-10-01"})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_date_range"


@pytest.mark.parametrize(
    "params", [{"group_by": "week"}, {"from": "2026-13-01"}, {"to": "yesterday"}]
)
def test_sales_report_with_invalid_query_is_422(
    client_as: ClientAs, params: dict[str, str]
) -> None:
    assert client_as("accountant").get(SALES, params=params).status_code == 422


def test_sales_report_anonymous_is_401(client: TestClient) -> None:
    assert client.get(SALES).status_code == 401


# Low stock --------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["seller", "admin"])
def test_low_stock_report_allowed_roles(
    client_as: ClientAs, low_stock_data: LowStockData, role: str
) -> None:
    response = client_as(role).get(LOW_STOCK)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["items"][1] == {
        "product_id": low_stock_data.products["LOW-A"].id,
        "sku": "LOW-A",
        "name": low_stock_data.products["LOW-A"].name,
        "unit": "unit",
        "stock_qty": "2.000",
        "min_stock": "5.000",
        "shortfall": "3.000",
        "supplier_name": "Acme",
    }


def test_low_stock_report_as_accountant_is_403(client_as: ClientAs) -> None:
    response = client_as("accountant").get(LOW_STOCK)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


@pytest.mark.parametrize("params", [{"limit": "0"}, {"limit": "201"}, {"offset": "-1"}])
def test_low_stock_report_with_invalid_pagination_is_422(
    client_as: ClientAs, params: dict[str, str]
) -> None:
    assert client_as("seller").get(LOW_STOCK, params=params).status_code == 422


def test_low_stock_report_paginates(client_as: ClientAs, low_stock_data: LowStockData) -> None:
    body = client_as("seller").get(LOW_STOCK, params={"limit": 2, "offset": 2}).json()

    assert [i["sku"] for i in body["items"]] == ["LOW-EQ"]
    assert body["total"] == 3


# Receivables ------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["accountant", "admin"])
def test_receivables_report_allowed_roles(
    client_as: ClientAs, receivables_data: ReceivablesData, role: str
) -> None:
    response = client_as(role).get(RECEIVABLES, params={"as_of": "2026-11-27"})

    assert response.status_code == 200
    body = response.json()
    assert body["as_of"] == "2026-11-27"
    assert body["rows"][0] == {
        "customer_id": receivables_data.alfa.id,
        "customer_name": "Alfa SA",
        "invoice_count": 6,
        "balance": "2815.00",
        "days_0_30": "121.00",
        "days_31_60": "605.00",
        "days_61_90": "1089.00",
        "days_over_90": "1000.00",
    }
    assert body["totals"] == {
        "balance": "2915.00",
        "days_0_30": "221.00",
        "days_31_60": "605.00",
        "days_61_90": "1089.00",
        "days_over_90": "1000.00",
    }


def test_receivables_report_as_seller_is_403(client_as: ClientAs) -> None:
    response = client_as("seller").get(RECEIVABLES)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_receivables_report_with_invalid_as_of_is_422(client_as: ClientAs) -> None:
    assert client_as("accountant").get(RECEIVABLES, params={"as_of": "27/11"}).status_code == 422
