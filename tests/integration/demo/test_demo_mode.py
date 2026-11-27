"""ERP_DEMO_MODE: demo accounts cannot lock visitors out, and every page says it is a demo."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.demo import DEMO_USERS
from app.core.errors import BusinessRuleViolation
from app.models.enums import UserRole
from app.models.users import User
from app.services.users import UserChanges, update_user
from tests.conftest import ClientAs
from tests.factories import make_user

DEMO_ADMIN = DEMO_USERS[0][0]
BANNER = 'data-testid="demo-banner"'


@pytest.fixture
def demo_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "demo_mode", True)
    monkeypatch.setattr(get_settings(), "demo_password", "public-demo-pass")
    yield


@pytest.fixture
def demo_admin(db: Session) -> User:
    return make_user(db, email=DEMO_ADMIN, role=UserRole.ADMIN)


LOCKED_CHANGES = {
    "password": UserChanges(password="another-password"),
    "role": UserChanges(role=UserRole.ADMIN),
    "deactivate": UserChanges(is_active=False),
}


# Service ----------------------------------------------------------------------------------


@pytest.mark.usefixtures("demo_on")
@pytest.mark.parametrize("change", LOCKED_CHANGES, ids=str)
def test_demo_account_cannot_change_password_role_or_deactivate(
    db: Session, demo_admin: User, change: str
) -> None:
    target = make_user(db, role=UserRole.SELLER)

    with pytest.raises(BusinessRuleViolation) as exc:
        update_user(db, target.id, LOCKED_CHANGES[change], actor=demo_admin)

    assert exc.value.code == "demo_mode_locked"
    db.refresh(target)
    assert (target.role, target.is_active) == (UserRole.SELLER, True)


@pytest.mark.usefixtures("demo_on")
def test_demo_account_cannot_change_its_own_password(db: Session, demo_admin: User) -> None:
    with pytest.raises(BusinessRuleViolation, match="Demo accounts"):
        update_user(db, demo_admin.id, UserChanges(password="hijacked-pass"), actor=demo_admin)


@pytest.mark.usefixtures("demo_on")
def test_demo_account_can_still_rename_and_resend_same_role(db: Session, demo_admin: User) -> None:
    target = make_user(db, role=UserRole.SELLER)

    update_user(
        db, target.id, UserChanges(full_name="Nuevo Nombre", role=UserRole.SELLER), actor=demo_admin
    )

    assert target.full_name == "Nuevo Nombre"


@pytest.mark.usefixtures("demo_on")
def test_real_admin_is_not_locked_in_demo_mode(db: Session) -> None:
    owner = make_user(db, email="owner@example.com", role=UserRole.ADMIN)
    target = make_user(db, role=UserRole.SELLER)

    update_user(db, target.id, UserChanges(is_active=False), actor=owner)

    assert target.is_active is False


def test_demo_accounts_are_not_locked_outside_demo_mode(db: Session, demo_admin: User) -> None:
    target = make_user(db, role=UserRole.SELLER)

    update_user(db, target.id, UserChanges(role=UserRole.ACCOUNTANT), actor=demo_admin)

    assert target.role is UserRole.ACCOUNTANT


# API and web ------------------------------------------------------------------------------


@pytest.mark.usefixtures("demo_on")
@pytest.mark.parametrize(
    "body",
    [{"password": "another-password"}, {"role": "admin"}, {"is_active": False}],
    ids=["password", "role", "deactivate"],
)
def test_api_patch_user_is_locked_for_demo_accounts(
    client_as: ClientAs, db: Session, body: dict[str, object]
) -> None:
    admin = client_as("admin", email=DEMO_ADMIN)
    target = make_user(db, role=UserRole.SELLER)

    response = admin.patch(f"/api/v1/users/{target.id}", json=body)

    assert (response.status_code, response.json()["code"]) == (422, "demo_mode_locked")


@pytest.mark.usefixtures("demo_on")
def test_web_user_form_explains_the_demo_lock(client_as: ClientAs, db: Session) -> None:
    admin = client_as("admin", email=DEMO_ADMIN)
    target = make_user(db, role=UserRole.SELLER)

    response = admin.patch(
        f"/users/{target.id}",
        data={
            "full_name": target.full_name,
            "role": "seller",
            "is_active": "true",
            "password": "another-password",
        },
        headers={"HX-Request": "true"},
    )

    assert "En la demo no se pueden cambiar contraseñas" in response.text
    db.refresh(target)
    assert target.is_active


# Banner -----------------------------------------------------------------------------------


@pytest.mark.usefixtures("demo_on")
def test_banner_and_demo_accounts_on_login_page(client: TestClient) -> None:
    html = client.get("/login").text

    assert BANNER in html
    assert "se reinician todos los días" in html
    assert all(email in html for email, _, _ in DEMO_USERS)
    assert "public-demo-pass" in html


@pytest.mark.usefixtures("demo_on")
def test_banner_on_every_logged_in_page(client_as: ClientAs) -> None:
    seller = client_as("seller")

    for path in ("/orders", "/invoices", "/reports/sales"):
        assert BANNER in seller.get(path).text, path


def test_no_banner_and_no_credentials_outside_demo_mode(client: TestClient) -> None:
    html = client.get("/login").text

    assert BANNER not in html
    assert 'data-testid="demo-accounts"' not in html
