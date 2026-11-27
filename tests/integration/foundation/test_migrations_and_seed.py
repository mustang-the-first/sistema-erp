from uuid import uuid4

from alembic import command
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.models import Base
from app.models.accounting import Account, SystemAccount
from app.models.enums import SystemAccountKey
from app.services.chart_seed import CHART, seed_chart_of_accounts
from tests.conftest import alembic_config, build_test_engine


def _public_tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        return {row[0] for row in rows}


def _balance_function_exists(engine: Engine) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.scalar(
                text("SELECT count(*) FROM pg_proc WHERE proname = 'check_journal_entry_balanced'")
            )
        )


def test_upgrade_check_downgrade_on_a_clean_database(engine: Engine) -> None:
    name = f"erp_migrations_{uuid4().hex[:8]}"
    admin = engine.execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = engine.url.set(database=name).render_as_string(hide_password=False)
    fresh = build_test_engine(url)
    config = alembic_config(url)
    try:
        command.upgrade(config, "head")
        assert _public_tables(fresh) == set(Base.metadata.tables) | {"alembic_version"}
        assert _balance_function_exists(fresh)
        command.check(config)  # models and migrations in sync

        command.downgrade(config, "base")
        assert _public_tables(fresh) == {"alembic_version"}
        assert not _balance_function_exists(fresh)

        command.upgrade(config, "head")  # and it can be re-applied
        assert _public_tables(fresh) == set(Base.metadata.tables) | {"alembic_version"}
    finally:
        fresh.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def test_chart_of_accounts_is_seeded_and_mapped(db: Session) -> None:
    accounts = {a.code: a for a in db.scalars(select(Account))}
    mapping = {row.key: row.account.code for row in db.scalars(select(SystemAccount))}

    assert set(accounts) == {seed.code for seed in CHART}
    assert mapping == {
        SystemAccountKey.CASH: "1.1.1.01",
        SystemAccountKey.BANK: "1.1.1.02",
        SystemAccountKey.RECEIVABLES: "1.1.3.01",
        SystemAccountKey.INVENTORY: "1.1.4.01",
        SystemAccountKey.VAT_21: "2.1.3.01",
        SystemAccountKey.VAT_10_5: "2.1.3.02",
        SystemAccountKey.CAPITAL: "3.1.01",
        SystemAccountKey.SALES: "4.1.01",
        SystemAccountKey.INVENTORY_GAIN: "4.2.01",
        SystemAccountKey.COGS: "5.1.01",
        SystemAccountKey.INVENTORY_LOSS: "5.2.01",
    }
    assert all(accounts[code].is_postable for code in mapping.values())
    assert not accounts["1.1"].is_postable
    assert accounts["1.1.3.01"].parent is accounts["1.1.3"]


def test_seed_is_idempotent(db: Session) -> None:
    before = db.scalar(select(func.count()).select_from(Account))

    assert seed_chart_of_accounts(db) == 0
    assert db.scalar(select(func.count()).select_from(Account)) == before
