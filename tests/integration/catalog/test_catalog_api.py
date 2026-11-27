from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models.catalog import Product
from tests.conftest import ClientAs
from tests.factories import make_customer, make_product, make_supplier
from tests.integration.catalog.cuits import valid_cuit

D = Decimal
API = "/api/v1"


def product_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "sku": "API-001",
        "name": "Arandela",
        "unit": "unit",
        "unit_price": "1.50",
        "vat_rate": "10.5",
        "unit_cost": "0.80",
        "min_stock": "50",
    } | overrides


# Products ---------------------------------------------------------------------------------


def test_create_product_as_admin_returns_201_with_decimal_strings(client_as: ClientAs) -> None:
    admin = client_as("admin")

    response = admin.post(f"{API}/products", json=product_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["sku"] == "API-001"
    assert body["unit_price"] == "1.50"
    assert body["stock_qty"] == "0.000"
    assert body["vat_rate"] == "10.5"
    assert body["is_active"] is True


@pytest.mark.parametrize("role", ["seller", "accountant"])
def test_product_writes_by_non_admin_return_403(
    client_as: ClientAs, db: Session, role: str
) -> None:
    product = make_product(db)
    client = client_as(role)

    assert client.post(f"{API}/products", json=product_payload()).status_code == 403
    assert client.patch(f"{API}/products/{product.id}", json={"name": "x"}).status_code == 403


@pytest.mark.parametrize("role", ["admin", "seller", "accountant"])
def test_products_are_readable_by_every_role(client_as: ClientAs, db: Session, role: str) -> None:
    product = make_product(db, sku="READ-1", stock=D("3"))
    client = client_as(role)

    listing = client.get(f"{API}/products", params={"q": "read-1"})
    detail = client.get(f"{API}/products/{product.id}")

    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["stock_qty"] == "3.000"
    assert detail.json()["id"] == product.id


def test_products_anonymous_returns_401(client: Any) -> None:
    assert client.get(f"{API}/products").status_code == 401


def test_list_products_active_filter_and_pagination(client_as: ClientAs, db: Session) -> None:
    make_product(db, sku="PG-1")
    make_product(db, sku="PG-2")
    make_product(db, sku="PG-3", is_active=False)
    client = client_as("seller")

    active = client.get(f"{API}/products", params={"q": "PG-", "active": "true"}).json()
    page = client.get(f"{API}/products", params={"q": "PG-", "limit": 1, "offset": 2}).json()

    assert [p["sku"] for p in active["items"]] == ["PG-1", "PG-2"]
    assert page == {"items": [page["items"][0]], "total": 3}
    assert page["items"][0]["sku"] == "PG-3"


@pytest.mark.parametrize("params", [{"limit": 201}, {"limit": 0}, {"offset": -1}])
def test_list_products_bad_pagination_returns_422(
    client_as: ClientAs, params: dict[str, int]
) -> None:
    assert client_as("seller").get(f"{API}/products", params=params).status_code == 422


def test_create_product_duplicate_sku_returns_422_sku_taken(
    client_as: ClientAs, db: Session
) -> None:
    make_product(db, sku="API-001")

    response = client_as("admin").post(f"{API}/products", json=product_payload())

    assert response.status_code == 422
    assert response.json()["code"] == "sku_taken"


def test_create_product_negative_price_returns_422_invalid_amount(client_as: ClientAs) -> None:
    response = client_as("admin").post(f"{API}/products", json=product_payload(unit_price="-1.00"))

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_amount"


def test_create_product_inactive_supplier_returns_422(client_as: ClientAs, db: Session) -> None:
    supplier = make_supplier(db)
    supplier.is_active = False
    db.flush()

    response = client_as("admin").post(
        f"{API}/products", json=product_payload(supplier_id=supplier.id)
    )

    assert response.json()["code"] == "invalid_supplier"


def test_patch_product_updates_and_soft_deletes(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, unit_price=D("100.00"))

    response = client_as("admin").patch(
        f"{API}/products/{product.id}", json={"unit_price": "120.00", "is_active": False}
    )

    assert response.status_code == 200
    assert (response.json()["unit_price"], response.json()["is_active"]) == ("120.00", False)


def test_patch_product_stock_qty_is_rejected(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, stock=D("5"))

    response = client_as("admin").patch(f"{API}/products/{product.id}", json={"stock_qty": "99"})

    assert response.status_code == 422
    db.refresh(product)
    assert product.stock_qty == D("5")


def test_product_missing_returns_404(client_as: ClientAs) -> None:
    admin = client_as("admin")

    assert admin.get(f"{API}/products/999999").status_code == 404
    response = admin.patch(f"{API}/products/999999", json={"name": "x"})
    assert (response.status_code, response.json()["code"]) == (404, "not_found")


def test_product_has_no_delete_endpoint(client_as: ClientAs, db: Session) -> None:
    product = make_product(db)

    assert client_as("admin").delete(f"{API}/products/{product.id}").status_code == 405
    assert db.get(Product, product.id) is not None


# Customers --------------------------------------------------------------------------------


def customer_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Kiosco Don Pepe",
        "cuit": valid_cuit("27", dashed=True),
        "tax_condition": "monotributo",
        "address": "Calle 1",
        "email": "pepe@example.com",
        "phone": "123",
        "payment_terms_days": 15,
    } | overrides


@pytest.mark.parametrize("role", ["seller", "admin"])
def test_customer_crud_for_seller_and_admin(client_as: ClientAs, role: str) -> None:
    client = client_as(role)
    payload = customer_payload()

    created = client.post(f"{API}/customers", json=payload)
    customer_id = created.json()["id"]
    patched = client.patch(f"{API}/customers/{customer_id}", json={"payment_terms_days": 45})
    fetched = client.get(f"{API}/customers/{customer_id}")
    listing = client.get(f"{API}/customers", params={"q": "don pepe"})

    assert created.status_code == 201
    assert created.json()["cuit"] == payload["cuit"].replace("-", "")
    assert patched.json()["payment_terms_days"] == 45
    assert fetched.json()["name"] == "Kiosco Don Pepe"
    assert listing.json()["total"] == 1


def test_customer_endpoints_forbidden_for_accountant(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    accountant = client_as("accountant")

    assert accountant.get(f"{API}/customers").status_code == 403
    assert accountant.post(f"{API}/customers", json=customer_payload()).status_code == 403
    assert accountant.get(f"{API}/customers/{customer.id}").status_code == 403
    response = accountant.patch(f"{API}/customers/{customer.id}", json={"name": "x"})
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"cuit": None, "tax_condition": "responsable_inscripto"}, "cuit_required"),
        ({"cuit": "20-12345678-9"}, "invalid_cuit"),
        ({"payment_terms_days": -5}, "invalid_payment_terms"),
    ],
)
def test_create_customer_rule_violations_return_422(
    client_as: ClientAs, overrides: dict[str, Any], code: str
) -> None:
    response = client_as("seller").post(f"{API}/customers", json=customer_payload(**overrides))

    assert response.status_code == 422
    assert response.json()["code"] == code


def test_create_customer_duplicate_cuit_returns_cuit_taken(client_as: ClientAs) -> None:
    seller = client_as("seller")
    payload = customer_payload()
    seller.post(f"{API}/customers", json=payload)

    response = seller.post(f"{API}/customers", json=payload | {"name": "Otro"})

    assert response.json()["code"] == "cuit_taken"


def test_create_final_consumer_without_cuit_returns_201(client_as: ClientAs) -> None:
    response = client_as("seller").post(
        f"{API}/customers", json=customer_payload(cuit=None, tax_condition="consumidor_final")
    )

    assert response.status_code == 201
    assert response.json()["cuit"] is None


def test_customer_missing_returns_404(client_as: ClientAs) -> None:
    assert client_as("seller").get(f"{API}/customers/999999").status_code == 404


# Suppliers --------------------------------------------------------------------------------


def test_supplier_crud_as_admin(client_as: ClientAs) -> None:
    admin = client_as("admin")
    cuit = valid_cuit("33", dashed=True)

    created = admin.post(
        f"{API}/suppliers",
        json={"name": "Distribuidora Sur", "cuit": cuit, "tax_condition": "responsable_inscripto"},
    )
    supplier_id = created.json()["id"]
    patched = admin.patch(f"{API}/suppliers/{supplier_id}", json={"is_active": False})
    fetched = admin.get(f"{API}/suppliers/{supplier_id}")
    listing = admin.get(f"{API}/suppliers", params={"active": "false", "q": "Distribuidora Sur"})

    assert created.status_code == 201
    assert created.json()["cuit"] == cuit.replace("-", "")
    assert patched.json()["is_active"] is False
    assert fetched.status_code == 200
    assert listing.json()["total"] == 1


@pytest.mark.parametrize("role", ["seller", "accountant"])
def test_supplier_endpoints_forbidden_for_non_admin(
    client_as: ClientAs, db: Session, role: str
) -> None:
    supplier = make_supplier(db)
    client = client_as(role)
    payload = {"name": "X", "cuit": valid_cuit("33"), "tax_condition": "exento"}

    assert client.get(f"{API}/suppliers").status_code == 403
    assert client.post(f"{API}/suppliers", json=payload).status_code == 403
    assert client.get(f"{API}/suppliers/{supplier.id}").status_code == 403
    assert client.patch(f"{API}/suppliers/{supplier.id}", json={"name": "y"}).status_code == 403


def test_create_supplier_invalid_cuit_returns_422(client_as: ClientAs) -> None:
    response = client_as("admin").post(
        f"{API}/suppliers",
        json={"name": "X", "cuit": "30-00000000-1", "tax_condition": "exento"},
    )

    assert (response.status_code, response.json()["code"]) == (422, "invalid_cuit")


def test_supplier_missing_returns_404(client_as: ClientAs) -> None:
    assert (
        client_as("admin").patch(f"{API}/suppliers/999999", json={"name": "x"}).status_code == 404
    )
