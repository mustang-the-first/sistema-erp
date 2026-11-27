"""Pure posting rules: draft validation and the entry builders of docs/plan.md section 4."""

import datetime as dt
from decimal import Decimal

import pytest

from app.core.errors import BusinessRuleViolation
from app.models.enums import AdjustmentReason, JournalSourceType, PaymentMethod
from app.models.enums import SystemAccountKey as K
from app.services.posting import (
    EntryDraft,
    EntryLineDraft,
    build_adjustment_entry,
    build_delivery_entry,
    build_invoice_entry,
    build_receipt_entry,
    credit,
    debit,
    validate_draft,
)

D = Decimal
TODAY = dt.date(2026, 11, 27)


def draft(*lines: EntryLineDraft) -> EntryDraft:
    return EntryDraft(TODAY, "test", JournalSourceType.INVOICE, 1, lines)


def sides(entry: EntryDraft) -> set[tuple[str, K, Decimal]]:
    return {
        ("D", line.account, line.debit) if line.debit else ("C", line.account, line.credit)
        for line in entry.lines
    }


# validate_draft ---------------------------------------------------------------------------


def test_balanced_draft_is_valid() -> None:
    validate_draft(draft(debit(K.CASH, D("10.00")), credit(K.RECEIVABLES, D("10.00"))))


def test_unbalanced_draft_is_rejected() -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        validate_draft(draft(debit(K.CASH, D("10.00")), credit(K.RECEIVABLES, D("9.99"))))
    assert exc.value.code == "unbalanced_entry"


def test_single_line_draft_is_rejected() -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        validate_draft(draft(debit(K.CASH, D("10.00"))))
    assert exc.value.code == "entry_too_short"


@pytest.mark.parametrize(
    "bad_line",
    [
        EntryLineDraft(K.CASH, debit=D("5"), credit=D("5")),  # both sides
        EntryLineDraft(K.CASH),  # zero line
        EntryLineDraft(K.CASH, debit=D("-5")),  # negative
        EntryLineDraft(K.CASH, debit=D("5.001")),  # sub-cent
    ],
)
def test_invalid_lines_are_rejected(bad_line: EntryLineDraft) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        validate_draft(draft(bad_line, credit(K.RECEIVABLES, D("5"))))
    assert exc.value.code == "invalid_entry_line"


# Builders ---------------------------------------------------------------------------------


def test_invoice_entry_matches_plan_example() -> None:
    entry = build_invoice_entry(
        invoice_id=7,
        entry_date=TODAY,
        description="Factura A 0001-00000007",
        net_total=D("1500.00"),
        vat_21=D("210.00"),
        vat_10_5=D("52.50"),
        total=D("1762.50"),
    )

    assert sides(entry) == {
        ("D", K.RECEIVABLES, D("1762.50")),
        ("C", K.SALES, D("1500.00")),
        ("C", K.VAT_21, D("210.00")),
        ("C", K.VAT_10_5, D("52.50")),
    }
    assert (entry.source_type, entry.source_id) == (JournalSourceType.INVOICE, 7)
    validate_draft(entry)


def test_invoice_entry_omits_zero_vat_lines() -> None:
    entry = build_invoice_entry(
        invoice_id=1,
        entry_date=TODAY,
        description="x",
        net_total=D("100.00"),
        vat_21=D("21.00"),
        vat_10_5=D("0.00"),
        total=D("121.00"),
    )

    assert {line.account for line in entry.lines} == {K.RECEIVABLES, K.SALES, K.VAT_21}


def test_invoice_entry_rejects_inconsistent_totals() -> None:
    with pytest.raises(BusinessRuleViolation):
        build_invoice_entry(
            invoice_id=1,
            entry_date=TODAY,
            description="x",
            net_total=D("100.00"),
            vat_21=D("21.00"),
            vat_10_5=D("0"),
            total=D("120.00"),
        )


def test_delivery_entry_moves_cost_from_inventory_to_cogs() -> None:
    entry = build_delivery_entry(
        delivery_note_id=3, entry_date=TODAY, description="Remito", cost=D("600.00")
    )

    assert entry is not None
    assert sides(entry) == {("D", K.COGS, D("600.00")), ("C", K.INVENTORY, D("600.00"))}
    assert entry.source_type is JournalSourceType.DELIVERY_NOTE


def test_delivery_entry_with_zero_cost_posts_nothing() -> None:
    assert (
        build_delivery_entry(delivery_note_id=3, entry_date=TODAY, description="x", cost=D(0))
        is None
    )


def test_delivery_entry_rejects_negative_cost() -> None:
    with pytest.raises(BusinessRuleViolation):
        build_delivery_entry(delivery_note_id=3, entry_date=TODAY, description="x", cost=D("-1"))


@pytest.mark.parametrize(
    ("method", "funds"), [(PaymentMethod.CASH, K.CASH), (PaymentMethod.BANK_TRANSFER, K.BANK)]
)
def test_receipt_entry_debits_cash_or_bank(method: PaymentMethod, funds: K) -> None:
    entry = build_receipt_entry(
        receipt_id=1, entry_date=TODAY, description="Recibo", method=method, amount=D("500.00")
    )

    assert sides(entry) == {("D", funds, D("500.00")), ("C", K.RECEIVABLES, D("500.00"))}


def test_receipt_entry_rejects_non_positive_amount() -> None:
    with pytest.raises(BusinessRuleViolation):
        build_receipt_entry(
            receipt_id=1, entry_date=TODAY, description="x", method=PaymentMethod.CASH, amount=D(0)
        )


@pytest.mark.parametrize(
    ("reason", "quantity", "expected"),
    [
        (AdjustmentReason.INITIAL_LOAD, D("10"), {("D", K.INVENTORY), ("C", K.CAPITAL)}),
        (AdjustmentReason.COUNT_DIFFERENCE, D("2"), {("D", K.INVENTORY), ("C", K.INVENTORY_GAIN)}),
        (AdjustmentReason.COUNT_DIFFERENCE, D("-2"), {("D", K.INVENTORY_LOSS), ("C", K.INVENTORY)}),
        (AdjustmentReason.DAMAGE, D("-1"), {("D", K.INVENTORY_LOSS), ("C", K.INVENTORY)}),
    ],
)
def test_adjustment_entries(
    reason: AdjustmentReason, quantity: Decimal, expected: set[tuple[str, K]]
) -> None:
    entry = build_adjustment_entry(
        movement_id=9,
        entry_date=TODAY,
        description="Ajuste",
        reason=reason,
        quantity=quantity,
        value=D("120.00"),
    )

    assert entry is not None
    assert {(side, account) for side, account, _ in sides(entry)} == expected
    assert entry.total_debit == entry.total_credit == D("120.00")
    assert entry.source_type is JournalSourceType.STOCK_ADJUSTMENT


@pytest.mark.parametrize(
    ("reason", "quantity"),
    [
        (AdjustmentReason.INITIAL_LOAD, D("-1")),
        (AdjustmentReason.DAMAGE, D("1")),
        (AdjustmentReason.COUNT_DIFFERENCE, D("0")),
    ],
)
def test_adjustment_entry_rejects_inconsistent_sign(
    reason: AdjustmentReason, quantity: Decimal
) -> None:
    with pytest.raises(BusinessRuleViolation):
        build_adjustment_entry(
            movement_id=9,
            entry_date=TODAY,
            description="x",
            reason=reason,
            quantity=quantity,
            value=D("1"),
        )


def test_adjustment_entry_rejects_negative_value() -> None:
    with pytest.raises(BusinessRuleViolation):
        build_adjustment_entry(
            movement_id=9,
            entry_date=TODAY,
            description="x",
            reason=AdjustmentReason.DAMAGE,
            quantity=D("-1"),
            value=D("-1"),
        )


def test_adjustment_with_zero_value_posts_nothing() -> None:
    entry = build_adjustment_entry(
        movement_id=9,
        entry_date=TODAY,
        description="x",
        reason=AdjustmentReason.DAMAGE,
        quantity=D("-1"),
        value=D("0"),
    )
    assert entry is None
