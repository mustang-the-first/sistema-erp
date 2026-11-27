from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import OrderStatus
from app.models.sales import DeliveryNote, Order
from tests.conftest import ClientAs
from tests.factories import (
    make_customer,
    make_delivery_note,
    make_invoice,
    make_order,
    make_product,
)

D = Decimal
HX = {"HX-Request": "true"}


def test_orders_list_page_shows_orders(client_as: ClientAs, db: Session) -> None:
    order = make_order(db)

    response = client_as("seller").get("/orders")

    assert response.status_code == 200
    assert f'href="/orders/{order.id}"' in response.text


def test_orders_list_page_filters_by_status(client_as: ClientAs, db: Session) -> None:
    draft = make_order(db, status=OrderStatus.DRAFT)
    confirmed = make_order(db)

    text = client_as("seller").get("/orders", params={"status": "draft", "page": 0}).text

    assert f'href="/orders/{draft.id}"' in text
    assert f'href="/orders/{confirmed.id}"' not in text


def test_orders_pages_accountant_is_forbidden(client_as: ClientAs, db: Session) -> None:
    order = make_order(db)
    accountant = client_as("accountant")

    assert accountant.get("/orders").status_code == 403
    assert accountant.get(f"/orders/{order.id}").status_code == 403
    assert accountant.post(f"/orders/{order.id}/cancel", headers=HX).status_code == 403


def test_new_order_page_lists_active_customers_and_products(
    client_as: ClientAs, db: Session
) -> None:
    customer = make_customer(db)
    product = make_product(db)
    inactive = make_product(db, is_active=False)

    text = client_as("seller").get("/orders/new").text

    assert customer.name in text
    assert product.sku in text
    assert inactive.sku not in text
    assert 'hx-post="/orders"' in text


def test_create_order_form_creates_draft_and_redirects(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    wanted = make_product(db)
    skipped = make_product(db)

    response = client_as("seller").post(
        "/orders",
        headers=HX,
        data={
            "customer_id": str(customer.id),
            "date": "2026-11-20",
            "notes": "",
            "product_id": [str(wanted.id), str(skipped.id)],
            "quantity": ["2,5", ""],
        },
    )

    order = db.scalars(select(Order).where(Order.customer_id == customer.id)).one()
    assert response.headers["HX-Redirect"] == f"/orders/{order.id}"
    assert order.status is OrderStatus.DRAFT
    assert order.notes is None
    assert [(ln.product_id, ln.quantity) for ln in order.lines] == [(wanted.id, D("2.5"))]


@pytest.mark.parametrize(
    ("quantities", "date", "message"),
    [
        (["", ""], "", "at least one line"),
        (["abc", ""], "", "Invalid quantity"),
        (["1", ""], "not-a-date", "Invalid date"),
    ],
)
def test_create_order_form_rejection_renders_flash(
    client_as: ClientAs, db: Session, quantities: list[str], date: str, message: str
) -> None:
    customer = make_customer(db)
    products = [make_product(db), make_product(db)]

    response = client_as("seller").post(
        "/orders",
        headers=HX,
        data={
            "customer_id": str(customer.id),
            "date": date,
            "product_id": [str(p.id) for p in products],
            "quantity": quantities,
        },
    )

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#flash"
    assert message in response.text
    assert db.scalars(select(Order).where(Order.customer_id == customer.id)).first() is None


def test_create_order_form_mismatched_lists_is_rejected(client_as: ClientAs, db: Session) -> None:
    response = client_as("seller").post(
        "/orders",
        headers=HX,
        data={"customer_id": str(make_customer(db).id), "product_id": ["1", "2"], "quantity": "1"},
    )

    assert "Malformed form" in response.text


def test_order_detail_draft_shows_edit_confirm_and_cancel(client_as: ClientAs, db: Session) -> None:
    order = make_order(db, status=OrderStatus.DRAFT)

    text = client_as("seller").get(f"/orders/{order.id}").text

    assert f'hx-patch="/orders/{order.id}"' in text
    assert f'hx-post="/orders/{order.id}/confirm"' in text
    assert f'hx-post="/orders/{order.id}/cancel"' in text
    assert "delivery-notes" not in text


def test_order_detail_confirmed_shows_deliver_form_and_notes(
    client_as: ClientAs, db: Session
) -> None:
    order = make_order(db, lines=[(make_product(db), D("5"))])
    note = make_delivery_note(db, order=order, quantities={order.lines[0].id: D("2")})
    invoice = make_invoice(db)

    text = client_as("seller").get(f"/orders/{order.id}").text
    invoiced_text = client_as("seller").get(f"/orders/{invoice.delivery_note.order_id}").text

    assert f'hx-post="/orders/{order.id}/delivery-notes"' in text
    assert 'value="3,000"' in text  # pending quantity prefilled
    assert f'href="/delivery-notes/{note.id}"' in text
    assert "PLACEHOLDER" not in text  # Jinja comment, never rendered
    assert "Facturado" in invoiced_text


def test_update_order_form_replaces_lines(client_as: ClientAs, db: Session) -> None:
    order = make_order(db, status=OrderStatus.DRAFT)
    product = make_product(db)

    response = client_as("seller").patch(
        f"/orders/{order.id}",
        headers=HX,
        data={"notes": "new", "product_id": [str(product.id)], "quantity": ["4"]},
    )

    db.refresh(order)
    assert response.headers["HX-Redirect"] == f"/orders/{order.id}"
    assert order.notes == "new"
    assert [ln.product_id for ln in order.lines] == [product.id]


def test_update_order_form_not_draft_renders_flash(client_as: ClientAs, db: Session) -> None:
    order = make_order(db)

    response = client_as("seller").patch(
        f"/orders/{order.id}", headers=HX, data={"notes": "x", "product_id": [], "quantity": []}
    )

    assert response.headers["HX-Retarget"] == "#flash"


def test_confirm_and_cancel_buttons_change_status(client_as: ClientAs, db: Session) -> None:
    order = make_order(db, status=OrderStatus.DRAFT)
    seller = client_as("seller")

    confirmed = seller.post(f"/orders/{order.id}/confirm", headers=HX)
    again = seller.post(f"/orders/{order.id}/confirm", headers=HX)
    cancelled = seller.post(f"/orders/{order.id}/cancel", headers=HX)
    cancel_again = seller.post(f"/orders/{order.id}/cancel", headers=HX)

    db.refresh(order)
    assert confirmed.headers["HX-Redirect"] == f"/orders/{order.id}"
    assert again.headers["HX-Retarget"] == "#flash"
    assert cancelled.headers["HX-Redirect"] == f"/orders/{order.id}"
    assert cancel_again.headers["HX-Retarget"] == "#flash"
    assert order.status is OrderStatus.CANCELLED


def test_deliver_form_creates_note_and_redirects(client_as: ClientAs, db: Session) -> None:
    order = make_order(db, lines=[(make_product(db, stock=D("10")), D("5"))])

    response = client_as("seller").post(
        f"/orders/{order.id}/delivery-notes",
        headers=HX,
        data={"date": "", "order_line_id": [str(order.lines[0].id)], "quantity": ["2"]},
    )

    note = db.scalars(select(DeliveryNote).where(DeliveryNote.order_id == order.id)).one()
    assert response.headers["HX-Redirect"] == f"/delivery-notes/{note.id}"


def test_deliver_form_insufficient_stock_renders_flash(client_as: ClientAs, db: Session) -> None:
    order = make_order(db, lines=[(make_product(db, stock=D("1")), D("5"))])

    response = client_as("seller").post(
        f"/orders/{order.id}/delivery-notes",
        headers=HX,
        data={"order_line_id": [str(order.lines[0].id)], "quantity": ["2"]},
    )

    assert response.headers["HX-Retarget"] == "#flash"
    assert "Insufficient stock" in response.text
    assert db.scalars(select(DeliveryNote).where(DeliveryNote.order_id == order.id)).first() is None


@pytest.mark.parametrize("role", ["seller", "accountant"])
def test_delivery_note_pages_readers_see_list_and_detail(
    client_as: ClientAs, db: Session, role: str
) -> None:
    order = make_order(db, lines=[(make_product(db), D("2"))])
    note = make_delivery_note(db, order=order)
    reader = client_as(role)

    listing = reader.get("/delivery-notes", params={"order_id": order.id, "invoiced": "false"})
    detail = reader.get(f"/delivery-notes/{note.id}")

    assert listing.status_code == 200
    assert f'href="/delivery-notes/{note.id}"' in listing.text
    assert detail.status_code == 200
    assert f"{note.point_of_sale:04d}-{note.number:08d}" in detail.text
    assert "2,000" in detail.text
    assert (f'href="/orders/{order.id}"' in detail.text) is (role == "seller")


def test_delivery_note_detail_unknown_is_404(client_as: ClientAs) -> None:
    assert client_as("seller").get("/delivery-notes/999999").status_code == 404
