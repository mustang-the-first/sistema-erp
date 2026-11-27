"""Seed the chart of accounts and system account mapping. Safe to run repeatedly.

Usage: uv run python -m scripts.seed_chart_of_accounts
"""

from app.core.db import default_session_factory
from app.services.chart_seed import seed_chart_of_accounts


def main() -> None:
    with default_session_factory().begin() as session:
        created = seed_chart_of_accounts(session)
    print(f"Chart of accounts ready ({created} accounts created).")


if __name__ == "__main__":
    main()
