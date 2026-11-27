"""End-to-end order-to-cash through the HTTP API and the web views, as real users per role.

Flow: initial stock and a damage adjustment -> three customers (RI, Monotributo, Consumidor
Final) -> orders -> partial delivery notes -> invoices A (RI), A + Ley 27.618 legend
(Monotributo) and B (CF) -> partial and full receipts. One delivery note is invoiced with the
"Facturar" button that D1's order page embeds from D2.

Then the cross-checks of docs/plan.md (Wave 3): every module must tell the same story.
Expected amounts are worked out by hand in the comments.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import today
from app.models.stock import StockMovement
from tests.conftest import ClientAs

API = "/api/v1"
HX = {"HX-Request": "true"}
D = Decimal
LEY_27618 = "Ley Nº 27.618"


def valid_cuit(prefix: str, body: int) -> str:
    """Independent mod-11 CUIT generator (does not reuse the app's implementation)."""
    weights = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    for n in range(body, body + 50):
        first_ten = f"{prefix}{n:08d}"
        check = 11 - sum(int(d) * w for d, w in zip(first_ten, weights, strict=True)) % 11
        if check == 11:
            return f"{first_ten}0"
        if check < 10:
            return f"{first_ten}{check}"
    raise AssertionError("no valid CUIT found")


def ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


@dataclass
class World:
    admin: TestClient
    seller: TestClient
    accountant: TestClient
    ids: dict[str, int] = field(default_factory=dict)
    invoices: dict[str, dict[str, Any]] = field(default_factory=dict)


# Scenario ---------------------------------------------------------------------------------


@pytest.fixture
def world(client_as: ClientAs) -> Iterator[World]:
    w = World(client_as("admin"), client_as("seller"), client_as("accountant"))
    admin, seller = w.admin, w.seller

    # Catalog and initial stock (admin) -----------------------------------------------------
    yerba = ok(
        admin.post(
            f"{API}/products",
            json={
                "sku": "YER-1",
                "name": "Yerba 1 kg",
                "unit_price": "100.00",
                "vat_rate": "21.0",
                "unit_cost": "60.00",
                "min_stock": "40",
            },
        ),
        201,
    )
    harina = ok(
        admin.post(
            f"{API}/products",
            json={
                "sku": "HAR-1",
                "name": "Harina 1 kg",
                "unit_price": "200.00",
                "vat_rate": "10.5",
                "unit_cost": "120.00",
            },
        ),
        201,
    )
    w.ids |= {"yerba": yerba["id"], "harina": harina["id"]}
    for product_id, qty in ((yerba["id"], "50"), (harina["id"], "30")):  # 3000 + 3600
        ok(
            admin.post(
                f"{API}/stock/adjustments",
                json={"product_id": product_id, "quantity": qty, "reason": "initial_load"},
            ),
            201,
        )
    ok(
        admin.post(
            f"{API}/stock/adjustments",  # damage: 2 x 60 = 120
            json={"product_id": yerba["id"], "quantity": "-2", "reason": "damage"},
        ),
        201,
    )

    # Customers (seller) --------------------------------------------------------------------
    for key, name, condition, cuit, terms in (
        ("ri", "Almacén Don José SRL", "responsable_inscripto", valid_cuit("30", 71234567), 30),
        ("mono", "Kiosco La Esquina", "monotributo", valid_cuit("20", 23456789), 0),
        ("cf", "Consumidor Final", "consumidor_final", None, 0),
    ):
        customer = ok(
            seller.post(
                f"{API}/customers",
                json={
                    "name": name,
                    "tax_condition": condition,
                    "cuit": cuit,
                    "payment_terms_days": terms,
                },
            ),
            201,
        )
        w.ids[key] = customer["id"]

    # Order 1 (RI): 10 yerba + 5 harina, delivered in two notes -----------------------------
    order1 = ok(
        seller.post(
            f"{API}/orders",
            json={
                "customer_id": w.ids["ri"],
                "lines": [
                    {"product_id": yerba["id"], "quantity": "10"},
                    {"product_id": harina["id"], "quantity": "5"},
                ],
            },
        ),
        201,
    )
    ok(seller.post(f"{API}/orders/{order1['id']}/confirm"))
    line = {ln["product_id"]: ln["id"] for ln in order1["lines"]}
    note1a = ok(
        seller.post(
            f"{API}/orders/{order1['id']}/delivery-notes",
            json={
                "lines": [
                    {"order_line_id": line[yerba["id"]], "quantity": "6"},
                    {"order_line_id": line[harina["id"]], "quantity": "5"},
                ]
            },
        ),
        201,
    )
    note1b = ok(
        seller.post(
            f"{API}/orders/{order1['id']}/delivery-notes",
            json={
                "lines": [
                    {"order_line_id": line[yerba["id"]], "quantity": "4"},
                ]
            },
        ),
        201,
    )
    w.ids |= {"order1": order1["id"], "note1a": note1a["id"], "note1b": note1b["id"]}

    # Note 1a is invoiced from the order page with D2's "Facturar" button (web, HTMX) -------
    page = seller.get(f"/orders/{order1['id']}")
    assert page.status_code == 200
    assert f'hx-post="/delivery-notes/{note1a["id"]}/invoice"' in page.text
    assert f'hx-post="/delivery-notes/{note1b["id"]}/invoice"' in page.text
    clicked = seller.post(f"/delivery-notes/{note1a['id']}/invoice", headers=HX)
    assert clicked.status_code == 204
    invoice1a_id = int(clicked.headers["HX-Redirect"].rsplit("/", 1)[1])
    page = seller.get(f"/orders/{order1['id']}")
    assert f'hx-post="/delivery-notes/{note1a["id"]}/invoice"' not in page.text  # now "Facturado"
    assert "Facturado" in page.text
    assert f'hx-post="/delivery-notes/{note1b["id"]}/invoice"' in page.text

    # Remaining invoices through the API ------------------------------------------------------
    w.invoices["1a"] = ok(seller.get(f"{API}/invoices/{invoice1a_id}"))
    w.invoices["1b"] = ok(seller.post(f"{API}/delivery-notes/{note1b['id']}/invoice"), 201)
    for key, customer_key, product, qty in (
        ("mono", "mono", harina, "2"),
        ("cf", "cf", yerba, "3"),
    ):
        order = ok(
            seller.post(
                f"{API}/orders",
                json={
                    "customer_id": w.ids[customer_key],
                    "lines": [{"product_id": product["id"], "quantity": qty}],
                },
            ),
            201,
        )
        ok(seller.post(f"{API}/orders/{order['id']}/confirm"))
        note = ok(
            seller.post(
                f"{API}/orders/{order['id']}/delivery-notes",
                json={"lines": [{"order_line_id": order["lines"][0]["id"], "quantity": qty}]},
            ),
            201,
        )
        w.invoices[key] = ok(seller.post(f"{API}/delivery-notes/{note['id']}/invoice"), 201)

    # Receipts: RI pays 1000 cash on 1a (partial) and 484 by transfer on 1b (full);
    # Monotributo pays 442 by transfer (full); CF pays nothing ------------------------------
    for customer_key, method, allocations in (
        ("ri", "cash", [(w.invoices["1a"]["id"], "1000.00")]),
        ("ri", "bank_transfer", [(w.invoices["1b"]["id"], "484.00")]),
        ("mono", "bank_transfer", [(w.invoices["mono"]["id"], "442.00")]),
    ):
        ok(
            seller.post(
                f"{API}/receipts",
                json={
                    "customer_id": w.ids[customer_key],
                    "method": method,
                    "allocations": [{"invoice_id": i, "amount": a} for i, a in allocations],
                },
            ),
            201,
        )
    yield w


# Helpers ----------------------------------------------------------------------------------


def account_ids(w: World) -> dict[str, int]:
    accounts = ok(w.accountant.get(f"{API}/accounts", params={"flat": "true"}))["items"]
    return {a["code"]: a["id"] for a in accounts}


def closing(w: World, code: str) -> Decimal:
    """Ledger closing balance (debit - credit) of an account, as the accountant sees it."""
    return D(ok(w.accountant.get(f"{API}/ledger/{account_ids(w)[code]}"))["closing_balance"])


def all_journal_entries(w: World) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    while True:
        page = ok(
            w.accountant.get(f"{API}/journal-entries", params={"limit": 50, "offset": len(entries)})
        )
        entries += page["items"]
        if len(entries) >= page["total"]:
            return entries


# The documents ----------------------------------------------------------------------------


def test_invoices_have_the_right_letter_totals_and_legend(world: World) -> None:
    inv = world.invoices
    # 1a: 6 yerba (600 + 126) + 5 harina (1000 + 105) = 1831.00
    assert (
        inv["1a"]["letter"],
        inv["1a"]["net_total"],
        inv["1a"]["vat_21"],
        inv["1a"]["vat_10_5"],
        inv["1a"]["total"],
    ) == ("A", "1600.00", "126.00", "105.00", "1831.00")
    assert (inv["1b"]["letter"], inv["1b"]["total"]) == ("A", "484.00")  # 400 + 84
    assert (inv["mono"]["letter"], inv["mono"]["total"]) == ("A", "442.00")  # 400 + 42
    assert (inv["cf"]["letter"], inv["cf"]["total"]) == ("B", "363.00")  # 300 + 63
    assert LEY_27618 in inv["mono"]["legend"]
    assert inv["1a"]["legend"] is None and inv["cf"]["legend"] is None
    printable = world.accountant.get(f"/invoices/{inv['mono']['id']}").text
    assert LEY_27618 in printable
    assert LEY_27618 not in world.accountant.get(f"/invoices/{inv['1a']['id']}").text


def test_invoice_status_and_balances_after_receipts(world: World) -> None:
    def state(key: str) -> tuple[str, str]:
        invoice = ok(world.accountant.get(f"{API}/invoices/{world.invoices[key]['id']}"))
        return invoice["status"], invoice["balance"]

    assert state("1a") == ("open", "831.00")
    assert state("1b") == ("paid", "0.00")
    assert state("mono") == ("paid", "0.00")
    assert state("cf") == ("open", "363.00")
    order = ok(world.seller.get(f"{API}/orders/{world.ids['order1']}"))
    assert order["status"] == "delivered"


# Cross-checks -----------------------------------------------------------------------------


def test_journal_is_balanced_and_complete(world: World) -> None:
    entries = all_journal_entries(world)

    assert sum(D(e["total_debit"]) for e in entries) == sum(D(e["total_credit"]) for e in entries)
    assert all(e["total_debit"] == e["total_credit"] for e in entries)
    by_source: dict[str, int] = {}
    for e in entries:
        by_source[e["source_type"]] = by_source.get(e["source_type"], 0) + 1
    # 3 adjustments, 4 delivery notes, 4 invoices, 3 receipts
    assert by_source == {"stock_adjustment": 3, "delivery_note": 4, "invoice": 4, "receipt": 3}


def test_receivables_ledger_statements_and_report_agree(world: World) -> None:
    ledger = closing(world, "1.1.3.01")  # Deudores por ventas: 3120 - 1926 = 1194
    statements = sum(
        D(ok(world.accountant.get(f"{API}/customers/{world.ids[c]}/statement"))["closing_balance"])
        for c in ("ri", "mono", "cf")
    )
    report = ok(world.accountant.get(f"{API}/reports/receivables", params={"as_of": str(today())}))

    assert ledger == statements == D(report["totals"]["balance"]) == D("1194.00")
    by_customer = {row["customer_id"]: D(row["balance"]) for row in report["rows"]}
    assert by_customer == {world.ids["ri"]: D("831.00"), world.ids["cf"]: D("363.00")}


def test_inventory_ledger_equals_valued_stock_movements(world: World, db: Session) -> None:
    """Mercaderías = sum(quantity x unit_cost) of every movement, NOT stock_qty x current cost."""
    valued = db.scalar(select(func.sum(StockMovement.quantity * StockMovement.unit_cost)))
    via_api = sum(
        D(m["value"])
        for m in ok(world.admin.get(f"{API}/stock/movements", params={"limit": 200}))["items"]
    )
    # 3000 + 3600 (loads) - 120 (damage) - 13 x 60 - 7 x 120 (deliveries) = 4860
    assert closing(world, "1.1.4.01") == D(valued) == via_api == D("4860.00")


def test_stock_quantities_match_the_ledger(world: World, db: Session) -> None:
    for key, expected in (("yerba", D("35")), ("harina", D("23"))):  # 50-2-13, 30-7
        product = ok(world.admin.get(f"{API}/products/{world.ids[key]}"))
        moved = db.scalar(
            select(func.sum(StockMovement.quantity)).where(
                StockMovement.product_id == world.ids[key]
            )
        )
        assert D(product["stock_qty"]) == D(moved) == expected


def test_sales_and_vat_accounts_match_the_sales_report(world: World) -> None:
    day = str(today())
    report = ok(
        world.accountant.get(
            f"{API}/reports/sales", params={"from": day, "to": day, "group_by": "customer"}
        )
    )
    totals = report["totals"]

    # Income and liability accounts carry credit balances (debit - credit < 0).
    assert -closing(world, "4.1.01") == D(totals["net"]) == D("2700.00")
    assert -closing(world, "2.1.3.01") == D(totals["vat_21"]) == D("273.00")
    assert -closing(world, "2.1.3.02") == D(totals["vat_10_5"]) == D("147.00")
    assert D(totals["total"]) == D("3120.00")
    assert totals["invoice_count"] == 4


def test_cash_bank_cogs_and_equity_accounts(world: World) -> None:
    assert closing(world, "1.1.1.01") == D("1000.00")  # Caja: cash receipt
    assert closing(world, "1.1.1.02") == D("926.00")  # Banco: 484 + 442
    assert closing(world, "5.1.01") == D("1620.00")  # CMV: 780 + 840
    assert closing(world, "5.2.01") == D("120.00")  # Faltantes y roturas
    assert closing(world, "3.1.01") == D("-6600.00")  # Capital (credit): initial loads


def test_trial_balance_sums_to_zero(world: World) -> None:
    postable = [
        a
        for a in ok(world.accountant.get(f"{API}/accounts", params={"flat": "true"}))["items"]
        if a["is_postable"]
    ]
    balances = {a["code"]: closing(world, a["code"]) for a in postable}

    assert sum(balances.values()) == 0
