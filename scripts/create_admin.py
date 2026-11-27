"""Create an admin user. Refuses (exit code 1) if the email is already in use.

Usage: uv run python -m scripts.create_admin --email admin@example.com --name "Admin"

The password is read from ERP_ADMIN_PASSWORD if set, otherwise prompted for (twice).
"""

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager

from sqlalchemy.orm import Session

from app.core.db import default_session_factory
from app.core.errors import BusinessRuleViolation
from app.models.enums import UserRole
from app.services.users import create_user

PASSWORD_ENV = "ERP_ADMIN_PASSWORD"  # noqa: S105 - env var name, not a secret

SessionScope = Callable[[], AbstractContextManager[Session]]


class PasswordMismatch(Exception):
    pass


def read_password() -> str:
    from_env = os.environ.get(PASSWORD_ENV)
    if from_env:
        return from_env
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise PasswordMismatch("Passwords do not match")
    return password


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an admin user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True, help="full name")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, session_scope: SessionScope | None = None) -> int:
    args = parse_args(argv)
    scope = session_scope or default_session_factory().begin
    try:
        password = read_password()
        with scope() as session:
            user = create_user(
                session,
                email=args.email,
                full_name=args.name,
                role=UserRole.ADMIN,
                password=password,
            )
            email = user.email
    except (BusinessRuleViolation, PasswordMismatch) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Admin {email} created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
