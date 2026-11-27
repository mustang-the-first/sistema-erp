"""Queries for the chart of accounts, the journal (libro diario) and the ledger (libro mayor).

Owner: agent C · Accounting. No business rules here: only data access.
"""

import datetime as dt
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.accounting import Account, JournalEntry, JournalLine, SystemAccount
from app.models.enums import JournalSourceType
from app.repositories.base import Repository


class AccountRepository(Repository[Account]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Account)

    def list_all(self) -> list[Account]:
        return self.list(order_by=[Account.code])

    def code_exists(self, code: str) -> bool:
        return bool(self.session.scalar(select(exists().where(Account.code == code))))

    def subtree_ids(self, account_id: int) -> list[int]:
        """The account itself plus every descendant (recursive on parent_id)."""
        tree = select(Account.id).where(Account.id == account_id).cte("tree", recursive=True)
        tree = tree.union_all(select(Account.id).where(Account.parent_id == tree.c.id))
        return list(self.session.scalars(select(tree.c.id)))

    def has_active_children(self, account_id: int) -> bool:
        stmt = select(exists().where(Account.parent_id == account_id, Account.is_active))
        return bool(self.session.scalar(stmt))

    def is_system_account(self, account_id: int) -> bool:
        stmt = select(exists().where(SystemAccount.account_id == account_id))
        return bool(self.session.scalar(stmt))


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """One journal line on one of the ledger's accounts, with its entry header."""

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


class JournalRepository(Repository[JournalEntry]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, JournalEntry)

    @staticmethod
    def _entry_filters(
        date_from: dt.date | None,
        date_to: dt.date | None,
        source_type: JournalSourceType | None = None,
    ) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = []
        if date_from is not None:
            filters.append(JournalEntry.date >= date_from)
        if date_to is not None:
            filters.append(JournalEntry.date <= date_to)
        if source_type is not None:
            filters.append(JournalEntry.source_type == source_type)
        return filters

    def page(
        self,
        *,
        date_from: dt.date | None,
        date_to: dt.date | None,
        source_type: JournalSourceType | None,
        limit: int,
        offset: int,
    ) -> tuple[list[JournalEntry], int]:
        filters = self._entry_filters(date_from, date_to, source_type)
        total = self.session.scalar(select(func.count(JournalEntry.id)).where(*filters)) or 0
        stmt = (
            select(JournalEntry)
            .where(*filters)
            .options(selectinload(JournalEntry.lines).joinedload(JournalLine.account))
            .order_by(JournalEntry.date, JournalEntry.number)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt)), total

    def get_with_lines(self, entry_id: int) -> JournalEntry | None:
        stmt = (
            select(JournalEntry)
            .where(JournalEntry.id == entry_id)
            .options(selectinload(JournalEntry.lines).joinedload(JournalLine.account))
        )
        return self.session.scalars(stmt).one_or_none()

    def balance(self, account_ids: Collection[int], *, before: dt.date | None = None) -> Decimal:
        """Sum of debit - credit on these accounts; only entries dated before ``before`` if set."""
        stmt = (
            select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(JournalLine.account_id.in_(account_ids))
        )
        if before is not None:
            stmt = stmt.where(JournalEntry.date < before)
        return Decimal(self.session.scalar(stmt) or 0)

    def ledger_rows(
        self,
        account_ids: Collection[int],
        *,
        date_from: dt.date | None,
        date_to: dt.date | None,
    ) -> list[LedgerRow]:
        stmt = (
            select(
                JournalEntry.id,
                JournalEntry.number,
                JournalEntry.date,
                JournalEntry.description,
                JournalEntry.source_type,
                JournalEntry.source_id,
                Account.id,
                Account.code,
                Account.name,
                JournalLine.debit,
                JournalLine.credit,
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .join(Account, Account.id == JournalLine.account_id)
            .where(
                JournalLine.account_id.in_(account_ids),
                *self._entry_filters(date_from, date_to),
            )
            .order_by(JournalEntry.date, JournalEntry.number, JournalLine.id)
        )
        return [LedgerRow(*row) for row in self.session.execute(stmt)]
