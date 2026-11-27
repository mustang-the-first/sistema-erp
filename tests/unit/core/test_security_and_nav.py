from app.core.security import (
    csrf_token_for,
    csrf_token_is_valid,
    hash_password,
    hash_token,
    new_session_token,
    password_needs_rehash,
    verify_password,
)
from app.core.templating import NAV, nav_for
from app.models.enums import UserRole
from app.models.users import User


def test_password_hash_verifies_only_the_right_password() -> None:
    hashed = hash_password("s3cret-password")

    assert verify_password(hashed, "s3cret-password")
    assert not verify_password(hashed, "wrong")
    assert not password_needs_rehash(hashed)


def test_verify_password_with_garbage_hash_is_false() -> None:
    assert not verify_password("not-a-hash", "whatever")


def test_session_tokens_are_random_and_stored_hashed() -> None:
    token = new_session_token()

    assert token != new_session_token()
    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 64
    assert token not in hash_token(token)


def test_csrf_token_is_bound_to_the_session() -> None:
    token, other = new_session_token(), new_session_token()

    assert csrf_token_is_valid(token, csrf_token_for(token))
    assert not csrf_token_is_valid(token, csrf_token_for(other))
    assert not csrf_token_is_valid(token, None)
    assert not csrf_token_is_valid(token, "")


def _user(role: UserRole) -> User:
    return User(email="x@example.com", full_name="X", password_hash="x", role=role)


def test_admin_sees_every_nav_item() -> None:
    assert nav_for(_user(UserRole.ADMIN)) == list(NAV)


def test_seller_and_accountant_see_only_their_sections() -> None:
    seller = {item.label for item in nav_for(_user(UserRole.SELLER))}
    accountant = {item.label for item in nav_for(_user(UserRole.ACCOUNTANT))}

    assert {"Pedidos", "Clientes", "Facturas"} <= seller
    assert not {"Usuarios", "Libro diario", "Stock"} & seller
    assert {"Libro diario", "Libro mayor", "Plan de cuentas", "Facturas"} <= accountant
    assert not {"Pedidos", "Usuarios", "Clientes"} & accountant


def test_anonymous_has_no_nav() -> None:
    assert nav_for(None) == []
