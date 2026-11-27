"""Pydantic schemas of the accounting API. Owner: agent C · Accounting.

Amounts serialize as decimal strings ("1762.50"); dates as ISO YYYY-MM-DD.
"""

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.accounting import JournalEntry
from app.models.enums import AccountType, JournalSourceType
from app.services.accounting import AccountNode, Ledger, LedgerMovement, entry_totals

CODE_PATTERN = r"^\d{1,4}(\.\d{1,4})*$"


# Accounts ---------------------------------------------------------------------------------


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=20, pattern=CODE_PATTERN, examples=["1.1.1.03"])
    name: str = Field(min_length=1, max_length=200)
    type: AccountType
    parent_id: int
    is_postable: bool = True


class AccountUpdate(BaseModel):
    """Only the name and the active flag can change; any other field (e.g. ``type``) is a 422."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    type: AccountType
    parent_id: int | None
    is_postable: bool
    is_active: bool


class AccountTreeNode(AccountRead):
    children: list["AccountTreeNode"] = []

    @classmethod
    def from_node(cls, node: AccountNode) -> "AccountTreeNode":
        base = AccountRead.model_validate(node.account).model_dump()
        return cls(**base, children=[cls.from_node(child) for child in node.children])


class AccountTree(BaseModel):
    """Root accounts with nested ``children``. ``total`` = number of accounts in the tree."""

    items: list[AccountTreeNode]
    total: int


class AccountFlatList(BaseModel):
    """Every account, in code order (``?flat=true``)."""

    items: list[AccountRead]
    total: int


# Journal ----------------------------------------------------------------------------------


class JournalLineRead(BaseModel):
    account_id: int
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal


class JournalEntryRead(BaseModel):
    id: int
    number: int
    date: dt.date
    description: str
    source_type: JournalSourceType
    source_id: int
    created_by: int
    lines: list[JournalLineRead]
    total_debit: Decimal
    total_credit: Decimal

    @classmethod
    def from_entry(cls, entry: JournalEntry) -> "JournalEntryRead":
        total_debit, total_credit = entry_totals(entry)
        return cls(
            id=entry.id,
            number=entry.number,
            date=entry.date,
            description=entry.description,
            source_type=entry.source_type,
            source_id=entry.source_id,
            created_by=entry.created_by,
            lines=[
                JournalLineRead(
                    account_id=line.account_id,
                    account_code=line.account.code,
                    account_name=line.account.name,
                    debit=line.debit,
                    credit=line.credit,
                )
                for line in entry.lines
            ],
            total_debit=total_debit,
            total_credit=total_credit,
        )


class JournalEntryList(BaseModel):
    items: list[JournalEntryRead]
    total: int


# Ledger -----------------------------------------------------------------------------------


class LedgerMovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class LedgerRead(BaseModel):
    account: AccountRead
    date_from: dt.date | None
    date_to: dt.date | None
    includes_descendants: bool
    opening_balance: Decimal
    movements: list[LedgerMovementRead]
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal
    nature: Literal["deudor", "acreedor"] | None

    @classmethod
    def from_ledger(cls, ledger: Ledger) -> "LedgerRead":
        return cls(
            account=AccountRead.model_validate(ledger.account),
            date_from=ledger.date_from,
            date_to=ledger.date_to,
            includes_descendants=ledger.includes_descendants,
            opening_balance=ledger.opening_balance,
            movements=[_movement(m) for m in ledger.movements],
            total_debit=ledger.total_debit,
            total_credit=ledger.total_credit,
            closing_balance=ledger.closing_balance,
            nature=ledger.nature,
        )


def _movement(movement: LedgerMovement) -> LedgerMovementRead:
    return LedgerMovementRead.model_validate(movement)
