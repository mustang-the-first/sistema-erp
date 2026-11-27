from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.conftest import ClientAs
from tests.factories import make_product

D = Decimal
HX = {"HX-Request": "true"}


@pytest.mark.parametrize("role", ["admin", "seller", "accountant"])
def test_stock_page_renders_movements_for_every_role(
    client_as: ClientAs, db: Session, role: str
) -> None:
    make_product(db, sku="STK-WEB", stock=D("7"))

    response = client_as(role).get("/stock")

    assert response.status_code == 200
    assert "STK-WEB" in response.text
    assert ("Nuevo ajuste de stock" in response.text) is (role == "admin")


def test_stock_partial_applies_filters(client_as: ClientAs, db: Session) -> None:
    shown = make_product(db, sku="STK-A", stock=D("1"))
    make_product(db, sku="STK-B", stock=D("1"))

    response = client_as("seller").get(
        "/stock",
        params={
            "partial": "true",
            "product_id": str(shown.id),
            "from": "2000-01-01",
            "to": "",
            "kind": "adjustment",
        },
    )

    assert "<html" not in response.text
    assert "STK-A" in response.text
    assert "STK-B" not in response.text


def test_stock_page_invalid_date_returns_422(client_as: ClientAs) -> None:
    response = client_as("seller").get("/stock", params={"from": "not-a-date"})

    assert (response.status_code, response.json()["code"]) == (422, "invalid_date")


def test_adjustment_form_success_flashes_entry_number(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, unit_cost=D("10.00"))

    response = client_as("admin").post(
        "/stock/adjustments",
        data={
            "product_id": str(product.id),
            "reason": "initial_load",
            "quantity": "3",
            "date": "",
            "note": "",
        },
        headers=HX,
    )

    assert "registrado (asiento N°" in response.text
    assert response.headers["HX-Trigger"] == "stock-changed"
    db.refresh(product)
    assert product.stock_qty == D("3")


def test_adjustment_form_zero_cost_says_no_entry(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, unit_cost=D("0"))

    response = client_as("admin").post(
        "/stock/adjustments",
        data={"product_id": str(product.id), "reason": "initial_load", "quantity": "1"},
        headers=HX,
    )

    assert "sin asiento" in response.text


def test_adjustment_form_insufficient_stock_renders_error_and_keeps_stock(
    client_as: ClientAs, db: Session
) -> None:
    product = make_product(db, stock=D("1"))

    response = client_as("admin").post(
        "/stock/adjustments",
        data={"product_id": str(product.id), "reason": "damage", "quantity": "-5"},
        headers=HX,
    )

    assert "flash-error" in response.text
    assert "Insufficient stock" in response.text
    db.refresh(product)
    assert product.stock_qty == D("1")


@pytest.mark.parametrize("role", ["seller", "accountant"])
def test_adjustment_form_forbidden_for_non_admin(
    client_as: ClientAs, db: Session, role: str
) -> None:
    product = make_product(db)

    response = client_as(role).post(
        "/stock/adjustments",
        data={"product_id": str(product.id), "reason": "initial_load", "quantity": "1"},
    )

    assert response.status_code == 403
