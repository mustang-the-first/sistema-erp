"""Initial chart of accounts and system account mapping (idempotent)."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import Account, SystemAccount
from app.models.enums import AccountType, SystemAccountKey

ASSET, LIAB, EQUITY, INCOME, EXPENSE = (
    AccountType.ASSET,
    AccountType.LIABILITY,
    AccountType.EQUITY,
    AccountType.INCOME,
    AccountType.EXPENSE,
)
K = SystemAccountKey


@dataclass(frozen=True, slots=True)
class SeedAccount:
    code: str
    name: str
    type: AccountType
    parent: str | None = None
    system_key: SystemAccountKey | None = None

    @property
    def is_postable(self) -> bool:
        return self.system_key is not None


CHART: tuple[SeedAccount, ...] = (
    SeedAccount("1", "Activo", ASSET),
    SeedAccount("1.1", "Activo corriente", ASSET, "1"),
    SeedAccount("1.1.1", "Caja y bancos", ASSET, "1.1"),
    SeedAccount("1.1.1.01", "Caja", ASSET, "1.1.1", K.CASH),
    SeedAccount("1.1.1.02", "Banco cuenta corriente", ASSET, "1.1.1", K.BANK),
    SeedAccount("1.1.3", "Créditos por ventas", ASSET, "1.1"),
    SeedAccount("1.1.3.01", "Deudores por ventas", ASSET, "1.1.3", K.RECEIVABLES),
    SeedAccount("1.1.4", "Bienes de cambio", ASSET, "1.1"),
    SeedAccount("1.1.4.01", "Mercaderías", ASSET, "1.1.4", K.INVENTORY),
    SeedAccount("2", "Pasivo", LIAB),
    SeedAccount("2.1", "Pasivo corriente", LIAB, "2"),
    SeedAccount("2.1.3", "Deudas fiscales", LIAB, "2.1"),
    SeedAccount("2.1.3.01", "IVA Débito Fiscal 21%", LIAB, "2.1.3", K.VAT_21),
    SeedAccount("2.1.3.02", "IVA Débito Fiscal 10,5%", LIAB, "2.1.3", K.VAT_10_5),
    SeedAccount("3", "Patrimonio neto", EQUITY),
    SeedAccount("3.1", "Aportes de los propietarios", EQUITY, "3"),
    SeedAccount("3.1.01", "Capital", EQUITY, "3.1", K.CAPITAL),
    SeedAccount("4", "Ingresos", INCOME),
    SeedAccount("4.1", "Ingresos por ventas", INCOME, "4"),
    SeedAccount("4.1.01", "Ventas", INCOME, "4.1", K.SALES),
    SeedAccount("4.2", "Otros ingresos", INCOME, "4"),
    SeedAccount("4.2.01", "Sobrantes de inventario", INCOME, "4.2", K.INVENTORY_GAIN),
    SeedAccount("5", "Egresos", EXPENSE),
    SeedAccount("5.1", "Costo de ventas", EXPENSE, "5"),
    SeedAccount("5.1.01", "Costo de mercaderías vendidas", EXPENSE, "5.1", K.COGS),
    SeedAccount("5.2", "Otros egresos", EXPENSE, "5"),
    SeedAccount("5.2.01", "Faltantes y roturas de inventario", EXPENSE, "5.2", K.INVENTORY_LOSS),
)


def seed_chart_of_accounts(session: Session) -> int:
    """Create missing accounts and system mappings. Returns how many accounts were created.

    Existing accounts are left untouched (the accountant may have renamed them).
    """
    existing = {a.code: a for a in session.scalars(select(Account))}
    created = 0
    for seed in CHART:  # parents come before children in CHART
        if seed.code in existing:
            continue
        account = Account(
            code=seed.code,
            name=seed.name,
            type=seed.type,
            parent_id=existing[seed.parent].id if seed.parent else None,
            is_postable=seed.is_postable,
        )
        session.add(account)
        session.flush()
        existing[seed.code] = account
        created += 1

    mapped = set(session.scalars(select(SystemAccount.key)))
    for seed in CHART:
        if seed.system_key is not None and seed.system_key not in mapped:
            session.add(SystemAccount(key=seed.system_key, account_id=existing[seed.code].id))
    session.flush()
    return created
