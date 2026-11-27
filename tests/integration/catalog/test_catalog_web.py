from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalog import Customer, Product, Supplier
from tests.conftest import ClientAs
from tests.factories import make_customer, make_product, make_supplier
from tests.integration.catalog.cuits import valid_cuit

D = Decimal
HX = {"HX-Request": "true"}


@pytest.mark.parametrize("role", ["admin", "seller", "accountant"])
def test_products_page_renders_for_every_role(client_as: ClientAs, db: Session, role: str) -> None:
    make_product(db, sku="WEB-1", name="Martillo")

    response = client_as(role).get("/products")

    assert response.status_code == 200
    assert "Martillo" in response.text
    assert ("Nuevo producto" in response.text) is (role == "admin")


def test_products_partial_returns_only_the_table(client_as: ClientAs, db: Session) -> None:
    make_product(db, sku="WEB-2", name="Pinza", is_active=False)

    response = client_as("seller").get(
        "/products", params={"partial": "true", "q": "pinza", "active": "false"}, headers=HX
    )

    assert "<html" not in response.text
    assert "Pinza" in response.text


def test_create_product_form_success_flashes_and_triggers_reload(
    client_as: ClientAs, db: Session
) -> None:
    response = client_as("admin").post(
        "/products",
        data={
            "sku": "WEB-NEW",
            "name": "Destornillador",
            "unit": "unit",
            "unit_price": "100.00",
            "vat_rate": "21.0",
            "unit_cost": "55.00",
            "min_stock": "2",
            "supplier_id": "",
        },
        headers=HX,
    )

    assert response.status_code == 200
    assert "WEB-NEW creado" in response.text
    assert response.headers["HX-Trigger"] == "products-changed"
    product = db.scalars(select(Product).where(Product.sku == "WEB-NEW")).one()
    assert product.supplier_id is None


def test_create_product_form_rule_violation_renders_error(client_as: ClientAs, db: Session) -> None:
    make_product(db, sku="WEB-DUP")

    response = client_as("admin").post(
        "/products",
        data={
            "sku": "WEB-DUP",
            "name": "x",
            "unit_price": "1",
            "vat_rate": "21.0",
            "unit_cost": "1",
        },
        headers=HX,
    )

    assert response.status_code == 200
    assert "flash-error" in response.text
    assert "already in use" in response.text
    assert "HX-Trigger" not in response.headers


def test_create_product_form_validation_error_renders_error(client_as: ClientAs) -> None:
    response = client_as("admin").post(
        "/products", data={"sku": "X", "name": "x", "unit_price": "abc"}, headers=HX
    )

    assert "flash-error" in response.text
    assert "unit_price" in response.text


def test_product_edit_page_and_patch(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, name="Viejo")
    admin = client_as("admin")

    page = admin.get(f"/products/{product.id}")
    response = admin.patch(
        f"/products/{product.id}",
        data={
            "sku": product.sku,
            "name": "Nuevo",
            "unit": "unit",
            "unit_price": "10.00",
            "vat_rate": "10.5",
            "unit_cost": "5.00",
            "min_stock": "0",
            "supplier_id": "",
        },
        headers=HX,
    )

    assert page.status_code == 200 and "Viejo" in page.text
    assert "actualizado" in response.text
    db.refresh(product)
    assert (product.name, product.is_active) == ("Nuevo", False)  # unchecked switch


@pytest.mark.parametrize("role", ["seller", "accountant"])
def test_product_writes_and_edit_page_forbidden_for_non_admin(
    client_as: ClientAs, db: Session, role: str
) -> None:
    product = make_product(db)
    client = client_as(role)

    assert client.get(f"/products/{product.id}").status_code == 403
    assert client.post("/products", data={"sku": "x"}).status_code == 403
    assert client.patch(f"/products/{product.id}", data={"name": "x"}).status_code == 403


def test_web_form_without_csrf_header_is_rejected(client_as: ClientAs) -> None:
    admin = client_as("admin")
    admin.headers.pop("X-CSRF-Token")

    assert admin.post("/products", data={"sku": "x"}).status_code == 403


def test_customers_pages_and_forms_for_seller(client_as: ClientAs, db: Session) -> None:
    seller = client_as("seller")
    cuit = valid_cuit("27")

    page = seller.get("/customers")
    created = seller.post(
        "/customers",
        data={
            "name": "Cliente Web",
            "tax_condition": "responsable_inscripto",
            "cuit": f"{cuit[:2]}-{cuit[2:10]}-{cuit[10]}",
            "payment_terms_days": "30",
            "address": "",
        },
        headers=HX,
    )
    customer = db.scalars(select(Customer).where(Customer.cuit == cuit)).one()
    edit = seller.get(f"/customers/{customer.id}")
    patched = seller.patch(
        f"/customers/{customer.id}",
        data={
            "name": "Cliente Web 2",
            "tax_condition": "responsable_inscripto",
            "cuit": cuit,
            "payment_terms_days": "10",
            "is_active": "on",
        },
        headers=HX,
    )
    partial = seller.get("/customers", params={"partial": "true", "q": "Web 2"})

    assert page.status_code == 200 and "Nuevo cliente" in page.text
    assert created.headers["HX-Trigger"] == "customers-changed"
    assert edit.status_code == 200
    assert "actualizado" in patched.text
    assert "Cliente Web 2" in partial.text and "<html" not in partial.text
    assert customer.address is None


def test_customer_form_missing_cuit_renders_error(client_as: ClientAs) -> None:
    response = client_as("seller").post(
        "/customers",
        data={"name": "Sin CUIT", "tax_condition": "monotributo", "payment_terms_days": "0"},
        headers=HX,
    )

    assert "flash-error" in response.text
    assert "CUIT is required" in response.text


def test_customer_pages_forbidden_for_accountant(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    accountant = client_as("accountant")

    assert accountant.get("/customers").status_code == 403
    assert accountant.get(f"/customers/{customer.id}").status_code == 403


def test_suppliers_pages_and_forms_for_admin(client_as: ClientAs, db: Session) -> None:
    admin = client_as("admin")
    cuit = valid_cuit("33")
    existing = make_supplier(db, name="Proveedor Existente")

    page = admin.get("/suppliers")
    created = admin.post(
        "/suppliers",
        data={"name": "Prov Web", "cuit": cuit, "tax_condition": "exento", "email": ""},
        headers=HX,
    )
    edit = admin.get(f"/suppliers/{existing.id}")
    patched = admin.patch(
        f"/suppliers/{existing.id}",
        data={"name": "Proveedor Renombrado", "cuit": cuit, "tax_condition": "exento"},
        headers=HX,
    )
    partial = admin.get("/suppliers", params={"partial": "true", "active": "all"})

    assert "Proveedor Existente" in page.text
    assert "Prov Web creado" in created.text
    assert db.scalars(select(Supplier).where(Supplier.cuit == cuit)).one().name == "Prov Web"
    assert edit.status_code == 200
    assert "flash-error" in patched.text and "already registered" in patched.text
    assert "Prov Web" in partial.text


@pytest.mark.parametrize("role", ["seller", "accountant"])
def test_supplier_pages_forbidden_for_non_admin(client_as: ClientAs, role: str) -> None:
    assert client_as(role).get("/suppliers").status_code == 403


def test_missing_product_page_returns_404(client_as: ClientAs) -> None:
    assert client_as("admin").get("/products/999999").status_code == 404


def test_rejected_form_leaves_no_partial_writes(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, sku="KEEP-1", unit_price=D("5.00"))
    make_product(db, sku="KEEP-2")

    response = client_as("admin").patch(
        f"/products/{product.id}",
        data={
            "sku": "KEEP-2",
            "name": "x",
            "unit": "u",
            "unit_price": "9.00",
            "vat_rate": "21.0",
            "unit_cost": "1",
            "min_stock": "0",
        },
        headers=HX,
    )

    assert "flash-error" in response.text
    db.expire_all()
    stored = db.get(Product, product.id)
    assert stored is not None
    assert (stored.sku, stored.unit_price) == ("KEEP-1", D("5.00"))
