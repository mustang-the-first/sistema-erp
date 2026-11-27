import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import CSRF_HEADER, SESSION_COOKIE
from app.models.enums import UserRole
from app.models.users import User
from app.repositories.users import UserRepository
from tests.conftest import ClientAs
from tests.factories import make_user
from tests.factories.users import DEFAULT_PASSWORD

# login / logout ----------------------------------------------------------------------------


def test_login_page_renders_form(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert 'action="/login"' in response.text
    assert 'name="password"' in response.text


def test_login_page_when_logged_in_redirects_home(client_as: ClientAs) -> None:
    response = client_as("seller").get("/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_page_with_stale_cookie_clears_it(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, "expired-token")

    response = client.get("/login")

    assert response.status_code == 200
    assert f'{SESSION_COOKIE}="";' in response.headers["set-cookie"]


def test_web_login_logout_flow(client: TestClient, db: Session) -> None:
    user = make_user(db, email="admin@example.com", full_name="Ada Admin", role=UserRole.ADMIN)

    login = client.post(
        "/login",
        data={"email": "admin@example.com", "password": DEFAULT_PASSWORD},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/"
    assert SESSION_COOKIE in login.cookies
    assert "HttpOnly" in login.headers["set-cookie"]

    home = client.get("/", follow_redirects=False)
    assert home.headers["location"] == "/orders"
    assert client.get("/api/v1/auth/me").json()["id"] == user.id

    assert client.get("/login", follow_redirects=False).status_code == 303

    # base.html renders the CSRF token that htmx sends with hx-post="/logout".
    page = client.get("/users").text
    assert "Ada Admin" in page and 'hx-post="/logout"' in page
    match = re.search(r'"X-CSRF-Token": "([0-9a-f]+)"', page)
    assert match is not None
    logout = client.post("/logout", headers={CSRF_HEADER: match.group(1), "HX-Request": "true"})
    assert logout.status_code == 200
    assert logout.headers["HX-Redirect"] == "/login"
    assert client.get("/api/v1/auth/me").status_code == 401


def test_web_login_with_bad_credentials_rerenders_with_error(
    client: TestClient, db: Session
) -> None:
    make_user(db, email="vende@example.com")

    response = client.post("/login", data={"email": "vende@example.com", "password": "nope-nope"})

    assert response.status_code == 401
    assert "Email o contraseña incorrectos." in response.text
    assert 'value="vende@example.com"' in response.text
    assert SESSION_COOKIE not in response.cookies


def test_web_logout_without_session_still_redirects(client: TestClient) -> None:
    response = client.post("/logout")

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/login"


def test_web_logout_without_csrf_header_is_rejected(client_as: ClientAs) -> None:
    seller = client_as("seller")
    del seller.headers["X-CSRF-Token"]

    assert seller.post("/logout").status_code == 403


# users -------------------------------------------------------------------------------------


def test_users_page_lists_users_for_admin(client_as: ClientAs, db: Session) -> None:
    make_user(db, email="listed@example.com", role=UserRole.ACCOUNTANT)

    response = client_as("admin").get("/users")

    assert response.status_code == 200
    assert "listed@example.com" in response.text
    assert "Contador" in response.text
    assert 'hx-post="/users"' in response.text


def test_users_pages_are_forbidden_for_non_admins(client_as: ClientAs, db: Session) -> None:
    target = make_user(db)
    seller = client_as("seller")
    db.commit()  # error responses roll back the request savepoint: keep the arranged rows

    assert seller.get("/users").status_code == 403
    assert seller.get(f"/users/{target.id}").status_code == 403
    assert seller.post("/users", data={}).status_code == 403
    assert seller.patch(f"/users/{target.id}", data={}).status_code == 403


def test_users_page_anonymous_redirects_to_login(client: TestClient) -> None:
    response = client.get("/users", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_users_page_paginates(client_as: ClientAs, db: Session) -> None:
    admin = client_as("admin")
    for _ in range(51):
        make_user(db)

    first = admin.get("/users").text
    second = admin.get("/users", params={"offset": 50}).text

    assert 'href="/users?offset=50"' in first
    assert 'href="/users?offset=0"' in second


def test_create_user_form_success_rerenders_panel(client_as: ClientAs, db: Session) -> None:
    response = client_as("admin").post(
        "/users",
        data={
            "email": "Form@Example.com",
            "full_name": "Form User",
            "role": "accountant",
            "password": "s3cret-pass",
        },
    )

    assert response.status_code == 200
    assert 'id="users-panel"' in response.text
    assert "Usuario form@example.com creado." in response.text
    created = UserRepository(db).get_by_email("form@example.com")
    assert created is not None and created.role is UserRole.ACCOUNTANT


def test_create_user_form_error_keeps_values(client_as: ClientAs, db: Session) -> None:
    make_user(db, email="dup@example.com")

    response = client_as("admin").post(
        "/users",
        data={
            "email": "dup@example.com",
            "full_name": "Dup User",
            "role": "seller",
            "password": "s3cret-pass",
        },
    )

    assert response.status_code == 200
    assert "Ya existe un usuario con ese email." in response.text
    assert 'value="Dup User"' in response.text


def test_edit_user_page_renders_form(client_as: ClientAs, db: Session) -> None:
    target = make_user(db, full_name="Edit Me")

    response = client_as("admin").get(f"/users/{target.id}")

    assert response.status_code == 200
    assert f'hx-patch="/users/{target.id}"' in response.text
    assert 'value="Edit Me"' in response.text


def test_edit_user_form_saves_changes(client_as: ClientAs, db: Session) -> None:
    target = make_user(db)

    response = client_as("admin").patch(
        f"/users/{target.id}",
        data={"full_name": "Edited", "role": "accountant", "is_active": "false", "password": ""},
    )

    assert response.status_code == 200
    assert "Cambios guardados." in response.text
    assert (target.full_name, target.role, target.is_active) == (
        "Edited",
        UserRole.ACCOUNTANT,
        False,
    )


def test_edit_user_form_rejects_self_deactivation(client_as: ClientAs) -> None:
    admin = client_as("admin")
    me: User = admin.user  # type: ignore[attr-defined]

    response = admin.patch(
        f"/users/{me.id}",
        data={"full_name": me.full_name, "role": "admin", "is_active": "false"},
    )

    assert response.status_code == 200
    assert "No podés desactivar tu propio usuario." in response.text
    assert me.is_active is True
