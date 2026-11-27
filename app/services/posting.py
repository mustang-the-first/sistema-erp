"""Posting engine: the ONLY way journal entries are created.

Builders (``build_*_entry``) are pure functions that turn a business operation into an
``EntryDraft`` using ``SystemAccountKey`` roles; ``post_entry`` validates and persists it.
The database backs this up with CHECKs, UNIQUE(source_type, source_id) and a deferred trigger
that rejects unbalanced entries at COMMIT.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation
from app.core.money import ZERO, has_at_most_two_decimals
from app.models.accounting import Account, JournalEntry, JournalLine, SystemAccount
from app.models.enums import (
    AdjustmentReason,
    DocumentType,
    JournalSourceType,
    PaymentMethod,
    SystemAccountKey,
)
from app.models.sequences import GLOBAL_POINT_OF_SALE
from app.services.sequences import next_number

K = SystemAccountKey


@dataclass(frozen=True, slots=True)
class EntryLineDraft:
    account: SystemAccountKey
    debit: Decimal = ZERO
    credit: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class EntryDraft:
    date: dt.date
    description: str
    source_type: JournalSourceType
    source_id: int
    lines: tuple[EntryLineDraft, ...]

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit for line in self.lines), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit for line in self.lines), ZERO)


def debit(account: SystemAccountKey, amount: Decimal) -> EntryLineDraft:
    return EntryLineDraft(account=account, debit=amount)


def credit(account: SystemAccountKey, amount: Decimal) -> EntryLineDraft:
    return EntryLineDraft(account=account, credit=amount)


# Validation -------------------------------------------------------------------------------


def validate_draft(draft: EntryDraft) -> None:
    """Pure checks: >= 2 lines, each line one-sided and positive, cents only, balanced."""
    if len(draft.lines) < 2:
        raise BusinessRuleViolation("An entry needs at least two lines", code="entry_too_short")
    for line in draft.lines:
        one_sided = (line.debit > 0 and line.credit == 0) or (line.debit == 0 and line.credit > 0)
        if not one_sided:
            raise BusinessRuleViolation(
                f"Line for {line.account} must have exactly one positive side",
                code="invalid_entry_line",
            )
        if not (has_at_most_two_decimals(line.debit) and has_at_most_two_decimals(line.credit)):
            raise BusinessRuleViolation(
                f"Line for {line.account} has more than two decimals", code="invalid_entry_line"
            )
    if draft.total_debit != draft.total_credit:
        raise BusinessRuleViolation(
            f"Unbalanced entry: debit {draft.total_debit} != credit {draft.total_credit}",
            code="unbalanced_entry",
        )


def _resolve_accounts(
    session: Session, keys: set[SystemAccountKey]
) -> dict[SystemAccountKey, Account]:
    rows = session.execute(
        select(SystemAccount.key, Account)
        .join(Account, Account.id == SystemAccount.account_id)
        .where(SystemAccount.key.in_(keys))
    ).all()
    resolved = {key: account for key, account in rows}
    missing = keys - resolved.keys()
    if missing:
        raise BusinessRuleViolation(
            f"System accounts not configured: {', '.join(sorted(missing))}",
            code="system_account_missing",
        )
    for key, account in resolved.items():
        if not account.is_postable or not account.is_active:
            raise BusinessRuleViolation(
                f"Account {account.code} ({key}) is not postable", code="non_postable_account"
            )
    return resolved


def _source_already_posted(session: Session, draft: EntryDraft) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    JournalEntry.source_type == draft.source_type,
                    JournalEntry.source_id == draft.source_id,
                )
            )
        )
    )


def _duplicate_source(draft: EntryDraft) -> BusinessRuleViolation:
    return BusinessRuleViolation(
        f"{draft.source_type} {draft.source_id} already has a journal entry",
        code="duplicate_entry_source",
    )


def post_entry(session: Session, draft: EntryDraft, *, created_by: int) -> JournalEntry:
    validate_draft(draft)
    accounts = _resolve_accounts(session, {line.account for line in draft.lines})
    if _source_already_posted(session, draft):
        raise _duplicate_source(draft)

    entry = JournalEntry(
        number=next_number(session, DocumentType.JOURNAL_ENTRY, point_of_sale=GLOBAL_POINT_OF_SALE),
        date=draft.date,
        description=draft.description,
        source_type=draft.source_type,
        source_id=draft.source_id,
        created_by=created_by,
        lines=[
            JournalLine(account_id=accounts[line.account].id, debit=line.debit, credit=line.credit)
            for line in draft.lines
        ],
    )
    try:
        with session.begin_nested():  # a concurrent duplicate must not poison the transaction
            session.add(entry)
            session.flush()
    except IntegrityError as exc:
        if "uq_journal_entries_source_type_source_id" in str(exc.orig):
            raise _duplicate_source(draft) from exc
        raise
    return entry


# Builders (pure) --------------------------------------------------------------------------


def build_invoice_entry(
    *,
    invoice_id: int,
    entry_date: dt.date,
    description: str,
    net_total: Decimal,
    vat_21: Decimal,
    vat_10_5: Decimal,
    total: Decimal,
) -> EntryDraft:
    """Factura A o B: D Deudores por ventas / C Ventas, IVA DF 21%, IVA DF 10,5%."""
    if net_total + vat_21 + vat_10_5 != total:
        raise BusinessRuleViolation("Invoice total does not match net + VAT", code="bad_totals")
    lines = [debit(K.RECEIVABLES, total), credit(K.SALES, net_total)]
    if vat_21 > 0:
        lines.append(credit(K.VAT_21, vat_21))
    if vat_10_5 > 0:
        lines.append(credit(K.VAT_10_5, vat_10_5))
    return EntryDraft(entry_date, description, JournalSourceType.INVOICE, invoice_id, tuple(lines))


def build_delivery_entry(
    *, delivery_note_id: int, entry_date: dt.date, description: str, cost: Decimal
) -> EntryDraft | None:
    """Remito: D CMV / C Mercaderías at standard cost. None when the cost is zero."""
    if cost < 0:
        raise BusinessRuleViolation("Delivery cost cannot be negative", code="bad_amount")
    if cost == 0:
        return None
    return EntryDraft(
        entry_date,
        description,
        JournalSourceType.DELIVERY_NOTE,
        delivery_note_id,
        (debit(K.COGS, cost), credit(K.INVENTORY, cost)),
    )


def build_receipt_entry(
    *,
    receipt_id: int,
    entry_date: dt.date,
    description: str,
    method: PaymentMethod,
    amount: Decimal,
) -> EntryDraft:
    """Cobranza: D Caja (efectivo) o Banco c/c (transferencia) / C Deudores por ventas."""
    if amount <= 0:
        raise BusinessRuleViolation("Receipt amount must be positive", code="bad_amount")
    funds = K.CASH if method is PaymentMethod.CASH else K.BANK
    return EntryDraft(
        entry_date,
        description,
        JournalSourceType.RECEIPT,
        receipt_id,
        (debit(funds, amount), credit(K.RECEIVABLES, amount)),
    )


def build_adjustment_entry(
    *,
    movement_id: int,
    entry_date: dt.date,
    description: str,
    reason: AdjustmentReason,
    quantity: Decimal,
    value: Decimal,
) -> EntryDraft | None:
    """Ajuste de stock. ``quantity`` is signed; ``value`` = |quantity| x unit_cost (>= 0).

    - initial_load (+):        D Mercaderías        / C Capital
    - count_difference (+):    D Mercaderías        / C Sobrantes de inventario
    - count_difference / damage (-): D Faltantes y roturas / C Mercaderías

    Returns None when the value is zero (product with zero cost: nothing to post).
    """
    if quantity == 0:
        raise BusinessRuleViolation("Adjustment quantity cannot be zero", code="bad_amount")
    if value < 0:
        raise BusinessRuleViolation("Adjustment value cannot be negative", code="bad_amount")
    if reason is AdjustmentReason.INITIAL_LOAD and quantity < 0:
        raise BusinessRuleViolation("Initial load must be positive", code="bad_adjustment")
    if reason is AdjustmentReason.DAMAGE and quantity > 0:
        raise BusinessRuleViolation("Damage must be negative", code="bad_adjustment")
    if value == 0:
        return None

    lines: Sequence[EntryLineDraft]
    if quantity > 0:
        counterpart = K.CAPITAL if reason is AdjustmentReason.INITIAL_LOAD else K.INVENTORY_GAIN
        lines = (debit(K.INVENTORY, value), credit(counterpart, value))
    else:
        lines = (debit(K.INVENTORY_LOSS, value), credit(K.INVENTORY, value))
    return EntryDraft(
        entry_date, description, JournalSourceType.STOCK_ADJUSTMENT, movement_id, tuple(lines)
    )
