from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from tests.conftest import ClientAs
from tests.factories import make_delivery_note, make_invoice, make_order, make_product

D = Decimal


@pytest.mark.parametrize("role", ["seller", "accountant", "admin"])
def test_get_delivery_note_allowed_roles_see_detail(
    client_as: ClientAs, db: Session, role: str
) -> None:
    product = make_product(db)
    order = make_order(db, lines=[(product, D("2"))])
    note = make_delivery_note(db, order=order)

    response = client_as(role).get(f"/api/v1/delivery-notes/{note.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == note.id
    assert body["number"] == note.number
    assert body["order_number"] == order.number
    assert body["customer_id"] == order.customer_id
    assert body["invoiced"] is False
    assert body["lines"] == [
        {
            "id": note.lines[0].id,
            "order_line_id": order.lines[0].id,
            "product_id": product.id,
            "product_sku": product.sku,
            "product_name": product.name,
            "quantity": "2.000",
        }
    ]


def test_get_delivery_note_invoiced_exposes_invoice_id(client_as: ClientAs, db: Session) -> None:
    invoice = make_invoice(db)

    body = client_as("accountant").get(f"/api/v1/delivery-notes/{invoice.delivery_note_id}").json()

    assert body["invoiced"] is True
    assert body["invoice_id"] == invoice.id


def test_get_delivery_note_unknown_returns_404(client_as: ClientAs) -> None:
    assert client_as("seller").get("/api/v1/delivery-notes/999999").status_code == 404


def test_get_delivery_notes_filters_by_order_and_invoiced(client_as: ClientAs, db: Session) -> None:
    invoice = make_invoice(db)
    order = make_order(db)
    note = make_delivery_note(db, order=order)
    reader = client_as("accountant")

    by_order = reader.get("/api/v1/delivery-notes", params={"order_id": order.id}).json()
    invoiced = reader.get(
        "/api/v1/delivery-notes", params={"invoiced": "true", "limit": 200}
    ).json()
    pending = reader.get(
        "/api/v1/delivery-notes", params={"invoiced": "false", "limit": 200}
    ).json()

    assert by_order["total"] == 1
    assert [n["id"] for n in by_order["items"]] == [note.id]
    assert invoice.delivery_note_id in {n["id"] for n in invoiced["items"]}
    assert note.id not in {n["id"] for n in invoiced["items"]}
    assert note.id in {n["id"] for n in pending["items"]}
    assert all(n["invoiced"] is False for n in pending["items"])


def test_get_delivery_notes_limit_over_200_is_rejected(client_as: ClientAs) -> None:
    response = client_as("seller").get("/api/v1/delivery-notes", params={"limit": 500})

    assert response.status_code == 422
