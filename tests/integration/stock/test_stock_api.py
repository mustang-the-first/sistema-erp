from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import JournalEntry
from app.models.catalog import Product
from tests.conftest import ClientAs
from tests.factories import make_product

D = Decimal
API = "/api/v1"


def test_post_adjustment_returns_movement_and_entry_number(
    client_as: ClientAs, db: Session
) -> None:
    product = make_product(db, unit_cost=D("25.00"))
    admin = client_as("admin")

    response = admin.post(
        f"{API}/stock/adjustments",
        json={
            "product_id": product.id,
            "quantity": "8",
            "reason": "initial_load",
            "note": "Alta",
            "date": "2026-11-20",
        },
    )

    assert response.status_code == 201
    body = response.json()
    movement = body["movement"]
    assert movement["quantity"] == "8.000"
    assert movement["unit_cost"] == "25.00"
    assert movement["value"] == "200.00"
    assert (movement["kind"], movement["reason"], movement["date"]) == (
        "adjustment",
        "initial_load",
        "2026-11-20",
    )
    assert movement["created_by"] == admin.user.id  # type: ignore[attr-defined]
    entry = db.scalars(
        select(JournalEntry).where(JournalEntry.number == body["journal_entry_number"])
    ).one()
    assert {(line.account.code, line.debit, line.credit) for line in entry.lines} == {
        ("1.1.4.01", D("200.00"), D("0")),
        ("3.1.01", D("0"), D("200.00")),
    }
    db.refresh(product)
    assert product.stock_qty == D("8")


def test_post_adjustment_zero_cost_returns_null_entry(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, unit_cost=D("0"))

    response = client_as("admin").post(
        f"{API}/stock/adjustments",
        json={"product_id": product.id, "quantity": "1", "reason": "initial_load"},
    )

    assert response.status_code == 201
    assert response.json()["journal_entry_number"] is None


def test_post_adjustment_insufficient_stock_returns_422(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, stock=D("1"))

    response = client_as("admin").post(
        f"{API}/stock/adjustments",
        json={"product_id": product.id, "quantity": "-2", "reason": "damage"},
    )

    assert (response.status_code, response.json()["code"]) == (422, "insufficient_stock")
    db.refresh(product)
    assert product.stock_qty == D("1")


def test_post_adjustment_unknown_product_returns_404(client_as: ClientAs) -> None:
    response = client_as("admin").post(
        f"{API}/stock/adjustments",
        json={"product_id": 999999, "quantity": "1", "reason": "initial_load"},
    )

    assert response.status_code == 404


@pytest.mark.parametrize("role", ["seller", "accountant"])
def test_post_adjustment_non_admin_returns_403(client_as: ClientAs, db: Session, role: str) -> None:
    product = make_product(db)

    response = client_as(role).post(
        f"{API}/stock/adjustments",
        json={"product_id": product.id, "quantity": "1", "reason": "initial_load"},
    )

    assert response.status_code == 403
    assert db.get(Product, product.id).stock_qty == 0  # type: ignore[union-attr]


def test_stock_listings_readable_by_admin(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, stock=D("4"))
    client = client_as("admin")

    movements = client.get(f"{API}/stock/movements", params={"product_id": product.id})
    adjustments = client.get(f"{API}/stock/adjustments", params={"product_id": product.id})

    assert movements.status_code == 200
    assert movements.json()["total"] == 1
    assert movements.json()["items"][0]["product_sku"] == product.sku
    assert adjustments.status_code == 200
    assert adjustments.json()["items"][0]["movement"]["reason"] == "initial_load"


def test_list_movements_date_and_kind_filters(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, unit_cost=D("1.00"))
    admin = client_as("admin")
    for day, qty in (("2026-11-01", "5"), ("2026-11-10", "-1")):
        reason = "initial_load" if qty == "5" else "damage"
        admin.post(
            f"{API}/stock/adjustments",
            json={"product_id": product.id, "quantity": qty, "reason": reason, "date": day},
        )

    window = admin.get(
        f"{API}/stock/movements",
        params={"product_id": product.id, "from": "2026-11-05", "to": "2026-11-30"},
    ).json()
    deliveries = admin.get(
        f"{API}/stock/movements", params={"product_id": product.id, "kind": "delivery"}
    ).json()
    damages = admin.get(
        f"{API}/stock/adjustments", params={"product_id": product.id, "reason": "damage"}
    ).json()

    assert [m["quantity"] for m in window["items"]] == ["-1.000"]
    assert deliveries == {"items": [], "total": 0}
    assert damages["total"] == 1


@pytest.mark.parametrize("role", ["seller", "accountant"])
@pytest.mark.parametrize("path", ["/stock/movements", "/stock/adjustments"])
def test_stock_listings_forbidden_for_non_admins(client_as: ClientAs, role: str, path: str) -> None:
    response = client_as(role).get(f"{API}{path}")

    assert (response.status_code, response.json()["code"]) == (403, "forbidden")


def test_list_movements_inverted_range_returns_422(client_as: ClientAs) -> None:
    response = client_as("admin").get(
        f"{API}/stock/movements", params={"from": "2026-11-05", "to": "2026-11-01"}
    )

    assert (response.status_code, response.json()["code"]) == (422, "invalid_date_range")


def test_stock_endpoints_anonymous_returns_401(client: object) -> None:
    from fastapi.testclient import TestClient

    assert isinstance(client, TestClient)
    assert client.get(f"{API}/stock/movements").status_code == 401
