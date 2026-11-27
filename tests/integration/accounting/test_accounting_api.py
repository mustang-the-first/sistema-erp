"""Accounting JSON API: contract of docs/plan.md section 3 (Contabilidad)."""

import datetime as dt
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.accounting import Account
from tests.conftest import ClientAs
from tests.integration.accounting.conftest import AccountByCode, PostInvoice, PostReceipt

API = "/api/v1"


@pytest.fixture
def accountant(client_as: ClientAs) -> TestClient:
    return client_as("accountant")


def find(nodes: list[dict[str, Any]], code: str) -> dict[str, Any]:
    for node in nodes:
        if node["code"] == code:
            return node
        if code.startswith(node["code"] + "."):
            return find(node["children"], code)
    raise AssertionError(f"{code} not in tree")


# Roles ------------------------------------------------------------------------------------

ENDPOINTS = [
    ("GET", "/accounts", None),
    ("POST", "/accounts", {"code": "1.1.1.03", "name": "X", "type": "asset", "parent_id": 1}),
    ("PATCH", "/accounts/1", {"name": "X"}),
    ("GET", "/journal-entries", None),
    ("GET", "/journal-entries/1", None),
    ("GET", "/ledger/1", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_accounting_endpoints_as_seller_are_forbidden(
    client_as: ClientAs, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = client_as("seller").request(method, API + path, json=body)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
def test_accounting_endpoints_anonymous_are_unauthorized(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    assert client.request(method, API + path, json=body).status_code == 401


def test_accounts_as_admin_are_allowed(client_as: ClientAs) -> None:
    assert client_as("admin").get(f"{API}/accounts").status_code == 200


# Accounts ---------------------------------------------------------------------------------


def test_get_accounts_returns_nested_tree(accountant: TestClient) -> None:
    response = accountant.get(f"{API}/accounts")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 27
    assert [n["code"] for n in body["items"]] == ["1", "2", "3", "4", "5"]
    caja = find(body["items"], "1.1.1.01")
    assert caja | {"id": 0, "parent_id": 0} == {
        "id": 0,
        "code": "1.1.1.01",
        "name": "Caja",
        "type": "asset",
        "parent_id": 0,
        "is_postable": True,
        "is_active": True,
        "children": [],
    }
    assert [n["code"] for n in find(body["items"], "1.1")["children"]] == [
        "1.1.1",
        "1.1.3",
        "1.1.4",
    ]


def test_get_accounts_flat_returns_every_account_without_children(accountant: TestClient) -> None:
    body = accountant.get(f"{API}/accounts", params={"flat": "true"}).json()

    assert body["total"] == len(body["items"]) == 27
    assert body["items"][0]["code"] == "1"
    assert all("children" not in item for item in body["items"])


def test_post_account_creates_it_and_returns_201(
    accountant: TestClient, account_by_code: AccountByCode, db: Session
) -> None:
    parent = account_by_code("1.1.1")

    response = accountant.post(
        f"{API}/accounts",
        json={
            "code": "1.1.1.03",
            "name": "Banco Nación",
            "type": "asset",
            "parent_id": parent.id,
            "is_postable": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "1.1.1.03"
    assert body["parent_id"] == parent.id
    assert db.get_one(Account, body["id"]).name == "Banco Nación"


def test_post_account_with_taken_code_is_422_code_taken(
    accountant: TestClient, account_by_code: AccountByCode
) -> None:
    response = accountant.post(
        f"{API}/accounts",
        json={
            "code": "1.1.1.01",
            "name": "Otra caja",
            "type": "asset",
            "parent_id": account_by_code("1.1.1").id,
            "is_postable": True,
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "code_taken"


def test_post_account_with_type_mismatch_is_422(
    accountant: TestClient, account_by_code: AccountByCode
) -> None:
    response = accountant.post(
        f"{API}/accounts",
        json={
            "code": "1.1.1.03",
            "name": "X",
            "type": "income",
            "parent_id": account_by_code("1.1.1").id,
            "is_postable": True,
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "type_mismatch"


@pytest.mark.parametrize("code", ["", "1.1.1.a", "1..1", "1.1.1.03 x"])
def test_post_account_with_malformed_code_is_422(
    accountant: TestClient, account_by_code: AccountByCode, code: str
) -> None:
    response = accountant.post(
        f"{API}/accounts",
        json={"code": code, "name": "X", "type": "asset", "parent_id": account_by_code("1.1.1").id},
    )

    assert response.status_code == 422


def test_patch_account_renames_it(accountant: TestClient, account_by_code: AccountByCode) -> None:
    caja = account_by_code("1.1.1.01")

    response = accountant.patch(f"{API}/accounts/{caja.id}", json={"name": "Caja principal"})

    assert response.status_code == 200
    assert response.json()["name"] == "Caja principal"
    assert response.json()["is_active"] is True


def test_patch_account_with_type_is_422_and_type_unchanged(
    accountant: TestClient, account_by_code: AccountByCode
) -> None:
    caja = account_by_code("1.1.1.01")

    response = accountant.patch(f"{API}/accounts/{caja.id}", json={"type": "liability"})

    assert response.status_code == 422
    assert caja.type == "asset"


def test_patch_system_account_deactivation_is_422(
    accountant: TestClient, account_by_code: AccountByCode
) -> None:
    caja = account_by_code("1.1.1.01")

    response = accountant.patch(f"{API}/accounts/{caja.id}", json={"is_active": False})

    assert response.status_code == 422
    assert response.json()["code"] == "system_account"


def test_patch_unknown_account_is_404(accountant: TestClient) -> None:
    response = accountant.patch(f"{API}/accounts/999999", json={"name": "X"})

    assert response.status_code == 404


# Journal ----------------------------------------------------------------------------------


def test_journal_entries_paginate_and_serialize_amounts_as_strings(
    accountant: TestClient, post_invoice: PostInvoice, post_receipt: PostReceipt
) -> None:
    post_invoice(dt.date(2026, 11, 1), "1500.00", vat_21="210.00", vat_10_5="52.50")
    post_receipt(dt.date(2026, 11, 2), "100.00")
    post_receipt(dt.date(2026, 11, 3), "200.00")

    first = accountant.get(f"{API}/journal-entries", params={"limit": 2}).json()
    second = accountant.get(f"{API}/journal-entries", params={"limit": 2, "offset": 2}).json()

    assert first["total"] == second["total"] == 3
    assert [e["number"] for e in first["items"]] == [1, 2]
    assert [e["number"] for e in second["items"]] == [3]
    invoice = first["items"][0]
    assert invoice["date"] == "2026-11-01"
    assert invoice["source_type"] == "invoice"
    assert (invoice["total_debit"], invoice["total_credit"]) == ("1762.50", "1762.50")
    assert invoice["lines"][0] == {
        "account_id": invoice["lines"][0]["account_id"],
        "account_code": "1.1.3.01",
        "account_name": "Deudores por ventas",
        "debit": "1762.50",
        "credit": "0.00",
    }


def test_journal_entries_filter_by_dates_and_source_type(
    accountant: TestClient, post_invoice: PostInvoice, post_receipt: PostReceipt
) -> None:
    post_invoice(dt.date(2026, 11, 1), "100.00")
    post_receipt(dt.date(2026, 11, 2), "50.00")
    post_receipt(dt.date(2026, 11, 5), "50.00")

    by_date = accountant.get(
        f"{API}/journal-entries", params={"from": "2026-11-02", "to": "2026-11-04"}
    ).json()
    by_type = accountant.get(f"{API}/journal-entries", params={"source_type": "receipt"}).json()

    assert [e["date"] for e in by_date["items"]] == ["2026-11-02"]
    assert by_type["total"] == 2


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"from": "bad"}, {"source_type": "x"}],
)
def test_journal_entries_with_invalid_query_is_422(
    accountant: TestClient, params: dict[str, Any]
) -> None:
    assert accountant.get(f"{API}/journal-entries", params=params).status_code == 422


def test_journal_entries_with_from_after_to_is_422_invalid_date_range(
    accountant: TestClient,
) -> None:
    response = accountant.get(
        f"{API}/journal-entries", params={"from": "2026-11-05", "to": "2026-11-01"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_date_range"


def test_get_journal_entry_by_id(accountant: TestClient, post_receipt: PostReceipt) -> None:
    entry = post_receipt(dt.date(2026, 11, 2), "75.10")

    body = accountant.get(f"{API}/journal-entries/{entry.id}").json()

    assert body["id"] == entry.id
    assert [(li["account_code"], li["debit"], li["credit"]) for li in body["lines"]] == [
        ("1.1.1.01", "75.10", "0.00"),
        ("1.1.3.01", "0.00", "75.10"),
    ]


def test_get_unknown_journal_entry_is_404(accountant: TestClient) -> None:
    response = accountant.get(f"{API}/journal-entries/999999")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# Ledger -----------------------------------------------------------------------------------


def test_ledger_returns_opening_running_and_closing_balance(
    accountant: TestClient,
    account_by_code: AccountByCode,
    post_invoice: PostInvoice,
    post_receipt: PostReceipt,
) -> None:
    post_invoice(dt.date(2026, 10, 20), "1000.00", vat_21="210.00")
    post_receipt(dt.date(2026, 11, 5), "400.00")
    post_invoice(dt.date(2026, 11, 12), "500.00", vat_10_5="52.50")
    deudores = account_by_code("1.1.3.01")

    response = accountant.get(
        f"{API}/ledger/{deudores.id}", params={"from": "2026-11-01", "to": "2026-11-30"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["code"] == "1.1.3.01"
    assert (body["date_from"], body["date_to"]) == ("2026-11-01", "2026-11-30")
    assert body["opening_balance"] == "1210.00"
    assert [
        (m["entry_number"], m["debit"], m["credit"], m["balance"]) for m in body["movements"]
    ] == [
        (2, "0.00", "400.00", "810.00"),
        (3, "552.50", "0.00", "1362.50"),
    ]
    assert (body["total_debit"], body["total_credit"]) == ("552.50", "400.00")
    assert body["closing_balance"] == "1362.50"
    assert body["nature"] == "deudor"
    assert body["includes_descendants"] is False


def test_ledger_of_unknown_account_is_404(accountant: TestClient) -> None:
    assert accountant.get(f"{API}/ledger/999999").status_code == 404


def test_ledger_with_from_after_to_is_422(
    accountant: TestClient, account_by_code: AccountByCode
) -> None:
    response = accountant.get(
        f"{API}/ledger/{account_by_code('1.1.1.01').id}",
        params={"from": "2026-11-05", "to": "2026-11-01"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_date_range"
