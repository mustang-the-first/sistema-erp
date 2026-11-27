"""Chart of accounts, journal (libro diario) and ledger (libro mayor).

Owner: agent C · Accounting. This module never creates journal entries: they are posted only
by the operational modules through ``app.services.posting.post_entry``.

Balance convention: debit - credit. Positive = saldo deudor, negative = saldo acreedor.

Ledger of a non-postable account (rubro): it aggregates the lines of all its descendants (the
whole subtree), ordered by date, entry number and line; each movement carries the code and name
of the postable account it was posted to.
"""

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, NotFound
from app.core.money import ZERO, round_money
from app.models.accounting import Account, JournalEntry
from app.models.enums import AccountType, JournalSourceType
from app.repositories.accounting import AccountRepository, JournalRepository, LedgerRow

MAX_PAGE_SIZE = 200
SEGMENT = r"\d{1,4}"

Nature = Literal["deudor", "acreedor"]


# Pure helpers -----------------------------------------------------------------------------


def code_sort_key(code: str) -> tuple[int, ...]:
    """Numeric ordering of account codes: 1.1.2 < 1.1.10 (string order would invert them)."""
    return tuple(int(part) if part.isdigit() else 0 for part in code.split("."))


def validate_child_code(parent_code: str, code: str) -> None:
    """A child code is the parent code plus exactly one numeric segment (1.1.1 -> 1.1.1.03)."""
    if not re.fullmatch(rf"{re.escape(parent_code)}\.{SEGMENT}", code):
        raise BusinessRuleViolation(
            f"Code {code!r} must be the parent code followed by one numeric segment "
            f"(e.g. {parent_code}.01)",
            code="invalid_code",
        )


def validate_date_range(date_from: dt.date | None, date_to: dt.date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise BusinessRuleViolation(
            f"'from' ({date_from}) is after 'to' ({date_to})", code="invalid_date_range"
        )


def balance_nature(balance: Decimal) -> Nature | None:
    """Nature of a debit - credit balance; None when it is zero (saldo nulo)."""
    if balance > 0:
        return "deudor"
    if balance < 0:
        return "acreedor"
    return None


@dataclass(slots=True)
class AccountNode:
    account: Account
    children: list["AccountNode"] = field(default_factory=list)


def build_account_tree(accounts: Iterable[Account]) -> list[AccountNode]:
    """Nest accounts by parent_id; siblings ordered by code. Orphans (parent not in the
    iterable) become roots, so a partial list still renders."""
    ordered = sorted(accounts, key=lambda a: code_sort_key(a.code))
    nodes = {a.id: AccountNode(a) for a in ordered}
    roots: list[AccountNode] = []
    for account in ordered:
        parent = nodes.get(account.parent_id) if account.parent_id is not None else None
        (parent.children if parent else roots).append(nodes[account.id])
    return roots


@dataclass(frozen=True, slots=True)
class LedgerMovement:
    entry_id: int
    entry_number: int
    date: dt.date
    description: str
    source_type: JournalSourceType
    source_id: int
    account_id: int
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


def with_running_balance(opening: Decimal, rows: Sequence[LedgerRow]) -> list[LedgerMovement]:
    balance = opening
    movements: list[LedgerMovement] = []
    for row in rows:
        balance = round_money(balance + row.debit - row.credit)
        movements.append(
            LedgerMovement(
                entry_id=row.entry_id,
                entry_number=row.entry_number,
                date=row.date,
                description=row.description,
                source_type=row.source_type,
                source_id=row.source_id,
                account_id=row.account_id,
                account_code=row.account_code,
                account_name=row.account_name,
                debit=round_money(row.debit),
                credit=round_money(row.credit),
                balance=balance,
            )
        )
    return movements


# Chart of accounts ------------------------------------------------------------------------


def list_accounts(session: Session) -> list[Account]:
    """All accounts (active and inactive), in numeric code order."""
    return sorted(AccountRepository(session).list_all(), key=lambda a: code_sort_key(a.code))


def get_account(session: Session, account_id: int) -> Account:
    return AccountRepository(session).get_or_raise(account_id)


def create_account(
    session: Session,
    *,
    code: str,
    name: str,
    type: AccountType,
    parent_id: int,
    is_postable: bool,
) -> Account:
    accounts = AccountRepository(session)
    code, name = code.strip(), name.strip()
    if not name:
        raise BusinessRuleViolation("Account name is required", code="invalid_name")
    if accounts.code_exists(code):
        raise BusinessRuleViolation(f"Account code {code} already exists", code="code_taken")
    parent = accounts.get(parent_id)
    if parent is None:
        raise BusinessRuleViolation(f"Parent account {parent_id} not found", code="invalid_parent")
    if not parent.is_active:
        raise BusinessRuleViolation(
            f"Parent account {parent.code} is inactive", code="invalid_parent"
        )
    if parent.is_postable:
        raise BusinessRuleViolation(
            f"Parent account {parent.code} is postable; children go under a rubro",
            code="invalid_parent",
        )
    if type is not parent.type:
        raise BusinessRuleViolation(
            f"Account type {type} does not match parent type {parent.type}", code="type_mismatch"
        )
    validate_child_code(parent.code, code)
    return accounts.add(
        Account(code=code, name=name, type=type, parent_id=parent.id, is_postable=is_postable)
    )


def update_account(
    session: Session,
    account_id: int,
    *,
    name: str | None = None,
    is_active: bool | None = None,
) -> Account:
    """Rename and/or (de)activate. The type and the code are never changed."""
    accounts = AccountRepository(session)
    account = accounts.get_or_raise(account_id)
    if name is not None:
        name = name.strip()
        if not name:
            raise BusinessRuleViolation("Account name is required", code="invalid_name")
        account.name = name
    if is_active is False and account.is_active:
        _check_can_deactivate(session, account)
        account.is_active = False
    elif is_active is True and not account.is_active:
        parent = account.parent
        if parent is not None and not parent.is_active:
            raise BusinessRuleViolation(
                f"Activate the parent account {parent.code} first", code="invalid_parent"
            )
        account.is_active = True
    session.flush()
    return account


def _check_can_deactivate(session: Session, account: Account) -> None:
    accounts = AccountRepository(session)
    if accounts.is_system_account(account.id):
        raise BusinessRuleViolation(
            f"Account {account.code} is used by automatic entries (system account)",
            code="system_account",
        )
    if accounts.has_active_children(account.id):
        raise BusinessRuleViolation(
            f"Account {account.code} has active sub-accounts", code="account_has_active_children"
        )
    balance = JournalRepository(session).balance(accounts.subtree_ids(account.id))
    if balance != 0:
        raise BusinessRuleViolation(
            f"Account {account.code} has a non-zero balance ({round_money(balance)})",
            code="account_has_balance",
        )


# Journal (libro diario) -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JournalPage:
    items: list[JournalEntry]
    total: int


def entry_totals(entry: JournalEntry) -> tuple[Decimal, Decimal]:
    debit = sum((line.debit for line in entry.lines), ZERO)
    credit = sum((line.credit for line in entry.lines), ZERO)
    return round_money(debit), round_money(credit)


def list_journal(
    session: Session,
    *,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    source_type: JournalSourceType | None = None,
    limit: int = 50,
    offset: int = 0,
) -> JournalPage:
    """Entries ordered by date, then number; each with its lines (and their accounts) loaded."""
    validate_date_range(date_from, date_to)
    if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
        raise BusinessRuleViolation(
            f"limit must be 1..{MAX_PAGE_SIZE} and offset >= 0", code="invalid_pagination"
        )
    items, total = JournalRepository(session).page(
        date_from=date_from, date_to=date_to, source_type=source_type, limit=limit, offset=offset
    )
    return JournalPage(items=items, total=total)


def get_journal_entry(session: Session, entry_id: int) -> JournalEntry:
    entry = JournalRepository(session).get_with_lines(entry_id)
    if entry is None:
        raise NotFound(f"JournalEntry {entry_id} not found")
    return entry


# Ledger (libro mayor) ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ledger:
    account: Account
    date_from: dt.date | None
    date_to: dt.date | None
    includes_descendants: bool
    opening_balance: Decimal
    movements: list[LedgerMovement]
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal

    @property
    def nature(self) -> Nature | None:
        return balance_nature(self.closing_balance)


def get_ledger(
    session: Session,
    account_id: int,
    *,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
) -> Ledger:
    """Opening balance = debit - credit of everything before ``date_from`` (zero without it);
    movements within [from, to] with their running balance; closing = last running balance."""
    validate_date_range(date_from, date_to)
    accounts = AccountRepository(session)
    account = accounts.get_or_raise(account_id)
    account_ids = [account.id] if account.is_postable else accounts.subtree_ids(account.id)

    journal = JournalRepository(session)
    opening = round_money(journal.balance(account_ids, before=date_from)) if date_from else ZERO
    rows = journal.ledger_rows(account_ids, date_from=date_from, date_to=date_to)
    movements = with_running_balance(opening, rows)
    return Ledger(
        account=account,
        date_from=date_from,
        date_to=date_to,
        includes_descendants=not account.is_postable,
        opening_balance=opening,
        movements=movements,
        total_debit=sum((m.debit for m in movements), ZERO),
        total_credit=sum((m.credit for m in movements), ZERO),
        closing_balance=movements[-1].balance if movements else opening,
    )
