"""Queries for users and their login sessions. No business rules here."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, delete, func, select
from sqlalchemy.orm import Session

from app.models.users import User, UserSession
from app.repositories.base import Repository


class UserRepository(Repository[User]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, User)

    def get_by_email(self, email: str) -> User | None:
        """Case-insensitive lookup (emails are stored lowercase)."""
        stmt = select(User).where(func.lower(User.email) == email.lower())
        return self.session.scalars(stmt).first()

    def page(self, *, limit: int, offset: int) -> tuple[Sequence[User], int]:
        items = self.list(order_by=(User.id,), limit=limit, offset=offset)
        total = self.session.scalar(select(func.count()).select_from(User)) or 0
        return items, total


class SessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, token_hash: str, user_id: int, expires_at: datetime) -> UserSession:
        row = UserSession(token_hash=token_hash, user_id=user_id, expires_at=expires_at)
        self.session.add(row)
        self.session.flush()
        return row

    def delete(self, token_hash: str) -> int:
        return self._delete(UserSession.token_hash == token_hash)

    def delete_for_user(self, user_id: int) -> int:
        return self._delete(UserSession.user_id == user_id)

    def delete_expired_for_user(self, user_id: int, now: datetime) -> int:
        return self._delete(UserSession.user_id == user_id, UserSession.expires_at <= now)

    def count_for_user(self, user_id: int) -> int:
        stmt = select(func.count()).select_from(UserSession).where(UserSession.user_id == user_id)
        return self.session.scalar(stmt) or 0

    def _delete(self, *where: ColumnElement[bool]) -> int:
        stmt = delete(UserSession).where(*where).execution_options(synchronize_session="fetch")
        result = cast(CursorResult[Any], self.session.execute(stmt))
        self.session.flush()
        return result.rowcount
