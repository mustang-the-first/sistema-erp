import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.enums import UserRole
from app.models.users import User
from app.repositories.users import SessionRepository
from tests.conftest import ClientAs
from tests.factories import make_user

USERS = "/api/v1/users"
NEW_USER = {
    "email": "Nuevo@Example.com",
    "full_name": "Nuevo Usuario",
    "role": "seller",
    "password": "s3cret-pass",
}


def _endpoints(user_id: int) -> list[tuple[str, str, dict[str, object] | None]]:
    return [
        ("GET", USERS, None),
        ("POST", USERS, NEW_USER),
        ("GET", f"{USERS}/{user_id}", None),
        ("PATCH", f"{USERS}/{user_id}", {"full_name": "X"}),
    ]


@pytest.mark.parametrize("role", ["seller", "accountant"])
def test_users_endpoints_are_403_for_non_admins(
    client_as: ClientAs, db: Session, role: str
) -> None:
    target = make_user(db)
    http = client_as(role)
    db.commit()  # error responses roll back the request savepoint: keep the arranged rows

    for method, url, body in _endpoints(target.id):
        response = http.request(method, url, json=body)
        assert response.status_code == 403, (method, url)
        assert response.json()["code"] == "forbidden"


def test_users_endpoints_are_401_for_anonymous(client: TestClient, db: Session) -> None:
    target = make_user(db)

    for method, url, body in _endpoints(target.id):
        assert client.request(method, url, json=body).status_code == 401, (method, url)


def test_list_users_is_paginated(client_as: ClientAs, db: Session) -> None:
    admin = client_as("admin")
    for _ in range(3):
        make_user(db)

    first = admin.get(USERS, params={"limit": 2, "offset": 0}).json()
    everything = admin.get(USERS, params={"limit": 200}).json()

    assert len(first["items"]) == 2
    assert first["total"] == everything["total"] >= 4
    assert len(everything["items"]) == everything["total"]
    assert all("password_hash" not in item for item in everything["items"])


@pytest.mark.parametrize("params", [{"limit": 201}, {"limit": 0}, {"offset": -1}])
def test_list_users_with_out_of_range_pagination_is_422(
    client_as: ClientAs, params: dict[str, int]
) -> None:
    assert client_as("admin").get(USERS, params=params).status_code == 422


def test_create_user_returns_201_without_password_hash(client_as: ClientAs, db: Session) -> None:
    response = client_as("admin").post(USERS, json=NEW_USER)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "nuevo@example.com"
    assert body["role"] == "seller"
    assert body["is_active"] is True
    assert "password_hash" not in body and "password" not in body
    stored = db.get(User, body["id"])
    assert stored is not None and verify_password(stored.password_hash, NEW_USER["password"])


def test_create_user_with_taken_email_is_422_email_taken(client_as: ClientAs, db: Session) -> None:
    make_user(db, email="nuevo@example.com")

    response = client_as("admin").post(USERS, json=NEW_USER)

    assert response.status_code == 422
    assert response.json()["code"] == "email_taken"


@pytest.mark.parametrize(
    ("override", "code"),
    [({"password": "short"}, "password_too_short"), ({"email": "nope"}, "invalid_email")],
)
def test_create_user_with_invalid_data_is_422(
    client_as: ClientAs, override: dict[str, str], code: str
) -> None:
    response = client_as("admin").post(USERS, json=NEW_USER | override)

    assert response.status_code == 422
    assert response.json()["code"] == code


def test_create_user_with_unknown_role_is_422(client_as: ClientAs) -> None:
    response = client_as("admin").post(USERS, json=NEW_USER | {"role": "superuser"})

    assert response.status_code == 422


def test_get_user_returns_it(client_as: ClientAs, db: Session) -> None:
    target = make_user(db)

    response = client_as("admin").get(f"{USERS}/{target.id}")

    assert response.status_code == 200
    assert response.json()["email"] == target.email
    assert "password_hash" not in response.json()


def test_get_unknown_user_is_404(client_as: ClientAs) -> None:
    response = client_as("admin").get(f"{USERS}/999999")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_patch_user_updates_fields(client_as: ClientAs, db: Session) -> None:
    target = make_user(db)

    response = client_as("admin").patch(
        f"{USERS}/{target.id}", json={"full_name": "Renamed", "role": "accountant"}
    )

    assert response.status_code == 200
    assert response.json()["full_name"] == "Renamed"
    assert response.json()["role"] == "accountant"


def test_patch_deactivation_logs_the_user_out(client_as: ClientAs) -> None:
    admin = client_as("admin")
    seller = client_as("seller")
    seller_user: User = seller.user  # type: ignore[attr-defined]

    response = admin.patch(f"{USERS}/{seller_user.id}", json={"is_active": False})

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert seller.get("/api/v1/auth/me").status_code == 401


def test_patch_password_revokes_sessions_and_allows_new_login(
    client_as: ClientAs, client: TestClient, db: Session
) -> None:
    admin = client_as("admin")
    seller = client_as("seller")
    seller_user: User = seller.user  # type: ignore[attr-defined]

    response = admin.patch(f"{USERS}/{seller_user.id}", json={"password": "another-pass"})

    assert response.status_code == 200
    assert SessionRepository(db).count_for_user(seller_user.id) == 0
    login = client.post(
        "/api/v1/auth/login", json={"email": seller_user.email, "password": "another-pass"}
    )
    assert login.status_code == 200


def test_patch_short_password_is_422(client_as: ClientAs, db: Session) -> None:
    target = make_user(db)

    response = client_as("admin").patch(f"{USERS}/{target.id}", json={"password": "short"})

    assert response.status_code == 422
    assert response.json()["code"] == "password_too_short"


@pytest.mark.parametrize(
    ("body", "code"),
    [({"is_active": False}, "cannot_deactivate_self"), ({"role": "seller"}, "cannot_demote_self")],
)
def test_admin_patching_themselves_is_rejected(
    client_as: ClientAs, db: Session, body: dict[str, object], code: str
) -> None:
    admin = client_as("admin")
    me: User = admin.user  # type: ignore[attr-defined]
    db.commit()  # error responses roll back the request savepoint: keep the arranged rows

    response = admin.patch(f"{USERS}/{me.id}", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == code
    db.refresh(me)
    assert (me.is_active, me.role) == (True, UserRole.ADMIN)


def test_patch_unknown_user_is_404(client_as: ClientAs) -> None:
    assert client_as("admin").patch(f"{USERS}/999999", json={"full_name": "X"}).status_code == 404
