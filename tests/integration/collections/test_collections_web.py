import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import CSRF_HEADER
from app.models.collections import Receipt
from app.services.invoice_balance import invoice_balance
from tests.conftest import ClientAs
from tests.factories import make_customer, make_receipt
from tests.integration.collections.helpers import DAY, D, invoice_of, receipt_entry_lines

HX = {"HX-Request": "true"}


def receipt_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Receipt)) or 0


def test_new_receipt_page_lists_customers(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db, name="Ferretería Sur")

    response = client_as("seller").get("/receipts/new")

    assert response.status_code == 200
    assert "Nueva cobranza" in response.text
    assert "Ferretería Sur" in response.text
    assert f'value="{customer.id}"' in response.text
    assert 'hx-get="/receipts/new/open-invoices"' in response.text


def test_new_receipt_page_with_customer_preloads_open_invoices(
    client_as: ClientAs, db: Session
) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)

    html = client_as("seller").get(f"/receipts/new?customer_id={customer.id}").text

    assert f'name="amount_{inv.id}"' in html
    assert 'hx-post="/receipts"' in html


def test_open_invoices_partial_renders_allocation_form(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)
    make_receipt(db, customer=customer, allocations=[(inv, D("21.00"))])

    response = client_as("accountant").get(
        "/receipts/new/open-invoices", params={"customer_id": customer.id}, headers=HX
    )

    assert response.status_code == 200
    assert "<html" not in response.text  # partial, not a full page
    assert f'name="amount_{inv.id}"' in response.text
    assert "100,00" in response.text  # balance in Argentine format


def test_open_invoices_partial_without_customer_asks_to_pick_one(client_as: ClientAs) -> None:
    response = client_as("seller").get("/receipts/new/open-invoices", params={"customer_id": ""})

    assert "Elegí un cliente" in response.text


def test_open_invoices_partial_customer_without_debt_says_so(
    client_as: ClientAs, db: Session
) -> None:
    customer = make_customer(db)

    response = client_as("seller").get(
        "/receipts/new/open-invoices", params={"customer_id": customer.id}
    )

    assert "no tiene facturas con saldo pendiente" in response.text


def test_post_receipt_form_creates_receipt_and_redirects(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    a = invoice_of(db, customer)
    b = invoice_of(db, customer)

    response = client_as("seller").post(
        "/receipts",
        data={
            "customer_id": str(customer.id),
            "date": DAY.isoformat(),
            "method": "cash",
            "reference": "",
            f"amount_{a.id}": "121.00",
            f"amount_{b.id}": "",
        },
        headers=HX,
    )

    assert response.status_code == 200
    receipt = db.scalars(select(Receipt).where(Receipt.customer_id == customer.id)).one()
    assert response.headers["HX-Redirect"] == f"/receipts/{receipt.id}"
    assert receipt.amount == D("121.00")
    assert invoice_balance(db, a.id) == D("0.00")
    assert invoice_balance(db, b.id) == D("121.00")
    assert receipt_entry_lines(db, receipt.id) == {
        "1.1.1.01": (D("121.00"), D("0.00")),
        "1.1.3.01": (D("0.00"), D("121.00")),
    }


def test_post_receipt_form_overpayment_shows_error_and_saves_nothing(
    client_as: ClientAs, db: Session
) -> None:
    customer = make_customer(db)
    inv = invoice_of(db, customer)

    response = client_as("seller").post(
        "/receipts",
        data={"customer_id": str(customer.id), "method": "cash", f"amount_{inv.id}": "999.00"},
        headers=HX,
    )

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#flash"
    assert "exceeds the balance" in response.text
    assert receipt_count(db) == 0


def test_post_receipt_form_invalid_fields_show_error(client_as: ClientAs, db: Session) -> None:
    response = client_as("seller").post(
        "/receipts", data={"customer_id": "abc", "method": "cash"}, headers=HX
    )

    assert response.headers["HX-Retarget"] == "#flash"
    assert "Datos inválidos" in response.text
    assert receipt_count(db) == 0


def test_post_receipt_form_without_csrf_is_403(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)

    response = client_as("seller").post(
        "/receipts", data={"customer_id": str(customer.id)}, headers={CSRF_HEADER: "forged"}
    )

    assert response.status_code == 403


def test_receipt_detail_page_shows_allocations(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db, name="Kiosco Norte")
    inv = invoice_of(db, customer)
    receipt = make_receipt(db, customer=customer, allocations=[(inv, D("21.00"))])

    response = client_as("accountant").get(f"/receipts/{receipt.id}")

    assert response.status_code == 200
    assert f"Cobranza 0001-{receipt.number:08d}" in response.text
    assert "Kiosco Norte" in response.text
    assert f"A 0001-{inv.number:08d}" in response.text
    assert "Efectivo" in response.text


def test_receipt_detail_unknown_is_404(client_as: ClientAs) -> None:
    assert client_as("seller").get("/receipts/999999").status_code == 404


def test_statement_page_renders_balances_and_filters(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)
    oct_inv = invoice_of(db, customer, day=dt.date(2026, 10, 1))
    invoice_of(db, customer, "1000.00", day=dt.date(2026, 11, 10))  # 1210.00
    make_receipt(
        db,
        customer=customer,
        allocations=[(oct_inv, D("100.00"))],
        receipt_date=dt.date(2026, 10, 15),
    )
    client = client_as("seller")

    full = client.get(f"/customers/{customer.id}/statement", params={"from": "", "to": ""})
    november = client.get(f"/customers/{customer.id}/statement", params={"from": "2026-11-01"})

    assert full.status_code == 200
    assert "1.231,00" in full.text  # closing: 121 - 100 + 1210
    assert "Cuenta corriente" in full.text
    assert november.status_code == 200
    assert "21,00" in november.text  # opening balance
    assert "Cobranza" not in november.text.split("<tbody>")[1].split("</tbody>")[0]


def test_statement_page_invalid_date_is_422(client_as: ClientAs, db: Session) -> None:
    customer = make_customer(db)

    response = client_as("seller").get(
        f"/customers/{customer.id}/statement", params={"from": "not-a-date"}
    )

    assert response.status_code == 422


def test_collections_pages_anonymous_redirect_to_login(client: TestClient) -> None:
    for path in ("/receipts/new", "/receipts/1", "/customers/1/statement"):
        response = client.get(path, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"
