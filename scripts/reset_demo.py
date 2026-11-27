"""Wipe the demo database and load the demo dataset again.

Usage (scheduled daily by .github/workflows/demo-reset.yml or a provider cron):
    ERP_DEMO_MODE=true uv run python -m scripts.reset_demo

Safety:
- Refuses to run unless ERP_DEMO_MODE=true, so it can never wipe a real database by mistake.
- TRUNCATE + seed run in ONE transaction: if seeding fails, the old data stays untouched.
- lock_timeout: if the app holds a long transaction, the reset fails instead of hanging.
"""

import datetime as dt
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import default_session_factory
from app.models import Base
from scripts.seed_demo import DemoSummary, demo_password_or_exit, seed_demo

LOCK_TIMEOUT = "30s"


def reset_demo(session: Session, *, password: str, as_of: dt.date | None = None) -> DemoSummary:
    """Empty every table and re-seed, inside the caller's transaction (flush only)."""
    session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    session.expunge_all()
    return seed_demo(session, password=password, as_of=as_of)


def main() -> int:
    if not get_settings().demo_mode:
        print("Refusing to reset: ERP_DEMO_MODE is not true.", file=sys.stderr)
        return 2
    password = demo_password_or_exit()
    with default_session_factory().begin() as session:
        summary = reset_demo(session, password=password)
    print("Demo reset:", ", ".join(f"{v} {k}" for k, v in summary.counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
