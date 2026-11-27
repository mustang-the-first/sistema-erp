"""Role matrix for every web view (docs/plan.md, "Vistas web").

Each (method, path) of ``app.main.WEB_ROUTERS`` is requested for real as every role; the test
does not look at the navigation, nor at how FastAPI wires dependencies. ``ROUTES`` must list
every web route: a new route without an entry here fails ``test_matrix_covers_every_web_route``.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import WEB_ROUTERS
from app.models.accounting import Account
from tests.conftest import ClientAs
from tests.factories import (
    make_customer,
    make_delivery_note,
    make_invoice,
    make_order,
    make_product,
    make_receipt,
    make_supplier,
    make_user,
)

V, C = "seller", "accountant"  # admin is always allowed
ROLES = ("admin", V, C)
SAFE = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class WebRoute:
    method: str
    path: str
    roles: frozenset[str]  # besides admin

    @property
    def id(self) -> str:
        return f"{self.method} {self.path}"


def r(method: str, path: str, *roles: str) -> WebRoute:
    return WebRoute(method, path, frozenset(roles))


PUBLIC = {("GET", "/"), ("GET", "/login"), ("POST", "/login"), ("POST", "/logout")}

ROUTES: tuple[WebRoute, ...] = (
    # Users (admin)
    r("GET", "/users"),
    r("POST", "/users"),
    r("GET", "/users/{user_id}"),
    r("PATCH", "/users/{user_id}"),
    # Catalog
    r("GET", "/products", V, C),
    r("POST", "/products"),
    r("GET", "/products/{product_id}"),  # edit form: admin only (list is open to V, C)
    r("PATCH", "/products/{product_id}"),
    r("GET", "/customers", V),
    r("POST", "/customers", V),
    r("GET", "/customers/{customer_id}", V),
    r("PATCH", "/customers/{customer_id}", V),
    r("GET", "/suppliers"),
    r("POST", "/suppliers"),
    r("GET", "/suppliers/{supplier_id}"),
    r("PATCH", "/suppliers/{supplier_id}"),
    # Stock (admin)
    r("GET", "/stock"),
    r("POST", "/stock/adjustments"),
    # Orders and delivery notes
    r("GET", "/orders", V),
    r("GET", "/orders/new", V),
    r("GET", "/orders/{order_id}", V),
    r("POST", "/orders", V),
    r("PATCH", "/orders/{order_id}", V),
    r("POST", "/orders/{order_id}/confirm", V),
    r("POST", "/orders/{order_id}/cancel", V),
    r("POST", "/orders/{order_id}/delivery-notes", V),
    r("GET", "/delivery-notes", V, C),
    r("GET", "/delivery-notes/{note_id}", V, C),
    # Invoices
    r("GET", "/invoices", V, C),
    r("GET", "/invoices/{invoice_id}", V, C),
    r("POST", "/delivery-notes/{delivery_note_id}/invoice", V),
    # Accounting
    r("GET", "/accounting/accounts", C),
    r("POST", "/accounting/accounts", C),
    r("PATCH", "/accounting/accounts/{account_id}", C),
    r("GET", "/accounting/journal", C),
    r("GET", "/accounting/ledger", C),
    # Collections
    r("GET", "/receipts/new", V, C),
    r("GET", "/receipts/new/open-invoices", V, C),
    r("POST", "/receipts", V, C),
    r("GET", "/receipts/{receipt_id}", V, C),
    r("GET", "/customers/{customer_id}/statement", V, C),
    # Reports
    r("GET", "/reports/sales", V, C),
    r("GET", "/reports/low-stock", V),
    r("GET", "/reports/receivables", C),
)


@pytest.fixture
def ids(db: Session) -> dict[str, int]:
    """One existing row for every path parameter."""
    customer = make_customer(db)
    product = make_product(db, stock=Decimal("10"))
    invoice = make_invoice(db, customer=customer, lines=[(product, Decimal("1"))])
    receipt = make_receipt(db, customer=customer, allocations=[(invoice, Decimal("1.00"))])
    open_order = make_order(db, customer=customer, lines=[(product, Decimal("2"))])
    pending_note = make_delivery_note(db, order=open_order)
    account = db.scalars(select(Account).where(Account.code == "1.1.1.01")).one()
    return {
        "user_id": make_user(db).id,
        "product_id": product.id,
        "customer_id": customer.id,
        "supplier_id": make_supplier(db).id,
        "order_id": open_order.id,
        "note_id": invoice.delivery_note_id,
        "invoice_id": invoice.id,
        "delivery_note_id": pending_note.id,
        "receipt_id": receipt.id,
        "account_id": account.id,
    }


def _request(client: TestClient, route: WebRoute, ids: dict[str, int]) -> int:
    url = route.path.format(**ids)
    return client.request(route.method, url, follow_redirects=False).status_code


def test_matrix_covers_every_web_route() -> None:
    declared = {(route.method, route.path) for route in ROUTES} | PUBLIC
    actual = {
        (method, route.path)
        for module in WEB_ROUTERS
        for route in module.router.routes
        for method in route.methods  # type: ignore[attr-defined]
    }
    assert actual - declared == set(), "web routes without an entry in the role matrix"
    assert declared - actual == set(), "role matrix entries for routes that do not exist"


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("route", ROUTES, ids=lambda route: route.id)
def test_web_route_access_by_role(
    client_as: ClientAs, ids: dict[str, int], route: WebRoute, role: str
) -> None:
    allowed = role == "admin" or role in route.roles

    status = _request(client_as(role), route, ids)

    if not allowed:
        assert status == 403
    elif route.method in SAFE:
        assert status == 200
    else:  # allowed write with an empty form: may be rejected as invalid, never as forbidden
        assert status not in (401, 403)


@pytest.mark.parametrize("route", ROUTES, ids=lambda route: route.id)
def test_web_route_anonymous_is_sent_to_login(
    client: TestClient, ids: dict[str, int], route: WebRoute
) -> None:
    response = client.request(route.method, route.path.format(**ids), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
