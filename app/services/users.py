"""User administration: create, list, read and update users (admin only, enforced by routers).

Rules:
- Emails are unique case-insensitively and stored lowercase (``email_taken`` otherwise).
- Passwords have at least ``MIN_PASSWORD_LENGTH`` characters.
- An admin cannot deactivate themselves nor remove their own admin role.
- Deactivating a user or changing their password deletes all their login sessions.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation
from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.users import User
from app.repositories.users import SessionRepository, UserRepository

MIN_PASSWORD_LENGTH = 8
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class UserChanges:
    """Partial update. ``None`` means "leave unchanged" (no field is nullable)."""

    full_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = None


@dataclass(frozen=True, slots=True)
class UserPage:
    items: Sequence[User]
    total: int


def normalize_email(email: str) -> str:
    """Trimmed, lowercase and minimally well-formed; raises ``invalid_email`` otherwise."""
    normalized = email.strip().lower()
    if not _EMAIL_RE.fullmatch(normalized):
        raise BusinessRuleViolation("Invalid email address", code="invalid_email")
    return normalized


def normalize_full_name(full_name: str) -> str:
    normalized = " ".join(full_name.split())
    if not normalized:
        raise BusinessRuleViolation("Full name is required", code="full_name_required")
    return normalized


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise BusinessRuleViolation(
            f"Password must have at least {MIN_PASSWORD_LENGTH} characters",
            code="password_too_short",
        )


def _email_taken(email: str) -> BusinessRuleViolation:
    return BusinessRuleViolation(f"Email {email} is already in use", code="email_taken")


def create_user(
    session: Session, *, email: str, full_name: str, role: UserRole, password: str
) -> User:
    users = UserRepository(session)
    normalized_email = normalize_email(email)
    name = normalize_full_name(full_name)
    validate_password(password)
    if users.get_by_email(normalized_email) is not None:
        raise _email_taken(normalized_email)
    user = User(
        email=normalized_email,
        full_name=name,
        role=role,
        password_hash=hash_password(password),
        is_active=True,
    )
    try:
        with session.begin_nested():  # a concurrent insert of the same email
            users.add(user)
    except IntegrityError as exc:
        raise _email_taken(normalized_email) from exc
    return user


def list_users(session: Session, *, limit: int, offset: int) -> UserPage:
    items, total = UserRepository(session).page(limit=limit, offset=offset)
    return UserPage(items=items, total=total)


def get_user(session: Session, user_id: int) -> User:
    return UserRepository(session).get_or_raise(user_id)


def update_user(session: Session, user_id: int, changes: UserChanges, *, actor: User) -> User:
    user = UserRepository(session).get_or_raise(user_id)
    is_self = user.id == actor.id
    if is_self and changes.is_active is False:
        raise BusinessRuleViolation("You cannot deactivate yourself", code="cannot_deactivate_self")
    if (
        is_self
        and user.role is UserRole.ADMIN
        and changes.role is not None
        and changes.role is not UserRole.ADMIN
    ):
        raise BusinessRuleViolation(
            "You cannot remove your own admin role", code="cannot_demote_self"
        )

    # Validate everything before touching the row.
    name = normalize_full_name(changes.full_name) if changes.full_name is not None else None
    if changes.password is not None:
        validate_password(changes.password)

    revoke_sessions = False
    if name is not None:
        user.full_name = name
    if changes.role is not None:
        user.role = changes.role
    if changes.password is not None:
        user.password_hash = hash_password(changes.password)
        revoke_sessions = True
    if changes.is_active is not None:
        revoke_sessions = revoke_sessions or (user.is_active and not changes.is_active)
        user.is_active = changes.is_active
    session.flush()
    if revoke_sessions:
        SessionRepository(session).delete_for_user(user.id)
    return user
