"""THE invoice balance: total - sum(receipt allocations). Single implementation.

Sales (open/paid status), Collections (open invoices, allocation limits) and Reports
(receivables, aging) must use these functions; none may reimplement the formula.
"""

import datetime as dt
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import Subquery, func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.models.collections import Receipt, ReceiptAllocation
from app.models.sales import Invoice


def invoice_balances_subquery(as_of: dt.date | None = None) -> Subquery:
    """One row per invoice: invoice_id, customer_id, date, due_date, total, paid, balance.

    With ``as_of``, only invoices issued and receipts dated on or before that day count
    (receivables "as of" a date).
    """
    paid_stmt = (
        select(
            ReceiptAllocation.invoice_id,
            func.sum(ReceiptAllocation.amount).label("paid"),
        )
        .join(Receipt, Receipt.id == ReceiptAllocation.receipt_id)
        .group_by(ReceiptAllocation.invoice_id)
    )
    if as_of is not None:
        paid_stmt = paid_stmt.where(Receipt.date <= as_of)
    paid = paid_stmt.subquery("invoice_paid")

    paid_amount = func.coalesce(paid.c.paid, 0)
    stmt = select(
        Invoice.id.label("invoice_id"),
        Invoice.customer_id,
        Invoice.date,
        Invoice.due_date,
        Invoice.total,
        paid_amount.label("paid"),
        (Invoice.total - paid_amount).label("balance"),
    ).outerjoin(paid, paid.c.invoice_id == Invoice.id)
    if as_of is not None:
        stmt = stmt.where(Invoice.date <= as_of)
    return stmt.subquery("invoice_balances")


def invoice_balances(
    session: Session, invoice_ids: Iterable[int], *, as_of: dt.date | None = None
) -> dict[int, Decimal]:
    ids = list(invoice_ids)
    if not ids:
        return {}
    balances = invoice_balances_subquery(as_of)
    rows = session.execute(
        select(balances.c.invoice_id, balances.c.balance).where(balances.c.invoice_id.in_(ids))
    ).all()
    return {invoice_id: balance for invoice_id, balance in rows}


def invoice_balance(session: Session, invoice_id: int, *, as_of: dt.date | None = None) -> Decimal:
    balance = invoice_balances(session, [invoice_id], as_of=as_of).get(invoice_id)
    if balance is None:
        raise NotFound(f"Invoice {invoice_id} not found")
    return balance
