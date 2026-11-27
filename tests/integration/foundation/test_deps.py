from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import main
from app.core.db import get_db
from app.core.deps import CurrentUser, require_csrf, require_roles
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


def test_every_unsafe_web_route_requires_csrf() -> None:
    """Guard for Wave 2: any POST/PATCH/PUT/DELETE outside /api must depend on require_csrf."""
    application = main.create_app(session_factory=sessionmaker())
    unprotected = [
        route.path
        for route in application.routes
        if isinstance(route, APIRoute)
        and not route.path.startswith("/api/")
        and route.methods - {"GET", "HEAD", "OPTIONS"}
        and require_csrf not in {dep.call for dep in route.dependant.dependencies}
    ]
    assert unprotected == []


def test_home_redirects_by_role(client: TestClient, client_as: ClientAs) -> None:
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client_as("seller").get("/", follow_redirects=False).headers["location"] == "/orders"
    accountant = client_as("accountant").get("/", follow_redirects=False)
    assert accountant.headers["location"] == "/accounting/journal"


def test_layout_renders_role_nav_and_csrf_header(client_as: ClientAs) -> None:
    html = client_as("accountant").get("/_test/page").text

    assert "Libro diario" in html
    assert "Usuarios" not in html
    assert '"X-CSRF-Token": "' in html
    assert "htmx.min.js" in html
