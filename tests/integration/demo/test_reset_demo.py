"""reset_demo runs against its own temporary database (it TRUNCATEs every table)."""

import datetime as dt
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.db import build_session_factory
from app.core.demo import DEMO_EMAILS
from app.core.security import verify_password
from app.models.catalog import Product
from app.models.sales import Invoice
from app.models.users import User
from scripts import reset_demo as reset_script
from scripts.reset_demo import reset_demo
from scripts.seed_demo import seed_demo
from tests.conftest import alembic_config, build_test_engine
from tests.factories import make_product, make_user

PASSWORD = "demo-password-for-tests"
AS_OF = dt.date(2026, 11, 27)


@pytest.fixture
def demo_db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    name = f"erp_demo_reset_{uuid4().hex[:8]}"
    admin = engine.execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = engine.url.set(database=name).render_as_string(hide_password=False)
    command.upgrade(alembic_config(url), "head")
    demo_engine = build_test_engine(url)
    try:
        yield build_session_factory(demo_engine)
    finally:
        demo_engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def _counts(session: Session) -> tuple[int, int, int]:
    return (
        session.scalar(select(func.count()).select_from(User)) or 0,
        session.scalar(select(func.count()).select_from(Product)) or 0,
        session.scalar(select(func.count()).select_from(Invoice)) or 0,
    )


def test_reset_restores_exactly_the_demo_dataset(demo_db: sessionmaker[Session]) -> None:
    with demo_db.begin() as session:
        seed_demo(session, password=PASSWORD, as_of=AS_OF)
    with demo_db() as session:
        fresh = _counts(session)
    with demo_db.begin() as session:  # visitors play with the demo
        make_user(session, email="visitor@example.com")
        make_product(session, sku="VISITOR-1")

    with demo_db.begin() as session:
        summary = reset_demo(session, password=PASSWORD, as_of=AS_OF)

    assert summary.created
    with demo_db() as session:
        assert _counts(session) == fresh
        users = {u.email: u for u in session.scalars(select(User))}
        assert set(users) == DEMO_EMAILS
        assert all(verify_password(u.password_hash, PASSWORD) for u in users.values())
        assert session.scalar(select(Product).where(Product.sku == "VISITOR-1")) is None
        first_invoice = session.scalar(select(func.min(Invoice.number)))
        assert first_invoice == 1  # numbering restarts too


def test_failed_seed_leaves_the_previous_data(
    demo_db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with demo_db.begin() as session:
        seed_demo(session, password=PASSWORD, as_of=AS_OF)
    with demo_db() as session:
        before = _counts(session)

    def broken_seed(*args: object, **kwargs: object) -> None:
        raise RuntimeError("seed failed")

    monkeypatch.setattr(reset_script, "seed_demo", broken_seed)
    with pytest.raises(RuntimeError), demo_db.begin() as session:
        reset_demo(session, password=PASSWORD, as_of=AS_OF)

    with demo_db() as session:
        assert _counts(session) == before


def test_reset_refuses_outside_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "demo_mode", False)

    assert reset_script.main() == 2
