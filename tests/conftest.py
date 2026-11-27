"""Shared test infrastructure.

Fixtures
--------
- ``db``: Session on a connection-level transaction; each ``commit()`` only releases a
  SAVEPOINT (``join_transaction_mode="create_savepoint"``) and everything is rolled back at the
  end of the test. Use it for almost every integration test.
- ``app`` / ``client`` / ``client_as(role)``: the FastAPI app wired to ``db``; ``client_as``
  logs in a fresh user with that role and sends the CSRF header.
- ``committed_factory``: real sessions that really COMMIT (own engine, separate connections),
  for concurrency and commit-failure tests. Tables are TRUNCATEd afterwards.

Markers are added by directory: tests/unit -> unit, tests/integration -> integration,
tests/e2e -> e2e. Concurrency tests add ``@pytest.mark.concurrency`` themselves.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import build_engine, build_session_factory, get_db
from app.core.security import (
    CSRF_HEADER,
    SESSION_COOKIE,
    SESSION_TTL,
    csrf_token_for,
    hash_token,
    new_session_token,
)
from app.main import create_app
from app.models import Base
from app.models.enums import UserRole
from app.models.users import UserSession
from app.services.chart_seed import seed_chart_of_accounts
from tests.factories import make_user

ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = get_settings().test_database_url
# Seeded once per run and never truncated.
PERSISTENT_TABLES = frozenset({"accounts", "system_accounts"})
DIRECTORY_MARKERS = {"unit": "unit", "integration": "integration", "e2e": "e2e"}
CONCURRENCY_TIMEOUT_S = 60
# Server-side guards on every test connection: waiting on a lock or sitting idle inside a
# transaction becomes an error instead of an infinite hang.
TEST_CONNECT_ARGS = {
    "options": (
        "-c lock_timeout=5s -c statement_timeout=15s -c idle_in_transaction_session_timeout=10s"
    )
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        parts = item.path.relative_to(ROOT / "tests").parts
        marker = DIRECTORY_MARKERS.get(parts[0]) if parts else None
        if marker:
            item.add_marker(marker)
        if item.get_closest_marker("concurrency"):
            item.add_marker(pytest.mark.timeout(CONCURRENCY_TIMEOUT_S))


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    return config


def build_test_engine(url: str = TEST_DATABASE_URL, **kwargs: object) -> Engine:
    return build_engine(url, connect_args=TEST_CONNECT_ARGS, **kwargs)


# Database ---------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Rebuild the test database from the migrations (not create_all) once per run."""
    bootstrap = build_test_engine(poolclass=NullPool)
    try:
        with bootstrap.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        command.upgrade(alembic_config(TEST_DATABASE_URL), "head")
        with Session(bootstrap) as session, session.begin():
            seed_chart_of_accounts(session)
    finally:
        bootstrap.dispose()
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def engine(migrated_database: str) -> Iterator[Engine]:
    """Engine for the savepoint fixture ONLY. Real-commit tests never touch it."""
    savepoint_engine = build_test_engine(migrated_database)
    yield savepoint_engine
    savepoint_engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _truncate_transactional_tables(url: str) -> None:
    tables = [t.name for t in Base.metadata.sorted_tables if t.name not in PERSISTENT_TABLES]
    cleaner = build_test_engine(url, poolclass=NullPool)
    try:
        with cleaner.begin() as conn:
            conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    finally:
        cleaner.dispose()


@pytest.fixture
def committed_factory(
    request: pytest.FixtureRequest, migrated_database: str
) -> Iterator[sessionmaker[Session]]:
    """Sessions that really COMMIT, on their own engine (never the savepoint engine).

    Teardown: every session must have been closed (no connection still checked out), the
    engine is disposed, and only then the tables are TRUNCATEd from a fresh connection.
    """
    if "db" in request.fixturenames:
        pytest.fail(
            "committed_factory cannot be combined with db: the savepoint transaction would "
            "hold locks that block the TRUNCATE at teardown"
        )
    commit_engine = build_test_engine(migrated_database, pool_size=30, max_overflow=0)
    try:
        yield build_session_factory(commit_engine)
        leaked = commit_engine.pool.checkedout()  # type: ignore[attr-defined]
        assert leaked == 0, f"{leaked} connection(s) still checked out: a Session was not closed"
    finally:
        commit_engine.dispose()
        _truncate_transactional_tables(migrated_database)


# HTTP -------------------------------------------------------------------------------------


@pytest.fixture
def app(db: Session) -> FastAPI:
    application = create_app(session_factory=sessionmaker())

    def override_get_db() -> Iterator[Session]:
        try:
            yield db
            db.commit()  # releases the request SAVEPOINT; the test transaction stays open
        except BaseException:
            db.rollback()
            raise

    application.dependency_overrides[get_db] = override_get_db
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


ClientAs = Callable[..., TestClient]


@pytest.fixture
def client_as(app: FastAPI, db: Session) -> ClientAs:
    """``client_as("seller")`` -> TestClient logged in as a new active user with that role.

    The user is available as ``client.user``.
    """

    def make(role: UserRole | str, **user_fields: object) -> TestClient:
        user = make_user(db, role=UserRole(role), **user_fields)  # type: ignore[arg-type]
        token = new_session_token()
        db.add(
            UserSession(
                token_hash=hash_token(token),
                user_id=user.id,
                expires_at=datetime.now(UTC) + SESSION_TTL,
            )
        )
        db.flush()
        test_client = TestClient(app, headers={CSRF_HEADER: csrf_token_for(token)})
        test_client.cookies.set(SESSION_COOKIE, token)
        test_client.user = user  # type: ignore[attr-defined]
        return test_client

    return make
