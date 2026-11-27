import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import TaxCondition, VatRate
from app.models.sales import Invoice
from app.services import invoices as service
from app.services.invoices import LEY_27618_LEGEND
from tests.conftest import ClientAs
from tests.factories import make_customer, make_invoice, make_product, make_receipt, make_user
from tests.integration.invoices.helpers import delivered_note, mixed_vat_lines

D = Decimal
T = TaxCondition
INVOICE_DATE = dt.date(2026, 11, 5)


def issued(db: Session, tax_condition: TaxCondition, **kwargs: object) -> Invoice:
    note = delivered_note(db, tax_condition=tax_condition, **kwargs)  # type: ignore[arg-type]
    user = make_user(db)
    return service.issue_invoice(db, note.id, created_by=user.id, invoice_date=INVOICE_DATE).invoice


# Printable view: legend ------------------------------------------------------------------


def test_printable_invoice_a_to_monotributo_shows_legend(client_as: ClientAs, db: Session) -> None:
    invoice = issued(db, T.MONOTRIBUTO)

    response = client_as("seller").get(f"/invoices/{invoice.id}")

    assert response.status_code == 200
    assert LEY_27618_LEGEND in response.text
    assert 'data-letter="A"' in response.text


def test_printable_invoice_a_to_ri_has_no_legend(client_as: ClientAs, db: Session) -> None:
    invoice = issued(db, T.RESPONSABLE_INSCRIPTO)

    response = client_as("seller").get(f"/invoices/{invoice.id}")

    assert response.status_code == 200
    assert 'data-letter="A"' in response.text
    assert LEY_27618_LEGEND not in response.text
    assert "27.618" not in response.text


# Printable view: layout per letter ---------------------------------------------------------


def test_printable_invoice_a_shows_net_and_vat_per_rate(client_as: ClientAs, db: Session) -> None:
    invoice = issued(db, T.RESPONSABLE_INSCRIPTO, lines=mixed_vat_lines(db))

    html = client_as("accountant").get(f"/invoices/{invoice.id}").text

    assert "0001-00000001" in html
    assert "Importe neto gravado" in html
    assert "1.500,00" in html  # net
    assert "210,00" in html  # VAT 21
    assert "52,50" in html  # VAT 10.5
    assert "1.762,50" in html  # total
    assert "no-print" in html
    assert invoice.customer_name in html
    assert "IVA Responsable Inscripto" in html


def test_printable_invoice_b_shows_prices_with_vat_included(
    client_as: ClientAs, db: Session
) -> None:
    product = make_product(db, name="Lámpara", unit_price=D("100.00"), vat_rate=VatRate.VAT_21)
    invoice = issued(db, T.CONSUMIDOR_FINAL, lines=[(product, D("2"))])

    html = client_as("seller").get(f"/invoices/{invoice.id}").text

    assert 'data-letter="B"' in html
    assert "IVA incl." in html
    assert "121,00" in html  # unit price with VAT
    assert "242,00" in html  # total
    assert "Importe neto gravado" not in html
    assert "Consumidor Final" in html
    assert LEY_27618_LEGEND not in html


def test_printable_invoice_missing_is_404(client_as: ClientAs) -> None:
    assert client_as("seller").get("/invoices/999999").status_code == 404


# List --------------------------------------------------------------------------------------


def test_invoice_list_shows_invoices_and_pending_delivery_notes(
    client_as: ClientAs, db: Session
) -> None:
    customer = make_customer(db, name="Ferretería Sur")
    invoice = make_invoice(db, customer=customer)
    pending = delivered_note(db)

    html = client_as("seller").get("/invoices").text

    assert f"/invoices/{invoice.id}" in html
    assert "Ferretería Sur" in html
    assert "Remitos sin facturar" in html
    assert f'hx-post="/delivery-notes/{pending.id}/invoice"' in html


def test_invoice_list_for_accountant_hides_invoice_action(client_as: ClientAs, db: Session) -> None:
    delivered_note(db)

    html = client_as("accountant").get("/invoices").text

    assert "Remitos sin facturar" not in html


def test_invoice_list_filters_by_status_and_ignores_blank_or_bad_values(
    client_as: ClientAs, db: Session
) -> None:
    customer = make_customer(db)
    paid = make_invoice(db, customer=customer)
    unpaid = make_invoice(db, customer=customer)
    make_receipt(db, customer=customer, allocations=[(paid, D("121.00"))])
    seller = client_as("seller")

    html = seller.get(
        "/invoices",
        params={"status": "paid", "customer_id": "", "letter": "Z", "from": "bad", "to": ""},
    ).text

    assert f"/invoices/{paid.id}" in html
    assert f"/invoices/{unpaid.id}" not in html
    assert "Cobrada" in html


def test_invoice_list_paginates(client_as: ClientAs, db: Session) -> None:
    make_invoice(db)

    html = client_as("seller").get("/invoices", params={"offset": 50}).text

    assert "Anteriores" in html
    assert "No hay facturas" in html


# "Facturar" action -----------------------------------------------------------------------


def test_web_invoice_action_redirects_to_printable_invoice(
    client_as: ClientAs, db: Session
) -> None:
    note = delivered_note(db, tax_condition=T.MONOTRIBUTO)

    response = client_as("seller").post(
        f"/delivery-notes/{note.id}/invoice", data={"date": "2026-11-05"}
    )

    invoice = db.scalars(select(Invoice).where(Invoice.delivery_note_id == note.id)).one()
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/invoices/{invoice.id}"
    assert invoice.date == INVOICE_DATE


def test_web_invoice_action_without_date_uses_today(client_as: ClientAs, db: Session) -> None:
    note = delivered_note(db, note_date=dt.date(2020, 1, 1))

    response = client_as("seller").post(f"/delivery-notes/{note.id}/invoice", data={"date": ""})

    assert response.status_code == 204


def test_web_invoice_action_already_invoiced_shows_error_in_flash(
    client_as: ClientAs, db: Session
) -> None:
    seller = client_as("seller")
    note = delivered_note(db)
    seller.post(f"/delivery-notes/{note.id}/invoice", data={"date": "2026-11-05"})

    response = seller.post(f"/delivery-notes/{note.id}/invoice", data={"date": "2026-11-05"})

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#flash"
    assert "already invoiced" in response.text


def test_web_invoice_action_with_bad_date_shows_error(client_as: ClientAs, db: Session) -> None:
    note = delivered_note(db)

    response = client_as("seller").post(
        f"/delivery-notes/{note.id}/invoice", data={"date": "not-a-date"}
    )

    assert response.headers["HX-Retarget"] == "#flash"
    assert "Fecha inválida" in response.text
    assert db.scalars(select(Invoice).where(Invoice.delivery_note_id == note.id)).first() is None


def test_web_invoice_action_as_accountant_is_403(client_as: ClientAs, db: Session) -> None:
    note = delivered_note(db)

    response = client_as("accountant").post(f"/delivery-notes/{note.id}/invoice", data={})

    assert response.status_code == 403


def test_web_invoice_action_requires_csrf(client_as: ClientAs, db: Session) -> None:
    seller = client_as("seller")
    note = delivered_note(db)
    seller.headers.pop("X-CSRF-Token")

    response = seller.post(f"/delivery-notes/{note.id}/invoice", data={})

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"
