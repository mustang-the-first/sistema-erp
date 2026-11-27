"""Shared Jinja2 environment for the web routers, plus the role-based navigation."""

from dataclasses import dataclass
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.money import format_ars
from app.models.enums import UserRole
from app.models.users import User

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

S, C = UserRole.SELLER, UserRole.ACCOUNTANT


@dataclass(frozen=True, slots=True)
class NavItem:
    label: str
    href: str
    roles: frozenset[UserRole]  # admin always sees everything


NAV: tuple[NavItem, ...] = (
    NavItem("Pedidos", "/orders", frozenset({S})),
    NavItem("Facturas", "/invoices", frozenset({S, C})),
    NavItem("Cobranzas", "/receipts/new", frozenset({S, C})),
    NavItem("Clientes", "/customers", frozenset({S})),
    NavItem("Productos", "/products", frozenset({S, C})),
    NavItem("Proveedores", "/suppliers", frozenset()),
    NavItem("Stock", "/stock", frozenset()),
    NavItem("Plan de cuentas", "/accounting/accounts", frozenset({C})),
    NavItem("Libro diario", "/accounting/journal", frozenset({C})),
    NavItem("Libro mayor", "/accounting/ledger", frozenset({C})),
    NavItem("Reportes", "/reports/sales", frozenset({S, C})),
    NavItem("Usuarios", "/users", frozenset()),
)


def nav_for(user: User | None) -> list[NavItem]:
    if user is None:
        return []
    return [i for i in NAV if user.role is UserRole.ADMIN or user.role in i.roles]


templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["ars"] = format_ars
templates.env.globals["nav_for"] = nav_for
