from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import JournalEntry
from app.models.enums import JournalSourceType, OrderStatus, VatRate
from tests.conftest import ClientAs
from tests.factories import make_customer, make_delivery_note, make_order, make_product

D = Decimal


def test_post_orders_seller_creates_draft(client_as: ClientAs, db: Session) -> None:
    seller = client_as("seller")
    customer = make_customer(db)
    p21 = make_product(db, unit_price=D("100.00"), vat_rate=VatRate.VAT_21)
    p105 = make_product(db, unit_price=D("100.00"), vat_rate=VatRate.VAT_10_5)

    response = seller.post(
        "/api/v1/orders",
        json={
            "customer_id": customer.id,
            "date": "2026-11-20",
            "notes": "urgent",
            "lines": [
                {"product_id": p21.id, "quantity": "10"},
                {"product_id": p105.id, "quantity": "5.000"},
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["date"] == "2026-11-20"
    assert body["customer_name"] == customer.name
    assert body["created_by"] == seller.user.id  # type: ignore[attr-defined]
    assert body["totals"] == {
        "net_total": "1500.00",
        "vat_21": "210.00",
        "vat_10_5": "52.50",
        "total": "1762.50",
    }
    first = body["lines"][0]
    assert first["product_id"] == p21.id
    assert first["unit_price"] == "100.00"
    assert first["vat_rate"] == "21.0"
    assert first["net_amount"] == "1000.00"
    assert first["vat_amount"] == "210.00"
    assert first["delivered_quantity"] == "0.000"
    assert first["pending_quantity"] == "10.000"
    assert body["delivery_notes"] == []


def test_post_orders_business_rule_returns_422_with_code(client_as: ClientAs, db: Session) -> None:
    response = client_as("seller").post(
        "/api/v1/orders", json={"customer_id": make_customer(db).id, "lines": []}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "no_lines"


def test_post_orders_unknown_customer_returns_404(client_as: ClientAs, db: Session) -> None:
    response = client_as("seller").post(
        "/api/v1/orders",
        json={
            "customer_id": 999_999,
            "lines": [{"product_id": make_product(db).id, "quantity": 1}],
        },
    )

    assert response.status_code == 404


def test_post_orders_admin_is_allowed(client_as: ClientAs, db: Session) -> None:
    response = client_as("admin").post(
        "/api/v1/orders",
        json={
            "customer_id": make_customer(db).id,
            "lines": [{"product_id": make_product(db).id, "quantity": "1"}],
        },
    )

    assert response.status_code == 201


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/orders"),
        ("post", "/api/v1/orders"),
        ("get", "/api/v1/orders/{id}"),
        ("patch", "/api/v1/orders/{id}"),
        ("post", "/api/v1/orders/{id}/confirm"),
        ("post", "/api/v1/orders/{id}/cancel"),
        ("post", "/api/v1/orders/{id}/delivery-notes"),
    ],
)
def test_orders_endpoints_accountant_is_forbidden(
    client_as: ClientAs, db: Session, method: str, path: str
) -> None:
    order = make_order(db, status=OrderStatus.DRAFT)
    accountant = client_as("accountant")
    db.commit()  # keep the arranged rows when the failed request rolls back its savepoint

    response = accountant.request(method, path.format(id=order.id), json={})

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    db.refresh(order)
    assert order.status is OrderStatus.DRAFT


def test_orders_anonymous_is_401(client: TestClient) -> None:
    assert client.get("/api/v1/orders").status_code == 401


def test_get_orders_lists_with_filters_and_pagination(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    draft = make_order(db, customer=customer, status=OrderStatus.DRAFT)
    confirmed = make_order(db, customer=customer)
    seller = client_as("seller")

    all_items = seller.get("/api/v1/orders", params={"customer_id": customer.id}).json()
    drafts = seller.get(
        "/api/v1/orders", params={"customer_id": customer.id, "status": "draft"}
    ).json()
    page = seller.get(
        "/api/v1/orders", params={"customer_id": customer.id, "limit": 1, "offset": 1}
    ).json()
    dated = seller.get(
        "/api/v1/orders",
        params={"customer_id": customer.id, "from": "2000-01-01", "to": "2000-12-31"},
    ).json()

    assert all_items["total"] == 2
    assert {i["id"] for i in all_items["items"]} == {draft.id, confirmed.id}
    assert [i["id"] for i in drafts["items"]] == [draft.id]
    assert page["total"] == 2
    assert len(page["items"]) == 1
    assert dated == {"items": [], "total": 0}
    assert set(all_items["items"][0]) >= {"number", "status", "totals", "customer_name"}


def test_get_orders_limit_over_200_is_rejected(client_as: ClientAs) -> None:
    assert client_as("seller").get("/api/v1/orders", params={"limit": 201}).status_code == 422


def test_get_order_detail_shows_delivered_and_pending(client_as: ClientAs, db: Session) -> None:
    product = make_product(db)
    order = make_order(db, lines=[(product, D("5"))], status=OrderStatus.PARTIALLY_DELIVERED)
    note = make_delivery_note(db, order=order, quantities={order.lines[0].id: D("2")})

    body = client_as("seller").get(f"/api/v1/orders/{order.id}").json()

    assert body["lines"][0]["delivered_quantity"] == "2.000"
    assert body["lines"][0]["pending_quantity"] == "3.000"
    assert body["delivery_notes"] == [
        {
            "id": note.id,
            "point_of_sale": note.point_of_sale,
            "number": note.number,
            "date": note.date.isoformat(),
        }
    ]


def test_get_order_unknown_returns_404(client_as: ClientAs) -> None:
    response = client_as("seller").get("/api/v1/orders/999999")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_patch_order_draft_updates_notes_and_lines(client_as: ClientAs, db: Session) -> None:
    order = make_order(db, status=OrderStatus.DRAFT)
    product = make_product(db, unit_price=D("7.00"))
    seller = client_as("seller")

    notes_only = seller.patch(f"/api/v1/orders/{order.id}", json={"notes": "call first"})
    lines = seller.patch(
        f"/api/v1/orders/{order.id}",
        json={"lines": [{"product_id": product.id, "quantity": "3"}]},
    )

    assert notes_only.status_code == 200
    assert notes_only.json()["notes"] == "call first"
    assert lines.status_code == 200
    assert lines.json()["notes"] == "call first"
    assert [ln["product_id"] for ln in lines.json()["lines"]] == [product.id]
    assert lines.json()["totals"]["net_total"] == "21.00"


def test_patch_order_confirmed_returns_422_invalid_status(client_as: ClientAs, db: Session) -> None:
    order = make_order(db, status=OrderStatus.CONFIRMED)

    response = client_as("seller").patch(f"/api/v1/orders/{order.id}", json={"notes": "x"})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_status"


def test_post_confirm_and_cancel_transitions(client_as: ClientAs, db: Session) -> None:
    order = make_order(db, status=OrderStatus.DRAFT)
    seller = client_as("seller")

    confirmed = seller.post(f"/api/v1/orders/{order.id}/confirm")
    confirm_again = seller.post(f"/api/v1/orders/{order.id}/confirm")
    cancelled = seller.post(f"/api/v1/orders/{order.id}/cancel")
    cancel_again = seller.post(f"/api/v1/orders/{order.id}/cancel")

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirm_again.status_code == 422
    assert confirm_again.json()["code"] == "invalid_status"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancel_again.status_code == 422
    assert cancel_again.json()["code"] == "invalid_status"


def test_post_delivery_note_creates_note_and_cogs_entry(client_as: ClientAs, db: Session) -> None:
    product = make_product(db, unit_cost=D("60.00"), stock=D("10"))
    order = make_order(db, lines=[(product, D("4"))])
    seller = client_as("seller")

    response = seller.post(
        f"/api/v1/orders/{order.id}/delivery-notes",
        json={
            "date": "2026-11-25",
            "lines": [{"order_line_id": order.lines[0].id, "quantity": "3"}],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["order_id"] == order.id
    assert body["date"] == "2026-11-25"
    assert body["invoiced"] is False
    assert body["invoice_id"] is None
    assert body["lines"][0]["quantity"] == "3.000"
    assert body["lines"][0]["product_sku"] == product.sku
    entry = db.scalars(
        select(JournalEntry).where(
            JournalEntry.source_type == JournalSourceType.DELIVERY_NOTE,
            JournalEntry.source_id == body["id"],
        )
    ).one()
    assert [(ln.debit, ln.credit) for ln in entry.lines] == [
        (D("180.00"), D("0")),
        (D("0"), D("180.00")),
    ]
    order_body = seller.get(f"/api/v1/orders/{order.id}").json()
    assert order_body["status"] == "partially_delivered"
    assert order_body["lines"][0]["pending_quantity"] == "1.000"


def test_post_delivery_note_insufficient_stock_returns_422(
    client_as: ClientAs, db: Session
) -> None:
    product = make_product(db, stock=D("1"))
    order = make_order(db, lines=[(product, D("4"))])
    seller = client_as("seller")
    db.commit()  # keep the arranged rows when the failed request rolls back its savepoint

    response = seller.post(
        f"/api/v1/orders/{order.id}/delivery-notes",
        json={"lines": [{"order_line_id": order.lines[0].id, "quantity": "2"}]},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_stock"
    body = seller.get(f"/api/v1/orders/{order.id}").json()
    assert body["delivery_notes"] == []
    assert body["status"] == "confirmed"
    db.refresh(product)
    assert product.stock_qty == D("1")


def test_post_delivery_note_over_delivery_returns_422(client_as: ClientAs, db: Session) -> None:
    order = make_order(db, lines=[(make_product(db, stock=D("10")), D("4"))])

    response = client_as("seller").post(
        f"/api/v1/orders/{order.id}/delivery-notes",
        json={"lines": [{"order_line_id": order.lines[0].id, "quantity": "5"}]},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "exceeds_pending"
