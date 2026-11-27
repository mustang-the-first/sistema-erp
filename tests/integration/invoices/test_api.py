import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.enums import TaxCondition
from app.services.invoices import LEY_27618_LEGEND
from tests.conftest import ClientAs
from tests.factories import make_customer, make_invoice, make_receipt
from tests.integration.invoices.helpers import delivered_note, mixed_vat_lines

D = Decimal
T = TaxCondition


def issue_url(note_id: int) -> str:
    return f"/api/v1/delivery-notes/{note_id}/invoice"


# POST /delivery-notes/{id}/invoice --------------------------------------------------------


def test_post_invoice_as_seller_returns_201_with_detail(client_as: ClientAs, db: Session) -> None:
    seller = client_as("seller")
    note = delivered_note(db, lines=mixed_vat_lines(db))

    response = seller.post(issue_url(note.id), json={"date": "2026-11-05"})

    assert response.status_code == 201
    body = response.json()
    assert body["letter"] == "A"
    assert body["formatted_number"] == "0001-00000001"
    assert body["date"] == "2026-11-05"
    assert (body["net_total"], body["vat_21"], body["vat_10_5"], body["total"]) == (
        "1500.00",
        "210.00",
        "52.50",
        "1762.50",
    )
    assert (body["balance"], body["status"]) == ("1762.50", "open")
    assert body["created_by"] == seller.user.id  # type: ignore[attr-defined]
    assert [line["net_amount"] for line in body["lines"]] == ["1000.00", "500.00"]
    assert body["legend"] is None


def test_post_invoice_without_body_defaults_date(client_as: ClientAs, db: Session) -> None:
    note = delivered_note(db, note_date=dt.date(2020, 1, 1))

    response = client_as("seller").post(issue_url(note.id))

    assert response.status_code == 201
    assert response.json()["date"] >= "2026-01-01"


def test_post_invoice_as_admin_is_allowed(client_as: ClientAs, db: Session) -> None:
    note = delivered_note(db)

    assert client_as("admin").post(issue_url(note.id), json={}).status_code == 201


def test_post_invoice_as_accountant_is_403(client_as: ClientAs, db: Session) -> None:
    note = delivered_note(db)

    response = client_as("accountant").post(issue_url(note.id), json={"date": "2026-11-05"})

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_post_invoice_anonymous_is_401(client: object, db: Session) -> None:
    note = delivered_note(db)

    assert client.post(issue_url(note.id)).status_code == 401  # type: ignore[attr-defined]


def test_post_invoice_already_invoiced_is_422(client_as: ClientAs, db: Session) -> None:
    seller = client_as("seller")
    note = delivered_note(db)
    assert seller.post(issue_url(note.id), json={"date": "2026-11-05"}).status_code == 201

    response = seller.post(issue_url(note.id), json={"date": "2026-11-05"})

    assert response.status_code == 422
    assert response.json()["code"] == "already_invoiced"


def test_post_invoice_before_delivery_date_is_422(client_as: ClientAs, db: Session) -> None:
    note = delivered_note(db, note_date=dt.date(2026, 11, 2))

    response = client_as("seller").post(issue_url(note.id), json={"date": "2026-11-01"})

    assert response.status_code == 422
    assert response.json()["code"] == "invoice_date_before_delivery"


def test_post_invoice_missing_delivery_note_is_404(client_as: ClientAs) -> None:
    response = client_as("seller").post(issue_url(999_999), json={})

    assert response.status_code == 404


# GET /invoices/{id} ----------------------------------------------------------------------


def test_get_invoice_as_accountant_returns_detail(client_as: ClientAs, db: Session) -> None:
    invoice = make_invoice(db)

    response = client_as("accountant").get(f"/api/v1/invoices/{invoice.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == invoice.id
    assert body["customer_tax_condition"] == "responsable_inscripto"
    assert (body["total"], body["balance"], body["status"]) == ("121.00", "121.00", "open")
    assert len(body["lines"]) == 1


def test_get_invoice_missing_is_404(client_as: ClientAs) -> None:
    assert client_as("seller").get("/api/v1/invoices/999999").status_code == 404


# Legend (Ley 27.618) ---------------------------------------------------------------------


def test_api_legend_present_for_letter_a_to_monotributo(client_as: ClientAs, db: Session) -> None:
    seller = client_as("seller")
    note = delivered_note(db, tax_condition=T.MONOTRIBUTO)

    created = seller.post(issue_url(note.id), json={"date": "2026-11-05"}).json()
    fetched = seller.get(f"/api/v1/invoices/{created['id']}").json()

    assert created["letter"] == fetched["letter"] == "A"
    assert created["legend"] == fetched["legend"] == LEY_27618_LEGEND


@pytest.mark.parametrize("tax_condition", [T.RESPONSABLE_INSCRIPTO, T.CONSUMIDOR_FINAL, T.EXENTO])
def test_api_legend_null_for_a_to_ri_and_for_b(
    client_as: ClientAs, db: Session, tax_condition: TaxCondition
) -> None:
    seller = client_as("seller")
    note = delivered_note(db, tax_condition=tax_condition)

    created = seller.post(issue_url(note.id), json={"date": "2026-11-05"}).json()
    fetched = seller.get(f"/api/v1/invoices/{created['id']}").json()

    assert fetched["letter"] == ("A" if tax_condition is T.RESPONSABLE_INSCRIPTO else "B")
    assert created["legend"] is None
    assert fetched["legend"] is None


# GET /invoices ---------------------------------------------------------------------------


def test_list_invoices_with_status_filter_uses_balance(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    paid = make_invoice(db, customer=customer, invoice_date=dt.date(2026, 11, 1))
    unpaid = make_invoice(db, customer=customer, invoice_date=dt.date(2026, 11, 2))
    make_receipt(db, customer=customer, allocations=[(paid, D("121.00"))])
    reader = client_as("accountant")

    open_body = reader.get("/api/v1/invoices", params={"status": "open"}).json()
    paid_body = reader.get("/api/v1/invoices", params={"status": "paid"}).json()

    assert [(i["id"], i["balance"], i["status"]) for i in open_body["items"]] == [
        (unpaid.id, "121.00", "open")
    ]
    assert [(i["id"], i["balance"], i["status"]) for i in paid_body["items"]] == [
        (paid.id, "0.00", "paid")
    ]
    assert (open_body["total"], paid_body["total"]) == (1, 1)


def test_list_invoices_filters_and_paginates(client_as: ClientAs, db: Session) -> None:
    ri = make_customer(db, tax_condition=T.RESPONSABLE_INSCRIPTO)
    cf = make_customer(db, tax_condition=T.CONSUMIDOR_FINAL)
    a1 = make_invoice(db, customer=ri, invoice_date=dt.date(2026, 11, 1))
    a2 = make_invoice(db, customer=ri, invoice_date=dt.date(2026, 11, 10))
    b1 = make_invoice(db, customer=cf, invoice_date=dt.date(2026, 11, 20))
    seller = client_as("seller")

    def ids(**params: object) -> list[int]:
        response = seller.get("/api/v1/invoices", params=params)
        assert response.status_code == 200
        return [item["id"] for item in response.json()["items"]]

    assert ids() == [b1.id, a2.id, a1.id]
    assert ids(customer_id=ri.id) == [a2.id, a1.id]
    assert ids(letter="B") == [b1.id]
    assert ids(**{"from": "2026-11-05", "to": "2026-11-15"}) == [a2.id]
    assert ids(limit=1, offset=1) == [a2.id]
    assert seller.get("/api/v1/invoices", params={"limit": 1}).json()["total"] == 3


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"status": "x"}])
def test_list_invoices_with_invalid_query_is_422(
    client_as: ClientAs, params: dict[str, object]
) -> None:
    assert client_as("seller").get("/api/v1/invoices", params=params).status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/invoices", "/api/v1/invoices/1"])
def test_read_invoices_anonymous_is_401(client: object, path: str) -> None:
    assert client.get(path).status_code == 401  # type: ignore[attr-defined]
