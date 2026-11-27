"""Authentication, authorization and CSRF dependencies shared by every router.

- ``CurrentUser``: the logged-in, active user (401 otherwise; web requests redirect to /login).
- ``require_roles(UserRole.SELLER, ...)``: 403 unless the user has one of the roles. Admin
  always passes.
- ``require_csrf``: for unsafe methods on a logged-in session, the ``X-CSRF-Token`` header must
  match the session. HTMX sends it on every request (see ``templates/base.html``), so web forms
  must submit through ``hx-post``/``hx-patch``, not plain HTML form posts.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request

from app.core.db import DbSession
from app.core.errors import Forbidden, NotAuthenticated
from app.core.security import (
    CSRF_HEADER,
    SESSION_COOKIE,
    csrf_token_for,
    csrf_token_is_valid,
    hash_token,
)
from app.models.enums import UserRole
from app.models.users import User, UserSession

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_optional_user(request: Request, db: DbSession) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session_row = db.get(UserSession, hash_token(token))
    if session_row is None or session_row.expires_at <= datetime.now(UTC):
        return None
    user = session_row.user
    if not user.is_active:
        return None
    request.state.user = user
    request.state.csrf_token = csrf_token_for(token)
    return user


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def get_current_user(user: OptionalUser) -> User:
    if user is None:
        raise NotAuthenticated("Login required")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[[User], User]:
    allowed = frozenset(roles) | {UserRole.ADMIN}

    def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise Forbidden("Your role cannot perform this action")
        return user

    return dependency


def require_csrf(request: Request, user: OptionalUser) -> None:
    """CSRF protects authenticated sessions only. A request without a LIVE session (no cookie,
    or an expired/revoked one) is anonymous: it can do nothing a forged request could abuse,
    and routes that need a user reject it with 401 anyway. This keeps the plain login form
    working for a browser that still carries a stale cookie."""
    if request.method in SAFE_METHODS or user is None:
        return
    token = request.cookies[SESSION_COOKIE]  # a live session implies the cookie is present
    if not csrf_token_is_valid(token, request.headers.get(CSRF_HEADER)):
        raise Forbidden("Invalid or missing CSRF token", code="csrf_failed")
