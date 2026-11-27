from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import Response

from app.core.errors import BusinessRuleViolation
from app.core.security import SESSION_COOKIE, SESSION_TTL
from app.models.enums import UserRole
from app.routers.api import auth as api_auth
from app.schemas.users import UserOut, UserUpdate
from app.services.users import (
    MIN_PASSWORD_LENGTH,
    UserChanges,
    normalize_email,
    normalize_full_name,
    validate_password,
)


def test_normalize_email_with_mixed_case_and_spaces_returns_lowercase() -> None:
    assert normalize_email("  Ana.Perez@Example.COM ") == "ana.perez@example.com"


@pytest.mark.parametrize("email", ["", "ana", "ana@", "@example.com", "ana@example", "a b@x.com"])
def test_normalize_email_with_malformed_address_raises_invalid_email(email: str) -> None:
    with pytest.raises(BusinessRuleViolation) as exc_info:
        normalize_email(email)

    assert exc_info.value.code == "invalid_email"


def test_normalize_full_name_collapses_whitespace() -> None:
    assert normalize_full_name("  Ana   Pérez ") == "Ana Pérez"


def test_normalize_full_name_when_blank_raises_full_name_required() -> None:
    with pytest.raises(BusinessRuleViolation) as exc_info:
        normalize_full_name("   ")

    assert exc_info.value.code == "full_name_required"


def test_validate_password_at_minimum_length_passes() -> None:
    validate_password("x" * MIN_PASSWORD_LENGTH)


def test_validate_password_shorter_than_minimum_raises_password_too_short() -> None:
    with pytest.raises(BusinessRuleViolation) as exc_info:
        validate_password("x" * (MIN_PASSWORD_LENGTH - 1))

    assert exc_info.value.code == "password_too_short"
    assert exc_info.value.status_code == 422


def test_user_update_schema_keeps_only_sent_fields_as_changes() -> None:
    body = UserUpdate.model_validate({"role": "accountant"})

    changes = UserChanges(**body.model_dump(exclude_unset=True))

    assert changes == UserChanges(role=UserRole.ACCOUNTANT)


def test_user_out_never_exposes_password_hash() -> None:
    user = SimpleNamespace(
        id=1,
        email="a@example.com",
        full_name="A",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=datetime.now(UTC),
        password_hash="secret-hash",
    )

    dumped = UserOut.model_validate(user).model_dump()

    assert "password_hash" not in dumped


@pytest.mark.parametrize(("env", "secure"), [("development", False), ("production", True)])
def test_session_cookie_is_httponly_lax_and_secure_only_in_production(
    monkeypatch: pytest.MonkeyPatch, env: str, secure: bool
) -> None:
    monkeypatch.setattr(api_auth, "get_settings", lambda: SimpleNamespace(env=env))
    response = Response()

    api_auth.set_session_cookie(response, "tok")

    header = response.headers["set-cookie"]
    assert header.startswith(f"{SESSION_COOKIE}=tok;")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert f"Max-Age={int(SESSION_TTL.total_seconds())}" in header
    assert ("Secure" in header) is secure


def test_clear_session_cookie_expires_it() -> None:
    response = Response()

    api_auth.clear_session_cookie(response)

    header = response.headers["set-cookie"]
    assert header.startswith(f'{SESSION_COOKIE}="";')
    assert "Max-Age=0" in header
