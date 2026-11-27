from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotAuthenticated
from app.core.security import SESSION_TTL, hash_token, verify_password
from app.models.users import UserSession
from app.repositories.users import SessionRepository
from app.services import auth as auth_service
from tests.factories import make_user
from tests.factories.users import DEFAULT_PASSWORD


def _sessions(db: Session, user_id: int) -> list[UserSession]:
    return list(db.scalars(select(UserSession).where(UserSession.user_id == user_id)))


def test_login_with_valid_credentials_stores_only_token_hash(db: Session) -> None:
    user = make_user(db, email="ana@example.com")
    now = datetime.now(UTC)

    result = auth_service.login(db, email="ana@example.com", password=DEFAULT_PASSWORD, now=now)

    assert result.user.id == user.id
    assert result.expires_at == now + SESSION_TTL
    [row] = _sessions(db, user.id)
    assert row.token_hash == hash_token(result.token)
    assert row.token_hash != result.token
    assert row.expires_at == now + SESSION_TTL


def test_login_with_differently_cased_email_succeeds(db: Session) -> None:
    user = make_user(db, email="ana@example.com")

    result = auth_service.login(db, email="  ANA@Example.com ", password=DEFAULT_PASSWORD)

    assert result.user.id == user.id


@pytest.mark.parametrize(
    ("email", "password", "is_active"),
    [
        ("ana@example.com", "wrong-password", True),  # wrong password
        ("nobody@example.com", DEFAULT_PASSWORD, True),  # unknown email
        ("ana@example.com", DEFAULT_PASSWORD, False),  # inactive user
    ],
)
def test_login_failure_raises_same_generic_error(
    db: Session, email: str, password: str, is_active: bool
) -> None:
    user = make_user(db, email="ana@example.com", is_active=is_active)

    with pytest.raises(NotAuthenticated) as exc_info:
        auth_service.login(db, email=email, password=password)

    assert exc_info.value.message == auth_service.INVALID_CREDENTIALS_MESSAGE
    assert exc_info.value.code == "invalid_credentials"
    assert _sessions(db, user.id) == []


def test_login_with_outdated_hash_rehashes_password(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(db)
    old_hash = user.password_hash
    monkeypatch.setattr(auth_service, "password_needs_rehash", lambda _hash: True)

    auth_service.login(db, email=user.email, password=DEFAULT_PASSWORD)

    assert user.password_hash != old_hash
    assert verify_password(user.password_hash, DEFAULT_PASSWORD)


def test_login_with_current_hash_keeps_it(db: Session) -> None:
    user = make_user(db)
    old_hash = user.password_hash

    auth_service.login(db, email=user.email, password=DEFAULT_PASSWORD)

    assert user.password_hash == old_hash


def test_login_purges_expired_sessions_of_that_user(db: Session) -> None:
    user = make_user(db)
    now = datetime.now(UTC)
    sessions = SessionRepository(db)
    sessions.create(token_hash="expired", user_id=user.id, expires_at=now - timedelta(minutes=1))
    sessions.create(token_hash="alive", user_id=user.id, expires_at=now + timedelta(hours=1))

    result = auth_service.login(db, email=user.email, password=DEFAULT_PASSWORD, now=now)

    hashes = {row.token_hash for row in _sessions(db, user.id)}
    assert hashes == {"alive", hash_token(result.token)}


def test_logout_deletes_only_that_session(db: Session) -> None:
    user = make_user(db)
    first = auth_service.login(db, email=user.email, password=DEFAULT_PASSWORD)
    second = auth_service.login(db, email=user.email, password=DEFAULT_PASSWORD)

    auth_service.logout(db, first.token)

    assert [row.token_hash for row in _sessions(db, user.id)] == [hash_token(second.token)]


@pytest.mark.parametrize("token", [None, "", "unknown-token"])
def test_logout_without_a_live_session_is_a_noop(db: Session, token: str | None) -> None:
    auth_service.logout(db, token)
