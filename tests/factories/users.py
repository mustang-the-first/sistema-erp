import itertools
from functools import cache

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.users import User

DEFAULT_PASSWORD = "correct-horse-battery"
_counter = itertools.count(1)


@cache
def _default_hash() -> str:
    return hash_password(DEFAULT_PASSWORD)  # argon2 is slow: hash once per run


def make_user(
    db: Session,
    *,
    role: UserRole = UserRole.SELLER,
    email: str | None = None,
    full_name: str | None = None,
    password: str | None = None,
    is_active: bool = True,
) -> User:
    n = next(_counter)
    user = User(
        email=email or f"user{n}@example.com",
        full_name=full_name or f"Test User {n}",
        password_hash=hash_password(password) if password else _default_hash(),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.flush()
    return user
