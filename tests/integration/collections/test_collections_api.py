import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import ClientAs
from tests.factories import make_customer, make_receipt
from tests.integration.collections.helpers import DAY, D, invoice_of, receipt_entry_lines

API = "/api/v1"


@pytest.mark.parametrize("role", ["seller", "accountant", "admin"])
def test_post_receipt_allowed_roles_create_it(client_as: ClientAs, db: Session, role: str) -> None:
    customer = make_customer(db)
    a = invoice_of(db, customer)  # 121.00
    b = invoice_of(db, customer, "50.00")  # 60.50
    client = client_as(role)

    response = client.post(
        f"{API}/receipts",
        json={
            "customer_id": customer.id,
            "date": DAY.isoformat(),
            "method": "bank_transfer",
            "reference": "TRF 123",
            "allocations": [
                {"invoice_id": a.id, "amount": "121.00"},
                {"invoice_id": b.id, "amount": "10.5"},
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["amount"] == "131.50"
    assert body["method"] == "bank_transfer"
    assert body["reference"] == "TRF 123"
    assert body["date"] == "2026-11-10"
    assert body["customer_name"] == customer.name
    assert body["created_by"] == client.user.id  # type: ignore[attr-defined]
    assert body["document_number"] == f"0001-{body['number']:08d}"
    assert [(x["invoice_id"], x["amount"]) for x in body["allocations"]] == [
        (a.id, "121.00"),
        (b.id, "10.50"),
    ]
    assert body["allocations"][0]["invoice_number"] == f"A 0001-{a.number:08d}"
    assert receipt_entry_lines(db, body["id"]) == {
        "1.1.1.02": (D("131.50"), D("0.00")),
        "1.1.3.01": (D("0.00"), D("131.50")),
    }


def test_post_receipt_overpayment_is_422_with_code(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)

    response = client_as("seller").post(
        f"{API}/receipts",
        json={
            "customer_id": customer.id,
            "method": "cash",
            "allocations": [{"invoice_id": inv.id, "amount": "500.00"}],
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "overpayment"


def test_post_receipt_foreign_invoice_is_422_with_code(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    foreign = invoice_of(db, make_customer(db))

    response = client_as("accountant").post(
        f"{API}/receipts",
        json={
            "customer_id": customer.id,
            "method": "cash",
            "allocations": [{"invoice_id": foreign.id, "amount": "1.00"}],
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invoice_not_owned"


def test_post_receipt_empty_allocations_is_422_with_code(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)

    response = client_as("seller").post(
        f"{API}/receipts",
        json={"customer_id": customer.id, "method": "cash", "allocations": []},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "no_allocations"


def test_post_receipt_unknown_customer_is_404(client_as: ClientAs) -> None:
    response = client_as("seller").post(
        f"{API}/receipts",
        json={
            "customer_id": 999_999,
            "method": "cash",
            "allocations": [{"invoice_id": 1, "amount": "1.00"}],
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "customer_not_found"


def test_post_receipt_bad_method_is_422(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)

    response = client_as("seller").post(
        f"{API}/receipts",
        json={"customer_id": customer.id, "method": "cheque", "allocations": []},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/receipts"),
        ("get", "/receipts"),
        ("get", "/receipts/1"),
        ("get", "/customers/1/open-invoices"),
        ("get", "/customers/1/statement"),
    ],
)
def test_collections_api_anonymous_is_401(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, f"{API}{path}", json={})

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_get_receipts_lists_and_filters(client_as: ClientAs, db: Session) -> None:
    customer, other = make_customer(db), make_customer(db)
    inv = invoice_of(db, customer, day=dt.date(2026, 10, 1))
    other_inv = invoice_of(db, other, day=dt.date(2026, 10, 1))
    old = make_receipt(
        db, customer=customer, allocations=[(inv, D("1.00"))], receipt_date=dt.date(2026, 10, 2)
    )
    new = make_receipt(
        db, customer=customer, allocations=[(inv, D("2.00"))], receipt_date=dt.date(2026, 11, 2)
    )
    make_receipt(db, customer=other, allocations=[(other_inv, D("3.00"))])
    client = client_as("accountant")

    all_of_customer = client.get(f"{API}/receipts", params={"customer_id": customer.id}).json()
    november = client.get(
        f"{API}/receipts",
        params={"customer_id": customer.id, "from": "2026-11-01", "to": "2026-11-30"},
    ).json()
    paged = client.get(
        f"{API}/receipts", params={"customer_id": customer.id, "limit": 1, "offset": 1}
    ).json()

    assert [r["id"] for r in all_of_customer["items"]] == [new.id, old.id]
    assert all_of_customer["total"] == 2
    assert all_of_customer["items"][0]["amount"] == "2.00"
    assert [r["id"] for r in november["items"]] == [new.id]
    assert [r["id"] for r in paged["items"]] == [old.id]
    assert paged["total"] == 2


def test_get_receipts_limit_above_max_is_422(client_as: ClientAs) -> None:
    assert client_as("seller").get(f"{API}/receipts", params={"limit": 201}).status_code == 422


def test_get_receipt_returns_allocations(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)
    receipt = make_receipt(db, customer=customer, allocations=[(inv, D("21.00"))])

    body = client_as("seller").get(f"{API}/receipts/{receipt.id}").json()

    assert body["id"] == receipt.id
    assert body["allocations"] == [
        {
            "id": receipt.allocations[0].id,
            "invoice_id": inv.id,
            "invoice_number": f"A 0001-{inv.number:08d}",
            "invoice_date": inv.date.isoformat(),
            "invoice_total": "121.00",
            "amount": "21.00",
        }
    ]


def test_get_receipt_unknown_is_404(client_as: ClientAs) -> None:
    response = client_as("seller").get(f"{API}/receipts/999999")

    assert response.status_code == 404
    assert response.json()["code"] == "receipt_not_found"


def test_get_open_invoices_returns_balances(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)
    paid = invoice_of(db, customer)
    make_receipt(db, customer=customer, allocations=[(inv, D("21.00")), (paid, D("121.00"))])

    body = client_as("accountant").get(f"{API}/customers/{customer.id}/open-invoices").json()

    assert body["customer_id"] == customer.id
    assert body["total"] == 1
    [item] = body["items"]
    assert item["invoice_id"] == inv.id
    assert item["document_number"] == f"A 0001-{inv.number:08d}"
    assert (item["total"], item["paid"], item["balance"]) == ("121.00", "21.00", "100.00")
    assert item["date"] == DAY.isoformat()
    assert item["due_date"] == (DAY + dt.timedelta(days=30)).isoformat()
    assert isinstance(item["days_overdue"], int)


def test_get_open_invoices_unknown_customer_is_404(client_as: ClientAs) -> None:
    assert client_as("seller").get(f"{API}/customers/999999/open-invoices").status_code == 404


def test_get_statement_returns_opening_running_and_closing(
    client_as: ClientAs, db: Session
) -> None:
    customer = make_customer(db)
    oct_inv = invoice_of(db, customer, day=dt.date(2026, 10, 1))
    nov_inv = invoice_of(db, customer, "200.00", day=dt.date(2026, 11, 10))
    make_receipt(
        db,
        customer=customer,
        allocations=[(oct_inv, D("100.00"))],
        receipt_date=dt.date(2026, 10, 15),
    )
    receipt = make_receipt(
        db,
        customer=customer,
        allocations=[(nov_inv, D("50.00"))],
        receipt_date=dt.date(2026, 11, 20),
    )

    body = (
        client_as("seller")
        .get(f"{API}/customers/{customer.id}/statement", params={"from": "2026-11-01"})
        .json()
    )

    assert body["customer_id"] == customer.id
    assert body["from"] == "2026-11-01"
    assert body["to"] is None
    assert body["opening_balance"] == "21.00"
    assert [
        (m["kind"], m["document_id"], m["debit"], m["credit"], m["balance"])
        for m in body["movements"]
    ] == [
        ("invoice", nov_inv.id, "242.00", "0.00", "263.00"),
        ("receipt", receipt.id, "0.00", "50.00", "213.00"),
    ]
    assert (body["total_debit"], body["total_credit"]) == ("242.00", "50.00")
    assert body["closing_balance"] == "213.00"


def test_get_statement_inverted_range_is_422(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)

    response = client_as("seller").get(
        f"{API}/customers/{customer.id}/statement",
        params={"from": "2026-11-30", "to": "2026-11-01"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_date_range"
