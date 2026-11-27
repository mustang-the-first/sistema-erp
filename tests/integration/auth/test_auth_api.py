from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import SESSION_COOKIE, hash_token
from app.models.enums import UserRole
from app.models.users import UserSession
from app.services.auth import INVALID_CREDENTIALS_MESSAGE
from tests.conftest import ClientAs
from tests.factories import make_user
from tests.factories.users import DEFAULT_PASSWORD

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


def test_login_returns_user_and_sets_session_cookie(client: TestClient, db: Session) -> None:
    user = make_user(db, email="ana@example.com", full_name="Ana", role=UserRole.ACCOUNTANT)

    response = client.post(LOGIN, json={"email": "Ana@Example.com", "password": DEFAULT_PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert {k: body[k] for k in ("id", "email", "full_name", "role")} == {
        "id": user.id,
        "email": "ana@example.com",
        "full_name": "Ana",
        "role": "accountant",
    }
    assert "password_hash" not in body
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Secure" not in set_cookie
    token = response.cookies[SESSION_COOKIE]
    stored = db.scalars(select(UserSession.token_hash).where(UserSession.user_id == user.id)).all()
    assert stored == [hash_token(token)]


def test_login_failures_answer_401_with_identical_body(client: TestClient, db: Session) -> None:
    make_user(db, email="ana@example.com")
    make_user(db, email="off@example.com", is_active=False)
    attempts = [
        {"email": "ana@example.com", "password": "wrong-password"},
        {"email": "nobody@example.com", "password": DEFAULT_PASSWORD},
        {"email": "off@example.com", "password": DEFAULT_PASSWORD},
    ]

    responses = [client.post(LOGIN, json=body) for body in attempts]

    assert {r.status_code for r in responses} == {401}
    assert all(
        r.json() == {"detail": INVALID_CREDENTIALS_MESSAGE, "code": "invalid_credentials"}
        for r in responses
    )
    assert all(SESSION_COOKIE not in r.cookies for r in responses)


def test_login_with_missing_fields_is_422(client: TestClient) -> None:
    assert client.post(LOGIN, json={"email": "ana@example.com"}).status_code == 422


def test_me_after_login_returns_current_user(client: TestClient, db: Session) -> None:
    user = make_user(db)
    client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})

    response = client.get(ME)

    assert response.status_code == 200
    assert response.json()["id"] == user.id


def test_me_anonymous_is_401(client: TestClient) -> None:
    response = client.get(ME)

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_me_works_for_every_role(client_as: ClientAs) -> None:
    for role in UserRole:
        assert client_as(role).get(ME).json()["role"] == role.value


def test_logout_deletes_session_and_clears_cookie(client: TestClient, db: Session) -> None:
    user = make_user(db)
    client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})

    response = client.post(LOGOUT)

    assert response.status_code == 204
    assert f'{SESSION_COOKIE}="";' in response.headers["set-cookie"]
    assert db.scalars(select(UserSession).where(UserSession.user_id == user.id)).all() == []
    assert client.get(ME).status_code == 401


def test_logout_of_client_as_session_revokes_it(client_as: ClientAs) -> None:
    seller = client_as("seller")
    token = seller.cookies[SESSION_COOKIE]

    assert seller.post(LOGOUT).status_code == 204

    # Even replaying the old cookie no longer authenticates.
    seller.cookies.set(SESSION_COOKIE, token)
    assert seller.get(ME).status_code == 401


def test_logout_when_already_logged_out_is_204(client: TestClient) -> None:
    assert client.post(LOGOUT).status_code == 204
    client.cookies.set(SESSION_COOKIE, "stale-token")
    assert client.post(LOGOUT).status_code == 204
