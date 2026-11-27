import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation
from app.models.accounting import Account, JournalEntry, SystemAccount
from app.models.enums import JournalSourceType
from app.models.enums import SystemAccountKey as K
from app.models.users import User
from app.services.posting import EntryDraft, build_invoice_entry, credit, debit, post_entry
from tests.factories import make_user

D = Decimal
TODAY = dt.date(2026, 11, 27)


@pytest.fixture
def user(db: Session) -> User:
    return make_user(db)


def invoice_draft(source_id: int = 1) -> EntryDraft:
    return build_invoice_entry(
        invoice_id=source_id,
        entry_date=TODAY,
        description="Factura A 0001-00000001",
        net_total=D("1500.00"),
        vat_21=D("210.00"),
        vat_10_5=D("52.50"),
        total=D("1762.50"),
    )


def entry_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(JournalEntry)) or 0


def test_post_entry_persists_lines_on_mapped_accounts(db: Session, user: User) -> None:
    entry = post_entry(db, invoice_draft(), created_by=user.id)

    by_code = {line.account.code: (line.debit, line.credit) for line in entry.lines}
    assert by_code == {
        "1.1.3.01": (D("1762.50"), D("0")),
        "4.1.01": (D("0"), D("1500.00")),
        "2.1.3.01": (D("0"), D("210.00")),
        "2.1.3.02": (D("0"), D("52.50")),
    }
    assert entry.number == 1
    assert (entry.source_type, entry.source_id) == (JournalSourceType.INVOICE, 1)


def test_entries_are_numbered_consecutively(db: Session, user: User) -> None:
    numbers = [post_entry(db, invoice_draft(i), created_by=user.id).number for i in (1, 2, 3)]

    assert numbers == [1, 2, 3]


def test_unbalanced_entry_is_rejected_and_nothing_is_written(db: Session, user: User) -> None:
    unbalanced = EntryDraft(
        TODAY,
        "x",
        JournalSourceType.RECEIPT,
        1,
        (debit(K.CASH, D("100.00")), credit(K.RECEIVABLES, D("99.00"))),
    )

    with pytest.raises(BusinessRuleViolation) as exc:
        post_entry(db, unbalanced, created_by=user.id)

    assert exc.value.code == "unbalanced_entry"
    assert entry_count(db) == 0


def test_non_postable_account_is_rejected(db: Session, user: User) -> None:
    parent = db.scalars(select(Account).where(Account.code == "4.1")).one()  # rubro
    db.get_one(SystemAccount, K.SALES).account_id = parent.id
    db.flush()

    with pytest.raises(BusinessRuleViolation) as exc:
        post_entry(db, invoice_draft(), created_by=user.id)

    assert exc.value.code == "non_postable_account"
    assert entry_count(db) == 0


def test_inactive_account_is_rejected(db: Session, user: User) -> None:
    db.get_one(SystemAccount, K.SALES).account.is_active = False
    db.flush()

    with pytest.raises(BusinessRuleViolation) as exc:
        post_entry(db, invoice_draft(), created_by=user.id)

    assert exc.value.code == "non_postable_account"


def test_duplicate_source_is_rejected(db: Session, user: User) -> None:
    post_entry(db, invoice_draft(source_id=5), created_by=user.id)

    with pytest.raises(BusinessRuleViolation) as exc:
        post_entry(db, invoice_draft(source_id=5), created_by=user.id)

    assert exc.value.code == "duplicate_entry_source"
    assert entry_count(db) == 1


def test_same_source_id_with_other_type_is_allowed(db: Session, user: User) -> None:
    post_entry(db, invoice_draft(source_id=5), created_by=user.id)
    receipt = EntryDraft(
        TODAY,
        "Recibo",
        JournalSourceType.RECEIPT,
        5,
        (debit(K.CASH, D("10.00")), credit(K.RECEIVABLES, D("10.00"))),
    )

    post_entry(db, receipt, created_by=user.id)

    assert entry_count(db) == 2


def test_missing_system_account_is_reported(db: Session, user: User) -> None:
    db.delete(db.get_one(SystemAccount, K.VAT_10_5))
    db.flush()

    with pytest.raises(BusinessRuleViolation) as exc:
        post_entry(db, invoice_draft(), created_by=user.id)

    assert exc.value.code == "system_account_missing"
    assert "VAT_10_5" in exc.value.message
