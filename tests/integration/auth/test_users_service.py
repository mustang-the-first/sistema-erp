from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, NotFound
from app.core.security import verify_password
from app.models.enums import UserRole
from app.models.users import User
from app.repositories.users import SessionRepository, UserRepository
from app.services import users as users_service
from app.services.users import UserChanges
from tests.factories import make_user

PASSWORD = "s3cret-pass"


def _create(db: Session, **overrides: object) -> User:
    fields: dict[str, object] = {
        "email": "new@example.com",
        "full_name": "New User",
        "role": UserRole.SELLER,
        "password": PASSWORD,
    }
    fields.update(overrides)
    return users_service.create_user(db, **fields)  # type: ignore[arg-type]


def _add_session(db: Session, user: User, token_hash: str) -> None:
    expires = datetime.now(UTC) + timedelta(hours=1)
    SessionRepository(db).create(token_hash=token_hash, user_id=user.id, expires_at=expires)


def _rule(exc_info: pytest.ExceptionInfo[BusinessRuleViolation]) -> str:
    return exc_info.value.code


# create ------------------------------------------------------------------------------------


def test_create_user_stores_lowercase_email_and_argon2_hash(db: Session) -> None:
    user = _create(db, email=" New.User@Example.COM ", full_name="  New   User ")

    assert user.id is not None
    assert user.email == "new.user@example.com"
    assert user.full_name == "New User"
    assert user.role is UserRole.SELLER
    assert user.is_active is True
    assert user.password_hash != PASSWORD
    assert verify_password(user.password_hash, PASSWORD)


def test_create_user_with_existing_email_in_other_case_raises_email_taken(db: Session) -> None:
    make_user(db, email="taken@example.com")

    with pytest.raises(BusinessRuleViolation) as exc_info:
        _create(db, email="TAKEN@example.com")

    assert _rule(exc_info) == "email_taken"


def test_create_user_losing_a_concurrent_insert_raises_email_taken(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user(db, email="race@example.com")
    monkeypatch.setattr(UserRepository, "get_by_email", lambda self, email: None)

    with pytest.raises(BusinessRuleViolation) as exc_info:
        _create(db, email="race@example.com")

    assert _rule(exc_info) == "email_taken"
    # The outer transaction is still usable after the failed nested insert.
    assert make_user(db).id is not None


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"email": "not-an-email"}, "invalid_email"),
        ({"password": "short"}, "password_too_short"),
        ({"full_name": "   "}, "full_name_required"),
    ],
)
def test_create_user_with_invalid_data_is_rejected(
    db: Session, overrides: dict[str, object], code: str
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc_info:
        _create(db, **overrides)

    assert _rule(exc_info) == code
    assert UserRepository(db).get_by_email("new@example.com") is None


# read --------------------------------------------------------------------------------------


def test_list_users_paginates_by_id(db: Session) -> None:
    created = [make_user(db) for _ in range(3)]
    _, total_before = UserRepository(db).page(limit=1, offset=0)

    page = users_service.list_users(db, limit=2, offset=total_before - 3)

    assert page.total == total_before
    assert [u.id for u in page.items] == [created[0].id, created[1].id]


def test_get_user_returns_it(db: Session) -> None:
    user = make_user(db)

    assert users_service.get_user(db, user.id) is user


def test_get_user_unknown_raises_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        users_service.get_user(db, 999_999)


# update ------------------------------------------------------------------------------------


def test_update_user_changes_only_given_fields(db: Session) -> None:
    admin = make_user(db, role=UserRole.ADMIN)
    user = make_user(db, full_name="Old Name")
    _add_session(db, user, "keep-me")

    updated = users_service.update_user(
        db, user.id, UserChanges(full_name=" New  Name ", role=UserRole.ACCOUNTANT), actor=admin
    )

    assert updated.full_name == "New Name"
    assert updated.role is UserRole.ACCOUNTANT
    assert updated.is_active is True
    assert SessionRepository(db).count_for_user(user.id) == 1


def test_update_user_unknown_raises_not_found(db: Session) -> None:
    admin = make_user(db, role=UserRole.ADMIN)

    with pytest.raises(NotFound):
        users_service.update_user(db, 999_999, UserChanges(full_name="X"), actor=admin)


def test_deactivating_user_deletes_their_sessions(db: Session) -> None:
    admin = make_user(db, role=UserRole.ADMIN)
    user = make_user(db)
    _add_session(db, user, "s1")
    _add_session(db, user, "s2")
    _add_session(db, admin, "admin-session")

    users_service.update_user(db, user.id, UserChanges(is_active=False), actor=admin)

    assert user.is_active is False
    assert SessionRepository(db).count_for_user(user.id) == 0
    assert SessionRepository(db).count_for_user(admin.id) == 1


def test_reactivating_user_keeps_sessions_untouched(db: Session) -> None:
    admin = make_user(db, role=UserRole.ADMIN)
    user = make_user(db, is_active=False)
    _add_session(db, user, "stale")

    users_service.update_user(db, user.id, UserChanges(is_active=True), actor=admin)

    assert user.is_active is True
    assert SessionRepository(db).count_for_user(user.id) == 1


def test_changing_password_rehashes_and_deletes_sessions(db: Session) -> None:
    admin = make_user(db, role=UserRole.ADMIN)
    user = make_user(db)
    _add_session(db, user, "s1")

    users_service.update_user(db, user.id, UserChanges(password="brand-new-pass"), actor=admin)

    assert verify_password(user.password_hash, "brand-new-pass")
    assert SessionRepository(db).count_for_user(user.id) == 0


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (UserChanges(password="short", full_name="Changed"), "password_too_short"),
        (UserChanges(full_name="  ", role=UserRole.ADMIN), "full_name_required"),
    ],
)
def test_update_user_with_invalid_data_changes_nothing(
    db: Session, changes: UserChanges, code: str
) -> None:
    admin = make_user(db, role=UserRole.ADMIN)
    user = make_user(db, full_name="Original")
    old_hash = user.password_hash

    with pytest.raises(BusinessRuleViolation) as exc_info:
        users_service.update_user(db, user.id, changes, actor=admin)

    assert _rule(exc_info) == code
    assert (user.full_name, user.role, user.password_hash) == (
        "Original",
        UserRole.SELLER,
        old_hash,
    )


def test_admin_cannot_deactivate_themselves(db: Session) -> None:
    admin = make_user(db, role=UserRole.ADMIN)

    with pytest.raises(BusinessRuleViolation) as exc_info:
        users_service.update_user(db, admin.id, UserChanges(is_active=False), actor=admin)

    assert _rule(exc_info) == "cannot_deactivate_self"
    assert admin.is_active is True


def test_admin_cannot_remove_own_admin_role(db: Session) -> None:
    admin = make_user(db, role=UserRole.ADMIN)

    with pytest.raises(BusinessRuleViolation) as exc_info:
        users_service.update_user(db, admin.id, UserChanges(role=UserRole.SELLER), actor=admin)

    assert _rule(exc_info) == "cannot_demote_self"
    assert admin.role is UserRole.ADMIN


def test_admin_can_update_own_name_and_keep_admin_role(db: Session) -> None:
    admin = make_user(db, role=UserRole.ADMIN)

    users_service.update_user(
        db,
        admin.id,
        UserChanges(full_name="Me", role=UserRole.ADMIN, is_active=True),
        actor=admin,
    )

    assert (admin.full_name, admin.role, admin.is_active) == ("Me", UserRole.ADMIN, True)


def test_admin_can_demote_and_deactivate_another_admin(db: Session) -> None:
    admin = make_user(db, role=UserRole.ADMIN)
    other = make_user(db, role=UserRole.ADMIN)

    users_service.update_user(
        db, other.id, UserChanges(role=UserRole.SELLER, is_active=False), actor=admin
    )

    assert (other.role, other.is_active) == (UserRole.SELLER, False)
