"""Accounting HTML views (HTMX): chart of accounts, journal and ledger."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import Account
from tests.conftest import ClientAs
from tests.integration.accounting.conftest import AccountByCode, PostInvoice, PostReceipt

HX = {"HX-Request": "true"}


@pytest.fixture
def accountant(client_as: ClientAs) -> TestClient:
    return client_as("accountant")


@pytest.mark.parametrize(
    "path", ["/accounting/accounts", "/accounting/journal", "/accounting/ledger"]
)
def test_accounting_pages_as_seller_are_forbidden(client_as: ClientAs, path: str) -> None:
    assert client_as("seller").get(path).status_code == 403


def test_accounting_write_as_seller_is_forbidden(client_as: ClientAs) -> None:
    response = client_as("seller").post("/accounting/accounts", data={"code": "1.1.1.03"})

    assert response.status_code == 403


def test_accounting_page_anonymous_redirects_to_login(client: TestClient) -> None:
    response = client.get("/accounting/accounts", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# Chart of accounts ------------------------------------------------------------------------


def test_accounts_page_renders_tree_and_create_form(accountant: TestClient) -> None:
    response = accountant.get("/accounting/accounts")

    assert response.status_code == 200
    html = response.text
    assert 'id="accounts-tree"' in html
    assert 'hx-post="/accounting/accounts"' in html
    assert "1.1.3.01" in html
    assert "Deudores por ventas" in html
    assert 'hx-patch="/accounting/accounts/' in html


def test_create_account_via_htmx_returns_tree_with_new_account(
    accountant: TestClient, account_by_code: AccountByCode, db: Session
) -> None:
    parent = account_by_code("1.1.1")

    response = accountant.post(
        "/accounting/accounts",
        data={
            "code": "1.1.1.03",
            "name": "Banco Galicia",
            "parent_id": str(parent.id),
            "is_postable": "true",
        },
        headers=HX,
    )

    assert response.status_code == 200
    assert "HX-Retarget" not in response.headers
    assert "Banco Galicia" in response.text
    assert "Cuenta 1.1.1.03 creada" in response.text
    created = db.scalars(select(Account).where(Account.code == "1.1.1.03")).one()
    assert (created.type, created.is_postable) == (parent.type, True)


def test_create_rubro_via_htmx_without_postable_checkbox(
    accountant: TestClient, account_by_code: AccountByCode
) -> None:
    response = accountant.post(
        "/accounting/accounts",
        data={
            "code": "1.1.5",
            "name": "Otros créditos",
            "parent_id": str(account_by_code("1.1").id),
        },
        headers=HX,
    )

    assert response.status_code == 200
    assert account_by_code("1.1.5").is_postable is False


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"code": "1.1.1.01", "name": "Otra"}, "already exists"),
        ({"code": "bad", "name": "Otra"}, "code"),
        ({"code": "1.1.1.03", "name": "Otra", "parent_id": ""}, "rubro padre"),
    ],
)
def test_create_account_via_htmx_with_error_retargets_flash(
    accountant: TestClient, account_by_code: AccountByCode, data: dict[str, str], message: str
) -> None:
    form = {"parent_id": str(account_by_code("1.1.1").id)} | data

    response = accountant.post("/accounting/accounts", data=form, headers=HX)

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#flash"
    assert "flash-error" in response.text
    assert message in response.text


def test_update_account_via_htmx_renames_and_deactivates(
    accountant: TestClient, account_by_code: AccountByCode, db: Session
) -> None:
    parent = account_by_code("1.1.1")
    accountant.post(
        "/accounting/accounts",
        data={
            "code": "1.1.1.03",
            "name": "Banco",
            "parent_id": str(parent.id),
            "is_postable": "true",
        },
        headers=HX,
    )
    account = account_by_code("1.1.1.03")

    response = accountant.patch(
        f"/accounting/accounts/{account.id}", data={"name": "Banco Macro"}, headers=HX
    )

    assert response.status_code == 200
    assert "Cuenta 1.1.1.03 actualizada" in response.text
    db.refresh(account)
    assert (account.name, account.is_active) == ("Banco Macro", False)


def test_update_account_via_htmx_with_rule_violation_retargets_flash(
    accountant: TestClient, account_by_code: AccountByCode
) -> None:
    caja = account_by_code("1.1.1.01")

    response = accountant.patch(f"/accounting/accounts/{caja.id}", data={"name": "Caja"})

    assert response.headers["HX-Retarget"] == "#flash"
    assert "system account" in response.text
    assert caja.is_active is True


def test_update_account_via_htmx_with_blank_name_retargets_flash(
    accountant: TestClient, account_by_code: AccountByCode
) -> None:
    caja = account_by_code("1.1.1.01")

    response = accountant.patch(
        f"/accounting/accounts/{caja.id}", data={"name": "", "is_active": "true"}
    )

    assert response.headers["HX-Retarget"] == "#flash"
    assert "name" in response.text


# Journal ----------------------------------------------------------------------------------


def test_journal_page_lists_entries_with_ars_amounts(
    accountant: TestClient, post_invoice: PostInvoice
) -> None:
    post_invoice(dt.date(2026, 11, 3), "1500.00", vat_21="210.00", vat_10_5="52.50")

    response = accountant.get("/accounting/journal")

    assert response.status_code == 200
    assert "1 asiento</p>" in response.text
    assert "Asiento N° 1" in response.text
    assert "03/11/2026" in response.text
    assert "1.762,50" in response.text
    assert "IVA Débito Fiscal 10,5%" in response.text


def test_journal_page_filters_by_date_and_source(
    accountant: TestClient, post_invoice: PostInvoice, post_receipt: PostReceipt
) -> None:
    post_invoice(dt.date(2026, 11, 1), "100.00", description="Factura uno")
    post_receipt(dt.date(2026, 11, 2), "10.00", description="Cobranza uno")
    post_receipt(dt.date(2026, 11, 9), "10.00", description="Cobranza dos")

    response = accountant.get(
        "/accounting/journal",
        params={"from": "2026-11-02", "to": "2026-11-05", "source_type": "receipt"},
    )

    assert "Cobranza uno" in response.text
    assert "Factura uno" not in response.text
    assert "Cobranza dos" not in response.text


def test_journal_page_paginates(
    accountant: TestClient, post_receipt: PostReceipt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.routers.web.accounting.JOURNAL_PAGE_SIZE", 2)
    for day in (1, 2, 3):
        post_receipt(dt.date(2026, 11, day), "10.00", description=f"Cobranza día {day}")

    first = accountant.get("/accounting/journal").text
    last = accountant.get("/accounting/journal", params={"page": 2}).text

    assert "Página 1 de 2" in first
    assert "Cobranza día 3" not in first
    assert "Cobranza día 3" in last
    assert "« Anterior" in last


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"from": "2026-11-05", "to": "2026-11-01"}, "is after"),
        ({"from": "not-a-date"}, "Invalid date"),
        ({"source_type": "nope"}, "Tipo de origen"),
    ],
)
def test_journal_page_with_bad_filters_shows_error(
    accountant: TestClient, params: dict[str, str], message: str
) -> None:
    response = accountant.get("/accounting/journal", params=params)

    assert response.status_code == 200
    assert message in response.text


# Ledger -----------------------------------------------------------------------------------


def test_ledger_page_without_account_shows_picker(accountant: TestClient) -> None:
    response = accountant.get("/accounting/ledger")

    assert response.status_code == 200
    assert 'name="account_id"' in response.text
    assert "Elegí una cuenta para ver su mayor." in response.text


def test_ledger_page_shows_opening_running_and_closing_balance(
    accountant: TestClient,
    account_by_code: AccountByCode,
    post_invoice: PostInvoice,
    post_receipt: PostReceipt,
) -> None:
    post_invoice(dt.date(2026, 10, 20), "1000.00", vat_21="210.00")
    post_receipt(dt.date(2026, 11, 5), "400.00")
    deudores = account_by_code("1.1.3.01")

    response = accountant.get(
        "/accounting/ledger",
        params={"account_id": deudores.id, "from": "2026-11-01", "to": "2026-11-30"},
    )

    html = response.text
    assert "1.1.3.01 · Deudores por ventas</h2>" in html
    assert "1.210,00" in html  # opening balance
    assert "810,00" in html  # running and closing balance
    assert "(deudor)" in html


def test_ledger_page_of_rubro_shows_account_column(
    accountant: TestClient, account_by_code: AccountByCode, post_receipt: PostReceipt
) -> None:
    post_receipt(dt.date(2026, 11, 5), "400.00")

    response = accountant.get(
        "/accounting/ledger", params={"account_id": account_by_code("1.1.1").id}
    )

    assert "incluye los movimientos de todas sus subcuentas" in response.text
    assert "<td>1.1.1.01</td>" in response.text


def test_ledger_page_with_unknown_account_shows_error(accountant: TestClient) -> None:
    response = accountant.get("/accounting/ledger", params={"account_id": 999999})

    assert response.status_code == 200
    assert "not found" in response.text
