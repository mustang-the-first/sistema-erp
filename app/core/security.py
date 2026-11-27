"""Password hashing, session tokens and CSRF tokens."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

SESSION_COOKIE = "erp_session"
SESSION_TTL = timedelta(hours=12)
CSRF_HEADER = "X-CSRF-Token"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_session_token() -> str:
    """Raw token sent to the browser. Only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token_for(session_token: str) -> str:
    """CSRF token bound to the session: HMAC(secret_key, session token)."""
    key = get_settings().secret_key.encode()
    return hmac.new(key, session_token.encode(), hashlib.sha256).hexdigest()


def csrf_token_is_valid(session_token: str, candidate: str | None) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(csrf_token_for(session_token), candidate)
