from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.enums import UserRole
from app.repositories.users import UserRepository
from scripts import create_admin
from tests.factories import make_user

ARGS = ["--email", "Boss@Example.com", "--name", "The Boss"]


@pytest.fixture
def scope(db: Session) -> create_admin.SessionScope:
    @contextmanager
    def session_scope() -> Iterator[Session]:
        yield db

    return session_scope


def test_create_admin_with_env_password_creates_active_admin(
    db: Session, scope: create_admin.SessionScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(create_admin.PASSWORD_ENV, "super-secret")

    exit_code = create_admin.main(ARGS, session_scope=scope)

    assert exit_code == 0
    user = UserRepository(db).get_by_email("boss@example.com")
    assert user is not None
    assert (user.role, user.is_active, user.full_name) == (UserRole.ADMIN, True, "The Boss")
    assert verify_password(user.password_hash, "super-secret")


def test_create_admin_prompts_for_password_twice(
    db: Session, scope: create_admin.SessionScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(create_admin.PASSWORD_ENV, raising=False)
    prompts: list[str] = []

    def fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        return "typed-secret"

    monkeypatch.setattr(create_admin.getpass, "getpass", fake_getpass)

    assert create_admin.main(ARGS, session_scope=scope) == 0
    assert len(prompts) == 2
    user = UserRepository(db).get_by_email("boss@example.com")
    assert user is not None and verify_password(user.password_hash, "typed-secret")


def test_create_admin_with_mismatched_prompts_fails(
    db: Session,
    scope: create_admin.SessionScope,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(create_admin.PASSWORD_ENV, raising=False)
    answers = iter(["first-secret", "second-secret"])
    monkeypatch.setattr(create_admin.getpass, "getpass", lambda _prompt: next(answers))

    assert create_admin.main(ARGS, session_scope=scope) == 1
    assert "do not match" in capsys.readouterr().err
    assert UserRepository(db).get_by_email("boss@example.com") is None


def test_create_admin_refuses_existing_email(
    db: Session,
    scope: create_admin.SessionScope,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    existing = make_user(db, email="boss@example.com", role=UserRole.SELLER)
    monkeypatch.setenv(create_admin.PASSWORD_ENV, "super-secret")

    assert create_admin.main(ARGS, session_scope=scope) == 1
    assert "already in use" in capsys.readouterr().err
    assert existing.role is UserRole.SELLER


def test_create_admin_rejects_short_password(
    db: Session, scope: create_admin.SessionScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(create_admin.PASSWORD_ENV, "short")

    assert create_admin.main(ARGS, session_scope=scope) == 1
    assert UserRepository(db).get_by_email("boss@example.com") is None
