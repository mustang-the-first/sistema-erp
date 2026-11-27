"""Login and logout with server-side sessions.

The browser gets a random token; only ``hash_token(token)`` is stored in ``sessions``. Every
login failure (unknown email, wrong password, inactive user) raises the same
``NotAuthenticated`` so the API never reveals which emails exist.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache

from sqlalchemy.orm import Session

from app.core.errors import NotAuthenticated
from app.core.security import (
    SESSION_TTL,
    hash_password,
    hash_token,
    new_session_token,
    password_needs_rehash,
    verify_password,
)
from app.models.users import User
from app.repositories.users import SessionRepository, UserRepository

INVALID_CREDENTIALS_MESSAGE = "Invalid email or password"


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    token: str  # raw token for the cookie; never stored
    expires_at: datetime


@cache
def _dummy_hash() -> str:
    """Verified against when the email is unknown, so both paths cost one argon2 check."""
    return hash_password(new_session_token())


def invalid_credentials() -> NotAuthenticated:
    return NotAuthenticated(INVALID_CREDENTIALS_MESSAGE, code="invalid_credentials")


def login(
    session: Session, *, email: str, password: str, now: datetime | None = None
) -> LoginResult:
    now = now or datetime.now(UTC)
    user = UserRepository(session).get_by_email(email.strip())
    if user is None:
        verify_password(_dummy_hash(), password)
        raise invalid_credentials()
    if not verify_password(user.password_hash, password) or not user.is_active:
        raise invalid_credentials()

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    sessions = SessionRepository(session)
    sessions.delete_expired_for_user(user.id, now)
    token = new_session_token()
    expires_at = now + SESSION_TTL
    sessions.create(token_hash=hash_token(token), user_id=user.id, expires_at=expires_at)
    return LoginResult(user=user, token=token, expires_at=expires_at)


def logout(session: Session, token: str | None) -> None:
    """Deletes the session behind ``token``, if any. Idempotent."""
    if token:
        SessionRepository(session).delete(hash_token(token))
