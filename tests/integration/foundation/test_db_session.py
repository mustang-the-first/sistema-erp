"""Request transaction: commit happens before the response, so a failed commit is a 500.

These tests use real commits (``committed_factory``), not the savepoint fixture: the deferred
balance trigger only fires on a real COMMIT.
"""

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import today
from app.core.db import DbSession
from app.core.errors import BusinessRuleViolation
from app.main import create_app
from app.models.accounting import JournalEntry, JournalLine, SystemAccount
from app.models.enums import JournalSourceType, SystemAccountKey, UserRole
from tests.factories import make_user

pytestmark = pytest.mark.concurrency


def _entry(
    db: Session, user_id: int, *amounts: tuple[SystemAccountKey, Decimal, Decimal]
) -> JournalEntry:
    entry = JournalEntry(
        number=1,
        date=today(),
        description="test",
        source_type=JournalSourceType.INVOICE,
        source_id=1,
        created_by=user_id,
        lines=[
            JournalLine(account_id=db.get_one(SystemAccount, key).account_id, debit=d, credit=c)
            for key, d, c in amounts
        ],
    )
    db.add(entry)
    db.flush()
    return entry


def build_app(factory: sessionmaker[Session], user_id: int) -> FastAPI:
    app = create_app(session_factory=factory)
    one, zero = Decimal("100.00"), Decimal("0")

    @app.post("/_test/unbalanced")
    def unbalanced(db: DbSession) -> dict[str, str]:
        _entry(db, user_id, (SystemAccountKey.CASH, one, zero))  # one line: fails at COMMIT
        return {"status": "created"}

    @app.post("/_test/balanced")
    def balanced(db: DbSession) -> dict[str, int]:
        entry = _entry(
            db,
            user_id,
            (SystemAccountKey.CASH, one, zero),
            (SystemAccountKey.RECEIVABLES, zero, one),
        )
        return {"id": entry.id}

    @app.post("/api/v1/_test/domain-error")
    @app.post("/_test/domain-error")
    def domain_error(db: DbSession) -> None:
        _entry(
            db,
            user_id,
            (SystemAccountKey.CASH, one, zero),
            (SystemAccountKey.RECEIVABLES, zero, one),
        )
        raise BusinessRuleViolation("nope")

    return app


def entries(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(JournalEntry)) or 0


@pytest.fixture
def client(committed_factory: sessionmaker[Session]) -> TestClient:
    with committed_factory.begin() as session:
        user_id = make_user(session, role=UserRole.ADMIN).id
    return TestClient(build_app(committed_factory, user_id), raise_server_exceptions=False)


def test_failed_commit_returns_500_and_persists_nothing(
    client: TestClient, committed_factory: sessionmaker[Session]
) -> None:
    response = client.post("/_test/unbalanced")

    assert response.status_code == 500
    assert entries(committed_factory) == 0


def test_successful_request_is_committed(
    client: TestClient, committed_factory: sessionmaker[Session]
) -> None:
    response = client.post("/_test/balanced")

    assert response.status_code == 200
    with committed_factory() as session:
        assert session.get(JournalEntry, response.json()["id"]) is not None


def test_domain_error_rolls_back_the_request(
    client: TestClient, committed_factory: sessionmaker[Session]
) -> None:
    response = client.post("/api/v1/_test/domain-error")

    assert response.status_code == 422
    assert response.json() == {"detail": "nope", "code": "business_rule_violation"}
    assert entries(committed_factory) == 0


def test_web_domain_error_is_flashed_and_still_rolls_back(
    client: TestClient, committed_factory: sessionmaker[Session]
) -> None:
    """200 + #flash for HTMX, but the request transaction is rolled back all the same."""
    response = client.post("/_test/domain-error", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#flash"
    assert "nope" in response.text
    assert entries(committed_factory) == 0
