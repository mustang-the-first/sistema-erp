"""Public demo mode (ERP_DEMO_MODE=true).

The demo accounts below are shared with every visitor, so in demo mode they cannot lock others
out: no password changes, no role changes, no deactivations (see app/services/users.py). Data
is wiped and re-seeded daily by ``scripts.reset_demo``.
"""

from app.core.config import get_settings
from app.models.enums import UserRole

DEMO_USERS: tuple[tuple[str, str, UserRole], ...] = (
    ("admin@demo.local", "Ana Admin", UserRole.ADMIN),
    ("vendedor@demo.local", "Vito Vendedor", UserRole.SELLER),
    ("contador@demo.local", "Carla Contadora", UserRole.ACCOUNTANT),
)
DEMO_EMAILS = frozenset(email for email, _, _ in DEMO_USERS)
RESET_TIME_LABEL = "03:00 (hora de Argentina)"
# Development/test only: production requires ERP_DEMO_PASSWORD (see app/core/config.py).
DEFAULT_DEMO_PASSWORD = "demo-erp-2026"  # noqa: S105 - public demo credential


def demo_mode() -> bool:
    return get_settings().demo_mode


def demo_password() -> str:
    """The shared password shown on the login page in demo mode (it is public on purpose)."""
    return get_settings().demo_password or DEFAULT_DEMO_PASSWORD


def is_demo_account(email: str) -> bool:
    return email.lower() in DEMO_EMAILS
