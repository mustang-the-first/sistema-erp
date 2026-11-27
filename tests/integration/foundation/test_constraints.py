"""The database enforces the critical invariants on its own, even if a service has a bug."""

from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import today
from app.models.accounting import JournalEntry, JournalLine, SystemAccount
from app.models.catalog import Customer
from app.models.collections import ReceiptAllocation
from app.models.enums import (
    DocumentType,
    InvoiceLetter,
    JournalSourceType,
    SystemAccountKey,
    TaxCondition,
)
from app.models.sequences import DocumentSequence
from app.models.users import User
from tests.factories import make_customer, make_invoice, make_product, make_receipt, make_user

D = Decimal


def violates(db: Session, constraint: str, action: Callable[[], None]) -> None:
    with pytest.raises(IntegrityError) as exc, db.begin_nested():
        action()
        db.flush()
    assert constraint in str(exc.value.orig)


def account_id(db: Session, key: SystemAccountKey) -> int:
    return db.get_one(SystemAccount, key).account_id


def new_entry(db: Session, user: User, source_id: int, lines: list[JournalLine]) -> JournalEntry:
    entry = JournalEntry(
        number=source_id,
        date=today(),
        description="direct insert",
        source_type=JournalSourceType.INVOICE,
        source_id=source_id,
        created_by=user.id,
        lines=lines,
    )
    db.add(entry)
    return entry


def test_journal_line_must_have_exactly_one_side(db: Session) -> None:
    user, cash = make_user(db), account_id(db, SystemAccountKey.CASH)
    violates(
        db,
        "ck_journal_lines_exactly_one_side_positive",
        lambda: new_entry(db, user, 1, [JournalLine(account_id=cash, debit=D(5), credit=D(5))]),
    )


def test_one_entry_per_source(db: Session) -> None:
    user, cash = make_user(db), account_id(db, SystemAccountKey.CASH)
    new_entry(db, user, 1, [JournalLine(account_id=cash, debit=D(1))])
    db.flush()

    def duplicate() -> None:
        entry = new_entry(db, user, 2, [JournalLine(account_id=cash, debit=D(1))])
        entry.source_id = 1

    violates(db, "uq_journal_entries_source_type_source_id", duplicate)


def test_deferred_trigger_rejects_unbalanced_entry(db: Session) -> None:
    user = make_user(db)
    cash, receivables = (
        account_id(db, k) for k in (SystemAccountKey.CASH, SystemAccountKey.RECEIVABLES)
    )

    with pytest.raises(IntegrityError) as exc, db.begin_nested():
        new_entry(
            db,
            user,
            1,
            [
                JournalLine(account_id=cash, debit=D(10)),
                JournalLine(account_id=receivables, credit=D(9)),
            ],
        )
        db.flush()
        db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # what COMMIT would do
    assert "is unbalanced" in str(exc.value.orig)


def test_deferred_trigger_accepts_balanced_entry(db: Session) -> None:
    user = make_user(db)
    cash, receivables = (
        account_id(db, k) for k in (SystemAccountKey.CASH, SystemAccountKey.RECEIVABLES)
    )
    new_entry(
        db,
        user,
        1,
        [
            JournalLine(account_id=cash, debit=D(10)),
            JournalLine(account_id=receivables, credit=D(10)),
        ],
    )
    db.flush()

    db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def test_stock_cannot_be_negative(db: Session) -> None:
    product = make_product(db)

    def negative() -> None:
        product.stock_qty = D("-1")

    violates(db, "ck_products_stock_qty_non_negative", negative)


def test_allocation_amount_must_be_positive(db: Session) -> None:
    customer = make_customer(db)
    invoice = make_invoice(db, customer=customer)
    receipt = make_receipt(db, customer=customer, allocations=[(invoice, D("1.00"))])

    violates(
        db,
        "ck_receipt_allocations_amount_positive",
        lambda: db.add(
            ReceiptAllocation(receipt_id=receipt.id, invoice_id=invoice.id, amount=D(0))
        ),
    )


def test_numbering_is_unique_per_type_letter_and_point_of_sale(db: Session) -> None:
    db.add(DocumentSequence(doc_type=DocumentType.INVOICE, letter=InvoiceLetter.A, point_of_sale=1))
    db.flush()

    violates(
        db,
        "uq_document_sequences_doc_type_letter_point_of_sale",
        lambda: db.add(
            DocumentSequence(doc_type=DocumentType.INVOICE, letter=InvoiceLetter.A, point_of_sale=1)
        ),
    )


def test_numbering_unique_also_when_letter_is_null(db: Session) -> None:
    db.add(DocumentSequence(doc_type=DocumentType.ORDER, point_of_sale=0))
    db.flush()

    violates(
        db,
        "uq_document_sequences_doc_type_letter_point_of_sale",
        lambda: db.add(DocumentSequence(doc_type=DocumentType.ORDER, point_of_sale=0)),
    )


def test_invoice_numbers_are_unique(db: Session) -> None:
    invoice = make_invoice(db)
    other = make_invoice(db)

    def clash() -> None:
        other.number = invoice.number

    violates(db, "uq_invoices_letter_point_of_sale_number", clash)


def test_invoice_total_must_match_net_plus_vat(db: Session) -> None:
    invoice = make_invoice(db)

    def tamper() -> None:
        invoice.total = invoice.total + D("0.01")

    violates(db, "ck_invoices_total_consistency", tamper)


def test_customer_needs_cuit_unless_final_consumer(db: Session) -> None:
    violates(
        db,
        "ck_customers_cuit_required_unless_final_consumer",
        lambda: db.add(Customer(name="X", tax_condition=TaxCondition.MONOTRIBUTO)),
    )


def test_every_foreign_key_is_indexed(db: Session) -> None:
    unindexed = db.execute(
        text(
            """
            SELECT c.conrelid::regclass::text AS table_name, a.attname AS column_name
              FROM pg_constraint c
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
             WHERE c.contype = 'f'
               AND NOT EXISTS (
                   SELECT 1 FROM pg_index i
                    WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1]
               )
            """
        )
    ).all()
    assert unindexed == []


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("invoices", "customer_id"),
        ("invoices", "date"),
        ("invoices", "due_date"),
        ("journal_lines", "account_id"),
        ("journal_entries", "date"),
        ("stock_movements", "product_id"),
        ("stock_movements", "date"),
        ("orders", "status"),
    ],
)
def test_report_filters_are_indexed(db: Session, table: str, column: str) -> None:
    found = db.scalar(
        text(
            """
            SELECT count(*) FROM pg_index i
              JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0]
             WHERE i.indrelid = CAST(:table AS regclass) AND a.attname = :column
            """
        ),
        {"table": table, "column": column},
    )
    assert found
