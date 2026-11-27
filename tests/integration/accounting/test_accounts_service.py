"""Chart of accounts service: create, rename, (de)activate and every rejection."""

import datetime as dt

import pytest
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, NotFound
from app.models.accounting import Account, SystemAccount
from app.models.enums import AccountType, SystemAccountKey
from app.services import accounting as service
from tests.integration.accounting.conftest import AccountByCode, PostReceipt

DAY = dt.date(2026, 11, 10)


def create(db: Session, parent: Account, code: str, **overrides: object) -> Account:
    fields: dict[str, object] = {
        "code": code,
        "name": f"Account {code}",
        "type": parent.type,
        "parent_id": parent.id,
        "is_postable": True,
    } | overrides
    return service.create_account(db, **fields)  # type: ignore[arg-type]


def rule_code(exc: pytest.ExceptionInfo[BusinessRuleViolation]) -> str:
    return exc.value.code


# Listing ----------------------------------------------------------------------------------


def test_list_accounts_returns_seeded_chart_in_numeric_code_order(db: Session) -> None:
    codes = [a.code for a in service.list_accounts(db)]

    assert codes[:5] == ["1", "1.1", "1.1.1", "1.1.1.01", "1.1.1.02"]
    assert codes[-1] == "5.2.01"
    assert len(codes) == 27


def test_get_account_unknown_id_raises_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        service.get_account(db, 999_999)


# Create -----------------------------------------------------------------------------------


def test_create_account_under_rubro_persists_postable_account(
    db: Session, account_by_code: AccountByCode
) -> None:
    parent = account_by_code("1.1.1")

    account = create(db, parent, "1.1.1.03", name="  Banco Galicia  ")

    db.expire_all()
    stored = db.get_one(Account, account.id)
    assert (stored.code, stored.name, stored.type) == (
        "1.1.1.03",
        "Banco Galicia",
        AccountType.ASSET,
    )
    assert (stored.parent_id, stored.is_postable, stored.is_active) == (parent.id, True, True)


def test_create_rubro_then_child_under_it_succeeds(
    db: Session, account_by_code: AccountByCode
) -> None:
    rubro = create(db, account_by_code("1.1"), "1.1.5", name="Otros créditos", is_postable=False)

    child = create(db, rubro, "1.1.5.01", name="Anticipos")

    assert child.parent_id == rubro.id
    assert rubro.is_postable is False


def test_create_account_with_existing_code_is_rejected(
    db: Session, account_by_code: AccountByCode
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        create(db, account_by_code("1.1.1"), "1.1.1.01")

    assert rule_code(exc) == "code_taken"


def test_create_account_with_unknown_parent_is_rejected(db: Session) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        service.create_account(
            db, code="9.01", name="X", type=AccountType.ASSET, parent_id=999_999, is_postable=True
        )

    assert rule_code(exc) == "invalid_parent"


def test_create_account_under_postable_parent_is_rejected(
    db: Session, account_by_code: AccountByCode
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        create(db, account_by_code("1.1.1.01"), "1.1.1.01.01")

    assert rule_code(exc) == "invalid_parent"


def test_create_account_under_inactive_parent_is_rejected(
    db: Session, account_by_code: AccountByCode
) -> None:
    rubro = create(db, account_by_code("1.1"), "1.1.6", is_postable=False)
    service.update_account(db, rubro.id, is_active=False)

    with pytest.raises(BusinessRuleViolation) as exc:
        create(db, rubro, "1.1.6.01")

    assert rule_code(exc) == "invalid_parent"


def test_create_account_with_type_different_from_parent_is_rejected(
    db: Session, account_by_code: AccountByCode
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        create(db, account_by_code("1.1.1"), "1.1.1.03", type=AccountType.LIABILITY)

    assert rule_code(exc) == "type_mismatch"


def test_create_account_with_code_outside_parent_is_rejected(
    db: Session, account_by_code: AccountByCode
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        create(db, account_by_code("1.1.1"), "1.1.2.01")

    assert rule_code(exc) == "invalid_code"


def test_create_account_with_blank_name_is_rejected(
    db: Session, account_by_code: AccountByCode
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        create(db, account_by_code("1.1.1"), "1.1.1.03", name="   ")

    assert rule_code(exc) == "invalid_name"


# Update -----------------------------------------------------------------------------------


def test_update_account_renames_without_touching_type_or_code(
    db: Session, account_by_code: AccountByCode
) -> None:
    caja = account_by_code("1.1.1.01")

    updated = service.update_account(db, caja.id, name=" Caja chica ")

    assert (updated.name, updated.code, updated.type) == (
        "Caja chica",
        "1.1.1.01",
        AccountType.ASSET,
    )
    assert updated.is_active is True


def test_update_account_with_blank_name_is_rejected(
    db: Session, account_by_code: AccountByCode
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        service.update_account(db, account_by_code("1.1.1.01").id, name=" ")

    assert rule_code(exc) == "invalid_name"


def test_update_unknown_account_raises_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        service.update_account(db, 999_999, name="X")


def test_deactivate_unused_account_succeeds_and_reactivate_restores_it(
    db: Session, account_by_code: AccountByCode
) -> None:
    account = create(db, account_by_code("1.1.1"), "1.1.1.03")

    assert service.update_account(db, account.id, is_active=False).is_active is False
    assert service.update_account(db, account.id, is_active=True).is_active is True


def test_deactivate_system_account_is_rejected(db: Session, account_by_code: AccountByCode) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        service.update_account(db, account_by_code("1.1.1.01").id, is_active=False)

    assert rule_code(exc) == "system_account"


def test_deactivate_account_with_balance_is_rejected(
    db: Session, account_by_code: AccountByCode, post_receipt: PostReceipt
) -> None:
    # Post a cash receipt to a new account by temporarily mapping CASH to it.
    new_cash = create(db, account_by_code("1.1.1"), "1.1.1.03", name="Caja sucursal")
    mapping = db.get_one(SystemAccount, SystemAccountKey.CASH)
    original = mapping.account_id
    mapping.account_id = new_cash.id
    db.flush()
    post_receipt(DAY, "150.00")
    mapping.account_id = original
    db.flush()

    with pytest.raises(BusinessRuleViolation) as exc:
        service.update_account(db, new_cash.id, is_active=False)

    assert rule_code(exc) == "account_has_balance"
    assert "150.00" in exc.value.message


def test_deactivate_rubro_with_active_children_is_rejected(
    db: Session, account_by_code: AccountByCode
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        service.update_account(db, account_by_code("1.1.1").id, is_active=False)

    assert rule_code(exc) == "account_has_active_children"


def test_deactivate_rubro_whose_children_are_inactive_succeeds(
    db: Session, account_by_code: AccountByCode
) -> None:
    rubro = create(db, account_by_code("1.1"), "1.1.5", is_postable=False)
    child = create(db, rubro, "1.1.5.01")
    service.update_account(db, child.id, is_active=False)

    assert service.update_account(db, rubro.id, is_active=False).is_active is False


def test_reactivate_child_of_inactive_parent_is_rejected(
    db: Session, account_by_code: AccountByCode
) -> None:
    rubro = create(db, account_by_code("1.1"), "1.1.5", is_postable=False)
    child = create(db, rubro, "1.1.5.01")
    service.update_account(db, child.id, is_active=False)
    service.update_account(db, rubro.id, is_active=False)

    with pytest.raises(BusinessRuleViolation) as exc:
        service.update_account(db, child.id, is_active=True)

    assert rule_code(exc) == "invalid_parent"


def test_update_account_with_same_active_flag_is_a_no_op(
    db: Session, account_by_code: AccountByCode
) -> None:
    caja = account_by_code("1.1.1.01")  # a system account: deactivating would be rejected

    assert service.update_account(db, caja.id, is_active=True).is_active is True
