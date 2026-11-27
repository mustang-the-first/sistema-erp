"""Helpers for accounting tests. Entries are created ONLY through ``posting.post_entry`` with the
``build_*`` builders, exactly as the operational modules do."""

import datetime as dt
import itertools
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import Account, JournalEntry
from app.models.enums import AdjustmentReason, PaymentMethod, UserRole
from app.models.users import User
from app.services.posting import (
    build_adjustment_entry,
    build_invoice_entry,
    build_receipt_entry,
    post_entry,
)
from tests.factories import make_user

D = Decimal
_source_ids = itertools.count(1)

PostInvoice = Callable[..., JournalEntry]
PostReceipt = Callable[..., JournalEntry]
PostInitialLoad = Callable[..., JournalEntry]
AccountByCode = Callable[[str], Account]


@pytest.fixture
def poster(db: Session) -> User:
    return make_user(db, role=UserRole.ADMIN)


@pytest.fixture
def account_by_code(db: Session) -> AccountByCode:
    def get(code: str) -> Account:
        return db.scalars(select(Account).where(Account.code == code)).one()

    return get


@pytest.fixture
def post_invoice(db: Session, poster: User) -> PostInvoice:
    """D Deudores = total / C Ventas = net, IVA 21% = net x 0.21 (or 10.5%)."""

    def post(
        on: dt.date, net: str, *, vat_21: str = "0", vat_10_5: str = "0", description: str = ""
    ) -> JournalEntry:
        net_total, v21, v105 = D(net), D(vat_21), D(vat_10_5)
        draft = build_invoice_entry(
            invoice_id=next(_source_ids),
            entry_date=on,
            description=description or f"Factura {on}",
            net_total=net_total,
            vat_21=v21,
            vat_10_5=v105,
            total=net_total + v21 + v105,
        )
        return post_entry(db, draft, created_by=poster.id)

    return post


@pytest.fixture
def post_receipt(db: Session, poster: User) -> PostReceipt:
    """D Caja (cash) or Banco (transfer) / C Deudores por ventas."""

    def post(
        on: dt.date,
        amount: str,
        *,
        method: PaymentMethod = PaymentMethod.CASH,
        description: str = "",
    ) -> JournalEntry:
        draft = build_receipt_entry(
            receipt_id=next(_source_ids),
            entry_date=on,
            description=description or f"Cobranza {on}",
            method=method,
            amount=D(amount),
        )
        return post_entry(db, draft, created_by=poster.id)

    return post


@pytest.fixture
def post_initial_load(db: Session, poster: User) -> PostInitialLoad:
    """D Mercaderías / C Capital."""

    def post(on: dt.date, value: str) -> JournalEntry:
        draft = build_adjustment_entry(
            movement_id=next(_source_ids),
            entry_date=on,
            description=f"Carga inicial {on}",
            reason=AdjustmentReason.INITIAL_LOAD,
            quantity=D("1"),
            value=D(value),
        )
        assert draft is not None
        return post_entry(db, draft, created_by=poster.id)

    return post
