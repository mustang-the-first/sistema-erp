import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import main
from app.core.db import get_db
from app.core.deps import CurrentUser, require_csrf, require_roles
from app.core.errors import Forbidden, NotFound
from app.core.security import CSRF_HEADER
from app.core.templating import templates
from app.models.enums import UserRole
from app.models.users import User, UserSession
from tests.conftest import ClientAs


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    @app.get("/api/v1/_test/me")
    def api_me(user: CurrentUser) -> dict[str, str]:
        return {"email": user.email}

    @app.get("/_test/me")
    def web_me(user: CurrentUser) -> dict[str, str]:
        return {"email": user.email}

    @app.get("/api/v1/_test/sellers-only", dependencies=[Depends(require_roles(UserRole.SELLER))])
    def sellers_only() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/_test/form", dependencies=[Depends(require_csrf)])
    def form() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/_test/page", response_class=HTMLResponse)
    def page(request: Request, user: CurrentUser) -> HTMLResponse:
        return templates.TemplateResponse(request, "base.html", {})

    return app


def test_anonymous_api_request_is_401_json(client: TestClient) -> None:
    response = client.get("/api/v1/_test/me")

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_anonymous_web_request_redirects_to_login(client: TestClient) -> None:
    response = client.get("/_test/me", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_anonymous_htmx_request_gets_hx_redirect(client: TestClient) -> None:
    response = client.get("/_test/me", headers={"HX-Request": "true"})

    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"


def test_logged_in_user_is_resolved(client_as: ClientAs) -> None:
    seller = client_as("seller")

    assert seller.get("/api/v1/_test/me").json() == {"email": seller.user.email}  # type: ignore[attr-defined]


def test_expired_session_is_rejected(client_as: ClientAs, db: Session) -> None:
    seller = client_as("seller")
    session_row = db.scalars(select(UserSession).where(UserSession.user_id == seller.user.id)).one()  # type: ignore[attr-defined]
    session_row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()

    assert seller.get("/api/v1/_test/me").status_code == 401


def test_inactive_user_is_rejected(client_as: ClientAs, db: Session) -> None:
    seller = client_as("seller")
    user: User = seller.user  # type: ignore[attr-defined]
    user.is_active = False
    db.flush()

    assert seller.get("/api/v1/_test/me").status_code == 401


@pytest.mark.parametrize(("role", "status"), [("seller", 200), ("admin", 200), ("accountant", 403)])
def test_require_roles_lets_admin_through(client_as: ClientAs, role: str, status: int) -> None:
    response = client_as(role).get("/api/v1/_test/sellers-only")

    assert response.status_code == status
    if status == 403:
        assert response.json()["code"] == "forbidden"


def test_unsafe_web_request_needs_csrf_header(client_as: ClientAs) -> None:
    seller = client_as("seller")

    assert seller.post("/_test/form").status_code == 200
    rejected = seller.post("/_test/form", headers={CSRF_HEADER: "forged"})
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "csrf_failed"


def test_anonymous_post_needs_no_csrf(client: TestClient) -> None:
    assert client.post("/_test/form").status_code == 200


def test_web_routers_get_csrf_protection_at_include_time(
    client_as: ClientAs, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = APIRouter()

    @probe.post("/_test/probe")
    def probe_post() -> dict[str, bool]:
        return {"ok": True}

    monkeypatch.setattr(main, "WEB_ROUTERS", (SimpleNamespace(router=probe),))
    probe_app = main.create_app(session_factory=sessionmaker())
    probe_app.dependency_overrides[get_db] = lambda: db
    seller = client_as("seller")
    probe_client = TestClient(probe_app, headers=dict(seller.headers), cookies=seller.cookies)

    assert probe_client.post("/_test/probe").status_code == 200
    assert probe_client.post("/_test/probe", headers={CSRF_HEADER: "forged"}).status_code == 403


UNSAFE_WEB_ROUTES = sorted(
    (method, route.path)
    for module in main.WEB_ROUTERS
    for route in module.router.routes
    for method in route.methods  # type: ignore[attr-defined]
    if method not in {"GET", "HEAD", "OPTIONS"}
)


def test_unsafe_web_routes_exist() -> None:
    """The CSRF guard below must actually check something (it once checked nothing)."""
    assert len(UNSAFE_WEB_ROUTES) >= 20


@pytest.mark.parametrize(("method", "path"), UNSAFE_WEB_ROUTES, ids=lambda v: str(v))
def test_every_unsafe_web_route_rejects_forged_csrf(
    client_as: ClientAs, method: str, path: str
) -> None:
    """Real requests, as admin (passes every role check), with a forged token: all must be 403.
    Behavioural on purpose: it does not depend on how FastAPI composes router dependencies."""
    url = re.sub(r"{[^}]+}", "1", path)

    response = client_as("admin").request(method, url, headers={CSRF_HEADER: "forged"})

    assert response.status_code == 403


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize(
    ("role", "landing"),
    [
        (None, "/login"),
        ("admin", "/orders"),
        ("seller", "/orders"),
        ("accountant", "/accounting/journal"),
    ],
)
def test_home_redirects_by_role(
    client: TestClient, client_as: ClientAs, method: str, role: str | None, landing: str
) -> None:
    http = client if role is None else client_as(role)

    response = http.request(method, "/", follow_redirects=False)

    assert (response.status_code, response.headers["location"]) == (303, landing)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_health_answers_get_and_head(client: TestClient, method: str) -> None:
    assert client.request(method, "/health").status_code == 200


def test_layout_renders_role_nav_and_csrf_header(client_as: ClientAs) -> None:
    html = client_as("accountant").get("/_test/page").text

    assert "Libro diario" in html
    assert "Usuarios" not in html
    assert '"X-CSRF-Token": "' in html
    assert "htmx.min.js" in html


def test_failed_request_does_not_roll_back_arranged_data(client_as: ClientAs, db: Session) -> None:
    """Regression: an error response must undo only the request, never the test's setup."""
    seller = client_as("seller")
    seller_id = seller.user.id  # type: ignore[attr-defined]

    assert seller.get("/api/v1/_test/sellers-only").status_code == 200
    accountant = client_as("accountant")
    assert accountant.get("/api/v1/_test/sellers-only").status_code == 403  # failed request

    assert db.get(User, seller_id) is not None
    assert seller.get("/api/v1/_test/me").status_code == 200  # session row still there
    assert accountant.get("/api/v1/_test/me").status_code == 200


@pytest.fixture
def error_app(app: FastAPI) -> FastAPI:
    @app.get("/_test/missing")
    def missing() -> None:
        raise NotFound("Thing 9 not found")

    @app.get("/_test/forbidden-thing")
    def forbidden_thing() -> None:
        raise Forbidden("nope")

    return app


def test_htmx_not_found_is_flashed(error_app: FastAPI) -> None:
    response = TestClient(error_app).get("/_test/missing", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#flash"
    assert "Thing 9 not found" in response.text


def test_plain_web_get_error_keeps_status_and_json(error_app: FastAPI) -> None:
    response = TestClient(error_app).get("/_test/missing")

    assert (response.status_code, response.json()["code"]) == (404, "not_found")


def test_forbidden_is_never_flashed_as_success(error_app: FastAPI) -> None:
    response = TestClient(error_app).get("/_test/forbidden-thing", headers={"HX-Request": "true"})

    assert response.status_code == 403
    assert "HX-Retarget" not in response.headers


@pytest.fixture
def typed_app(app: FastAPI) -> FastAPI:
    @app.get("/api/v1/_test/typed")
    def api_typed(quantity: Annotated[int, Query(gt=0)]) -> dict[str, int]:
        return {"quantity": quantity}

    @app.get("/_test/typed")
    def web_typed(quantity: Annotated[int, Query(gt=0)]) -> dict[str, int]:
        return {"quantity": quantity}

    return app


@pytest.mark.parametrize("value", ["abc", "0"])
def test_schema_validation_errors_use_detail_and_code(typed_app: FastAPI, value: str) -> None:
    response = TestClient(typed_app).get("/api/v1/_test/typed", params={"quantity": value})

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"detail", "code"}
    assert body["code"] == "validation_error"
    assert body["detail"].startswith("quantity: ")


def test_missing_field_is_reported_by_name(typed_app: FastAPI) -> None:
    body = TestClient(typed_app).get("/api/v1/_test/typed").json()

    assert body == {"detail": "quantity: Field required", "code": "validation_error"}


def test_htmx_schema_validation_error_is_flashed(typed_app: FastAPI) -> None:
    response = TestClient(typed_app).get(
        "/_test/typed", params={"quantity": "abc"}, headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#flash"
    assert "quantity" in response.text
