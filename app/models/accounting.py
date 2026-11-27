import datetime as dt

from sqlalchemy import CheckConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt, Money, fk, str_enum
from app.models.enums import AccountType, JournalSourceType, SystemAccountKey


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[AccountType] = mapped_column(str_enum(AccountType, "account_type"))
    parent_id: Mapped[int | None] = mapped_column(fk("accounts.id"), index=True)
    is_postable: Mapped[bool] = mapped_column(default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")

    parent: Mapped["Account | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["Account"]] = relationship(back_populates="parent")


class SystemAccount(Base):
    """Maps the accounts the posting engine needs (by role) to concrete accounts."""

    __tablename__ = "system_accounts"

    key: Mapped[SystemAccountKey] = mapped_column(
        str_enum(SystemAccountKey, "system_account_key"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(fk("accounts.id"), index=True)

    account: Mapped[Account] = relationship()


class JournalEntry(Base):
    """Automatic journal entry. Balance (sum debit = sum credit, >= 2 lines) is also enforced by
    a deferred constraint trigger created in the initial migration."""

    __tablename__ = "journal_entries"
    __table_args__ = (UniqueConstraint("source_type", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(unique=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    description: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[JournalSourceType] = mapped_column(
        str_enum(JournalSourceType, "journal_source_type")
    )
    source_id: Mapped[int]
    created_by: Mapped[int] = mapped_column(fk("users.id"), index=True)
    created_at: Mapped[CreatedAt]

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="JournalLine.id"
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
            name="exactly_one_side_positive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(
        fk("journal_entries.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[int] = mapped_column(fk("accounts.id"), index=True)
    debit: Mapped[Money] = mapped_column(default=0, server_default="0")
    credit: Mapped[Money] = mapped_column(default=0, server_default="0")

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()
