"""Collections: receipts applied to invoices, open invoices and the customer statement.

Rules for a receipt (docs/plan.md, sections 2-4):

- the customer exists; at least one allocation; no invoice twice;
- every amount is > 0 with at most two decimals;
- every invoice belongs to the customer and is dated on or before the receipt;
- every amount is <= the invoice balance (``invoice_balance``, the Foundation's single
  implementation). The invoice rows are locked FOR UPDATE, ordered by id, *before* the balances
  are read, so concurrent receipts for the same invoice are serialized and cannot overpay;
- receipt amount = sum of allocations; number from the RECEIPT sequence of the configured point
  of sale; journal entry via ``build_receipt_entry`` + ``post_entry``.

Services only flush; the request transaction commits.
"""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.config import get_settings
from app.core.errors import BusinessRuleViolation, NotFound
from app.core.money import ZERO, has_at_most_two_decimals
from app.models.catalog import Customer
from app.models.collections import Receipt, ReceiptAllocation
from app.models.enums import DocumentType, PaymentMethod
from app.models.sales import Invoice
from app.repositories.collections import (
    CustomerReadRepository,
    InvoiceReadRepository,
    ReceiptRepository,
)
from app.services.invoice_balance import invoice_balances
from app.services.posting import build_receipt_entry, post_entry
from app.services.sequences import next_number


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    invoice_id: int
    amount: Decimal


# Formatting -------------------------------------------------------------------------------


def format_document_number(point_of_sale: int, number: int) -> str:
    """0001-00000012"""
    return f"{point_of_sale:04d}-{number:08d}"


def format_invoice_number(invoice: Invoice) -> str:
    """A 0001-00000012"""
    return f"{invoice.letter} {format_document_number(invoice.point_of_sale, invoice.number)}"


def days_overdue(due_date: dt.date, as_of: dt.date) -> int:
    return max(0, (as_of - due_date).days)


# Validation (pure) ------------------------------------------------------------------------


def validate_allocation_requests(allocations: Sequence[AllocationRequest]) -> None:
    """Checks that need no database: presence, duplicates and amount format."""
    if not allocations:
        raise BusinessRuleViolation(
            "A receipt needs at least one allocation", code="no_allocations"
        )
    seen: set[int] = set()
    for allocation in allocations:
        if allocation.invoice_id in seen:
            raise BusinessRuleViolation(
                f"Invoice {allocation.invoice_id} is allocated more than once",
                code="duplicate_invoice",
            )
        seen.add(allocation.invoice_id)
        if not allocation.amount.is_finite() or allocation.amount <= 0:
            raise BusinessRuleViolation(
                f"Amount for invoice {allocation.invoice_id} must be positive",
                code="invalid_amount",
            )
        if not has_at_most_two_decimals(allocation.amount):
            raise BusinessRuleViolation(
                f"Amount for invoice {allocation.invoice_id} has more than two decimals",
                code="invalid_amount",
            )


def check_allocation_against_invoice(
    allocation: AllocationRequest,
    invoice: Invoice,
    *,
    customer_id: int,
    receipt_date: dt.date,
    balance: Decimal,
) -> None:
    label = format_invoice_number(invoice)
    if invoice.customer_id != customer_id:
        raise BusinessRuleViolation(
            f"Invoice {label} does not belong to customer {customer_id}", code="invoice_not_owned"
        )
    if invoice.date > receipt_date:
        raise BusinessRuleViolation(
            f"Invoice {label} is dated after the receipt", code="invoice_after_receipt"
        )
    if allocation.amount > balance:
        raise BusinessRuleViolation(
            f"Amount {allocation.amount} exceeds the balance {balance} of invoice {label}",
            code="overpayment",
        )


# Receipts ---------------------------------------------------------------------------------


def create_receipt(
    session: Session,
    *,
    customer_id: int,
    method: PaymentMethod,
    allocations: Sequence[AllocationRequest],
    created_by: int,
    receipt_date: dt.date | None = None,
    reference: str | None = None,
) -> Receipt:
    receipt_date = receipt_date or today()
    customer = _get_customer(session, customer_id)
    validate_allocation_requests(allocations)

    invoice_ids = sorted(a.invoice_id for a in allocations)
    invoices = {inv.id: inv for inv in InvoiceReadRepository(session).lock_many(invoice_ids)}
    missing = [i for i in invoice_ids if i not in invoices]
    if missing:
        raise NotFound(f"Invoice {missing[0]} not found", code="invoice_not_found")
    # Read balances only once the rows are locked: concurrent receipts are already committed.
    balances = invoice_balances(session, invoice_ids)
    for allocation in allocations:
        check_allocation_against_invoice(
            allocation,
            invoices[allocation.invoice_id],
            customer_id=customer.id,
            receipt_date=receipt_date,
            balance=balances[allocation.invoice_id],
        )

    point_of_sale = get_settings().point_of_sale
    amount = sum((a.amount for a in allocations), ZERO)
    receipt = ReceiptRepository(session).add(
        Receipt(
            point_of_sale=point_of_sale,
            number=next_number(session, DocumentType.RECEIPT, point_of_sale=point_of_sale),
            customer_id=customer.id,
            date=receipt_date,
            method=method,
            reference=(reference or "").strip() or None,
            amount=amount,
            created_by=created_by,
            allocations=[
                ReceiptAllocation(invoice_id=a.invoice_id, amount=a.amount) for a in allocations
            ],
        )
    )
    post_entry(
        session,
        build_receipt_entry(
            receipt_id=receipt.id,
            entry_date=receipt.date,
            description=(
                f"Cobranza {format_document_number(point_of_sale, receipt.number)}"
                f" - {customer.name}"
            )[:300],
            method=method,
            amount=amount,
        ),
        created_by=created_by,
    )
    return receipt


def get_receipt(session: Session, receipt_id: int) -> Receipt:
    receipt = ReceiptRepository(session).get_with_allocations(receipt_id)
    if receipt is None:
        raise NotFound(f"Receipt {receipt_id} not found", code="receipt_not_found")
    return receipt


@dataclass(frozen=True, slots=True)
class ReceiptPage:
    items: list[Receipt]
    total: int


def list_receipts(
    session: Session,
    *,
    customer_id: int | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> ReceiptPage:
    _check_range(date_from, date_to)
    items, total = ReceiptRepository(session).search(
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return ReceiptPage(items=items, total=total)


# Open invoices ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenInvoice:
    invoice: Invoice
    paid: Decimal
    balance: Decimal
    days_overdue: int

    @property
    def document_number(self) -> str:
        return format_invoice_number(self.invoice)


@dataclass(frozen=True, slots=True)
class OpenInvoicePage:
    customer: Customer
    items: list[OpenInvoice]
    total: int

    @property
    def total_balance(self) -> Decimal:
        return sum((item.balance for item in self.items), ZERO)


def open_invoices(
    session: Session,
    customer_id: int,
    *,
    as_of: dt.date | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> OpenInvoicePage:
    """Invoices of the customer with balance > 0 (via ``invoice_balances_subquery``)."""
    customer = _get_customer(session, customer_id)
    as_of = as_of or today()
    rows, total = InvoiceReadRepository(session).open_for_customer(
        customer.id, limit=limit, offset=offset
    )
    items = [
        OpenInvoice(
            invoice=invoice,
            paid=Decimal(paid),
            balance=Decimal(balance),
            days_overdue=days_overdue(invoice.due_date, as_of),
        )
        for invoice, paid, balance in rows
    ]
    return OpenInvoicePage(customer=customer, items=items, total=total)


# Statement --------------------------------------------------------------------------------


class MovementKind(StrEnum):
    INVOICE = "invoice"
    RECEIPT = "receipt"


@dataclass(frozen=True, slots=True)
class StatementMovement:
    date: dt.date
    kind: MovementKind
    document_id: int
    document_number: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


@dataclass(frozen=True, slots=True)
class Statement:
    customer: Customer
    date_from: dt.date | None
    date_to: dt.date | None
    opening_balance: Decimal
    movements: list[StatementMovement]
    closing_balance: Decimal

    @property
    def total_debit(self) -> Decimal:
        return sum((m.debit for m in self.movements), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((m.credit for m in self.movements), ZERO)


def build_movements(
    opening_balance: Decimal,
    invoices: Sequence[Invoice],
    receipts: Sequence[Receipt],
) -> list[StatementMovement]:
    """Chronological movements with running balance. Same day: invoices before receipts."""
    raw: list[tuple[tuple[dt.date, int, int], MovementKind, int, str, Decimal, Decimal]] = []
    for inv in invoices:
        raw.append(
            (
                (inv.date, 0, inv.id),
                MovementKind.INVOICE,
                inv.id,
                f"Factura {format_invoice_number(inv)}",
                inv.total,
                ZERO,
            )
        )
    for rec in receipts:
        raw.append(
            (
                (rec.date, 1, rec.id),
                MovementKind.RECEIPT,
                rec.id,
                f"Cobranza {format_document_number(rec.point_of_sale, rec.number)}",
                ZERO,
                rec.amount,
            )
        )
    raw.sort(key=lambda item: item[0])

    balance = opening_balance
    movements: list[StatementMovement] = []
    for (day, _, _), kind, doc_id, label, debit_amount, credit_amount in raw:
        balance = balance + debit_amount - credit_amount
        movements.append(
            StatementMovement(
                date=day,
                kind=kind,
                document_id=doc_id,
                document_number=label,
                debit=debit_amount,
                credit=credit_amount,
                balance=balance,
            )
        )
    return movements


def customer_statement(
    session: Session,
    customer_id: int,
    *,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
) -> Statement:
    """Opening balance (everything before ``date_from``), movements, closing balance."""
    _check_range(date_from, date_to)
    customer = _get_customer(session, customer_id)
    invoices_repo = InvoiceReadRepository(session)
    receipts_repo = ReceiptRepository(session)

    opening = ZERO
    if date_from is not None:
        opening = invoices_repo.invoices_total_before(
            customer.id, date_from
        ) - receipts_repo.receipts_total_before(customer.id, date_from)
    movements = build_movements(
        opening,
        invoices_repo.invoices_between(customer.id, date_from, date_to),
        receipts_repo.receipts_between(customer.id, date_from, date_to),
    )
    closing = movements[-1].balance if movements else opening
    return Statement(
        customer=customer,
        date_from=date_from,
        date_to=date_to,
        opening_balance=opening,
        movements=movements,
        closing_balance=closing,
    )


# Helpers ----------------------------------------------------------------------------------


def active_customers(session: Session) -> list[Customer]:
    return CustomerReadRepository(session).active()


def _get_customer(session: Session, customer_id: int) -> Customer:
    customer = CustomerReadRepository(session).get(customer_id)
    if customer is None:
        raise NotFound(f"Customer {customer_id} not found", code="customer_not_found")
    return customer


def _check_range(date_from: dt.date | None, date_to: dt.date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise BusinessRuleViolation("'from' must be on or before 'to'", code="invalid_date_range")
