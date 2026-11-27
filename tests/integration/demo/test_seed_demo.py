import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.accounting import JournalLine, SystemAccount
from app.models.collections import Receipt
from app.models.enums import SystemAccountKey, UserRole
from app.models.sales import DeliveryNote, Invoice, Order
from app.models.stock import StockMovement
from app.models.users import User
from app.services.invoice_balance import invoice_balances_subquery
from scripts.seed_demo import DEMO_USERS, seed_demo

PASSWORD = "demo-password-for-tests"
AS_OF = dt.date(2026, 11, 27)


@pytest.fixture
def seeded(db: Session) -> Session:
    summary = seed_demo(db, password=PASSWORD, as_of=AS_OF)
    assert summary.created
    return db


def _count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def _balance(db: Session, key: SystemAccountKey) -> Decimal:
    account_id = db.get_one(SystemAccount, key).account_id
    debit, credit = db.execute(
        select(
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        ).where(JournalLine.account_id == account_id)
    ).one()
    return Decimal(debit) - Decimal(credit)


def test_one_demo_user_per_role_can_log_in(seeded: Session, client: TestClient) -> None:
    roles = {u.email: u.role for u in seeded.scalars(select(User))}

    assert {email: roles[email] for email, _, _ in DEMO_USERS} == {
        "admin@demo.local": UserRole.ADMIN,
        "vendedor@demo.local": UserRole.SELLER,
        "contador@demo.local": UserRole.ACCOUNTANT,
    }
    for email, _, role in DEMO_USERS:
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert (response.status_code, response.json()["role"]) == (200, role.value)


def test_demo_documents_cover_every_state(seeded: Session) -> None:
    statuses = {status for status in seeded.scalars(select(Order.status))}
    uninvoiced = seeded.scalars(select(DeliveryNote).where(~DeliveryNote.invoice.has())).all()

    assert (_count(seeded, Order), _count(seeded, Invoice), _count(seeded, Receipt)) == (11, 8, 6)
    assert {s.value for s in statuses} == {"draft", "confirmed", "delivered"}
    assert len(uninvoiced) == 1  # shows the "Facturar" button
    assert {i.letter.value for i in seeded.scalars(select(Invoice))} == {"A", "B"}


def test_demo_books_are_consistent(seeded: Session) -> None:
    debit, credit = seeded.execute(
        select(func.sum(JournalLine.debit), func.sum(JournalLine.credit))
    ).one()
    balances = invoice_balances_subquery()
    open_balance = seeded.scalar(select(func.sum(balances.c.balance)))
    valued_stock = seeded.scalar(select(func.sum(StockMovement.quantity * StockMovement.unit_cost)))

    assert debit == credit
    assert _balance(seeded, SystemAccountKey.RECEIVABLES) == open_balance > 0
    assert _balance(seeded, SystemAccountKey.INVENTORY) == valued_stock > 0


def test_seed_demo_twice_changes_nothing(seeded: Session) -> None:
    before = (_count(seeded, User), _count(seeded, Order), _count(seeded, Invoice))

    again = seed_demo(seeded, password=PASSWORD, as_of=AS_OF)

    assert not again.created
    assert (_count(seeded, User), _count(seeded, Order), _count(seeded, Invoice)) == before
