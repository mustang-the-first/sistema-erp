"""Load a demo dataset: one user per role and ~4 months of order-to-cash activity.

Usage:
    uv run python -m scripts.seed_demo            # password from ERP_DEMO_PASSWORD
    ERP_DEMO_PASSWORD=... uv run python -m scripts.seed_demo

Everything goes through the business services (stock ledger, posting engine, numbering), so
the demo data obeys the same rules as data entered in the app. It refuses to run twice: if the
demo users already exist, it changes nothing and exits 0.

Demo users (password: ERP_DEMO_PASSWORD; ``demo-erp-2026`` outside production only):
    admin@demo.local (admin) · vendedor@demo.local (seller) · contador@demo.local (accountant)
"""

import datetime as dt
import sys
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.config import get_settings
from app.core.db import default_session_factory
from app.core.demo import DEFAULT_DEMO_PASSWORD, DEMO_USERS
from app.models.enums import AdjustmentReason, PaymentMethod, TaxCondition, UserRole, VatRate
from app.models.sales import DeliveryNote
from app.models.users import User
from app.schemas.catalog import CustomerCreate, ProductCreate, SupplierCreate
from app.schemas.stock import AdjustmentCreate
from app.services import catalog, collections, delivery_notes, invoices, orders, stock, users
from app.services.chart_seed import seed_chart_of_accounts

D = Decimal


@dataclass
class DemoSummary:
    created: bool
    users: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def cuit(prefix: str, body: int) -> str:
    """A valid CUIT (mod 11) starting at ``prefix + body``."""
    for n in range(body, body + 50):
        first_ten = f"{prefix}{n:08d}"
        check = catalog.cuit_check_digit(first_ten)
        if check is not None:
            return f"{first_ten}{check}"
    raise ValueError("no valid CUIT in range")  # pragma: no cover


def seed_demo(session: Session, *, password: str, as_of: dt.date | None = None) -> DemoSummary:
    """Create the demo dataset in ``session`` (flush only; the caller commits)."""
    if session.scalar(select(User).where(User.email == DEMO_USERS[0][0])) is not None:
        return DemoSummary(created=False)
    day0 = as_of or today()

    def ago(days: int) -> dt.date:
        return day0 - dt.timedelta(days=days)

    seed_chart_of_accounts(session)
    people = {
        role: users.create_user(session, email=email, full_name=name, role=role, password=password)
        for email, name, role in DEMO_USERS
    }
    admin, seller = people[UserRole.ADMIN].id, people[UserRole.SELLER].id

    # Catalog -------------------------------------------------------------------------------
    molino = catalog.create_supplier(
        session,
        SupplierCreate(
            name="Molino del Sur SA",
            cuit=cuit("30", 70111222),
            tax_condition=TaxCondition.RESPONSABLE_INSCRIPTO,
            email="ventas@molinodelsur.example",
        ),
    )
    yerbatera = catalog.create_supplier(
        session,
        SupplierCreate(
            name="Yerbatera Misionera SRL",
            cuit=cuit("30", 70333444),
            tax_condition=TaxCondition.RESPONSABLE_INSCRIPTO,
        ),
    )
    products = {}
    for sku, name, price, vat, cost, min_stock, supplier, initial in (
        ("YER-1", "Yerba mate 1 kg", "2850.00", VatRate.VAT_21, "1900.00", "20", yerbatera, "120"),
        ("YER-05", "Yerba mate 500 g", "1550.00", VatRate.VAT_21, "1000.00", "20", yerbatera, "80"),
        ("HAR-1", "Harina 000 1 kg", "980.00", VatRate.VAT_10_5, "640.00", "30", molino, "200"),
        (
            "FID-500",
            "Fideos secos 500 g",
            "1240.00",
            VatRate.VAT_10_5,
            "800.00",
            "30",
            molino,
            "150",
        ),
        (
            "ACE-900",
            "Aceite girasol 900 ml",
            "3100.00",
            VatRate.VAT_21,
            "2200.00",
            "15",
            None,
            "60",
        ),
        ("AZU-1", "Azúcar 1 kg", "1350.00", VatRate.VAT_10_5, "900.00", "40", None, "45"),
    ):
        product = catalog.create_product(
            session,
            ProductCreate(
                sku=sku,
                name=name,
                unit_price=D(price),
                vat_rate=vat,
                unit_cost=D(cost),
                min_stock=D(min_stock),
                supplier_id=supplier.id if supplier else None,
            ),
        )
        stock.create_adjustment(
            session,
            AdjustmentCreate(
                product_id=product.id,
                quantity=D(initial),
                reason=AdjustmentReason.INITIAL_LOAD,
                date=ago(140),
                note="Inventario inicial",
            ),
            created_by=admin,
        )
        products[sku] = product
    stock.create_adjustment(
        session,
        AdjustmentCreate(
            product_id=products["ACE-900"].id,
            quantity=D("-3"),
            reason=AdjustmentReason.DAMAGE,
            date=ago(40),
            note="Botellas rotas en depósito",
        ),
        created_by=admin,
    )
    stock.create_adjustment(
        session,
        AdjustmentCreate(
            product_id=products["HAR-1"].id,
            quantity=D("2"),
            reason=AdjustmentReason.COUNT_DIFFERENCE,
            date=ago(20),
            note="Conteo mensual",
        ),
        created_by=admin,
    )

    customers = {}
    for key, name, condition, tax_id, terms in (
        (
            "ri",
            "Almacén Don José SRL",
            TaxCondition.RESPONSABLE_INSCRIPTO,
            cuit("30", 71234567),
            30,
        ),
        (
            "ri2",
            "Supermercado El Trébol SA",
            TaxCondition.RESPONSABLE_INSCRIPTO,
            cuit("30", 72345678),
            15,
        ),
        ("mono", "Kiosco La Esquina", TaxCondition.MONOTRIBUTO, cuit("20", 23456789), 0),
        ("exento", "Comedor Comunitario Los Pinos", TaxCondition.EXENTO, cuit("30", 73456789), 30),
        ("cf", "Consumidor Final", TaxCondition.CONSUMIDOR_FINAL, None, 0),
    ):
        customers[key] = catalog.create_customer(
            session,
            CustomerCreate(
                name=name,
                tax_condition=condition,
                cuit=tax_id,
                payment_terms_days=terms,
            ),
        )

    # Sales: order -> delivery (full or partial) -> invoice -> receipts ---------------------
    def sell(
        customer: str,
        days_ago: int,
        lines: list[tuple[str, str]],
        *,
        deliver: bool = True,
        invoice: bool = True,
        confirm: bool = True,
    ) -> tuple[int, DeliveryNote | None, int | None]:
        view = orders.create_order(
            session,
            customer_id=customers[customer].id,
            order_date=ago(days_ago),
            created_by=seller,
            lines=[orders.OrderLineInput(products[sku].id, D(q)) for sku, q in lines],
        )
        if not confirm:
            return view.order.id, None, None
        orders.confirm_order(session, view.order.id)
        if not deliver:
            return view.order.id, None, None
        note = delivery_notes.create_delivery_note(
            session,
            view.order.id,
            note_date=ago(days_ago),
            created_by=seller,
            lines=[
                delivery_notes.DeliveryLineInput(line.id, line.quantity)
                for line in view.order.lines
            ],
        )
        if not invoice:
            return view.order.id, note, None
        issued = invoices.issue_invoice(
            session, note.id, created_by=seller, invoice_date=ago(days_ago)
        )
        return view.order.id, note, issued.invoice.id

    def pay(customer: str, days_ago: int, method: PaymentMethod, *alloc: tuple[int, str]) -> None:
        collections.create_receipt(
            session,
            customer_id=customers[customer].id,
            method=method,
            receipt_date=ago(days_ago),
            created_by=seller,
            allocations=[collections.AllocationRequest(i, D(a)) for i, a in alloc],
        )

    cash, bank = PaymentMethod.CASH, PaymentMethod.BANK_TRANSFER
    _, _, inv1 = sell("ri", 130, [("YER-1", "20"), ("HAR-1", "40")])
    _, _, inv2 = sell("ri2", 70, [("FID-500", "30"), ("ACE-900", "10")])
    _, _, inv3 = sell("mono", 60, [("YER-05", "10"), ("AZU-1", "12")])
    _, _, inv4 = sell("exento", 100, [("HAR-1", "60"), ("FID-500", "40")])
    _, _, inv5 = sell("ri", 75, [("ACE-900", "8"), ("YER-1", "15")])
    _, _, inv6 = sell("cf", 20, [("YER-05", "2")])
    _, _, inv7 = sell("ri2", 10, [("AZU-1", "20"), ("HAR-1", "50")])
    _, _, inv8 = sell("mono", 3, [("YER-1", "5")])
    if not all((inv1, inv2, inv3, inv4, inv5, inv6, inv7, inv8)):  # pragma: no cover
        raise RuntimeError("demo invoices were not issued")
    sell("ri", 2, [("FID-500", "10"), ("ACE-900", "4")], invoice=False)  # "Facturar" pending
    sell("exento", 1, [("AZU-1", "10")], deliver=False)  # confirmed, not delivered
    sell("cf", 0, [("YER-05", "1")], confirm=False)  # draft

    # Aging as of today: +90 (inv1), 61-90 (inv4), 31-60 (inv5), 0-30 (inv7, inv8).
    pay("ri", 60, bank, (inv1, "50000.00"))  # partial: the rest is 90+ days overdue
    pay("ri2", 50, bank, (inv2, _total(session, inv2)))
    pay("mono", 55, cash, (inv3, _total(session, inv3)))
    pay("exento", 15, bank, (inv4, "40000.00"))  # partial: the rest is 61-90 days overdue
    pay("cf", 20, cash, (inv6, _total(session, inv6)))
    pay("mono", 1, cash, (inv8, "5000.00"))  # partial, not yet due

    session.flush()
    return DemoSummary(
        created=True,
        users=[email for email, _, _ in DEMO_USERS],
        counts={
            "products": len(products),
            "customers": len(customers),
            "orders": 11,
            "invoices": 8,
            "receipts": 6,
        },
    )


def _total(session: Session, invoice_id: int | None) -> str:
    assert invoice_id is not None  # noqa: S101 - narrowed above
    return str(invoices.get_invoice(session, invoice_id).invoice.total)


def demo_password_or_exit() -> str:
    """ERP_DEMO_PASSWORD; the development default is never used in production."""
    settings = get_settings()
    if settings.demo_password:
        return settings.demo_password
    if settings.env == "production":
        print("ERP_DEMO_PASSWORD is required in production.", file=sys.stderr)
        raise SystemExit(1)
    return DEFAULT_DEMO_PASSWORD


def main() -> int:
    password = demo_password_or_exit()
    with default_session_factory().begin() as session:
        summary = seed_demo(session, password=password)
    if not summary.created:
        print("Demo data already loaded: nothing to do.")
        return 0
    print("Demo data loaded:", ", ".join(f"{v} {k}" for k, v in summary.counts.items()))
    print("Users:", ", ".join(summary.users), "(password: ERP_DEMO_PASSWORD)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
