import itertools
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.clock import today
from app.models.catalog import Customer, Product, Supplier
from app.models.enums import (
    AdjustmentReason,
    StockMovementKind,
    TaxCondition,
    UserRole,
    VatRate,
)
from app.models.users import User
from app.services.stock_ledger import MovementDraft, apply_movements
from tests.factories.users import make_user

_counter = itertools.count(1)


def _cuit(n: int) -> str:
    return f"30{n:08d}9"


def make_supplier(db: Session, *, name: str | None = None) -> Supplier:
    n = next(_counter)
    supplier = Supplier(
        name=name or f"Supplier {n}",
        cuit=_cuit(n),
        tax_condition=TaxCondition.RESPONSABLE_INSCRIPTO,
    )
    db.add(supplier)
    db.flush()
    return supplier


def make_product(
    db: Session,
    *,
    sku: str | None = None,
    name: str | None = None,
    unit_price: Decimal = Decimal("100.00"),
    vat_rate: VatRate = VatRate.VAT_21,
    unit_cost: Decimal = Decimal("60.00"),
    min_stock: Decimal = Decimal("0"),
    stock: Decimal = Decimal("0"),
    supplier: Supplier | None = None,
    created_by: User | None = None,
    is_active: bool = True,
) -> Product:
    n = next(_counter)
    product = Product(
        sku=sku or f"SKU-{n:05d}",
        name=name or f"Product {n}",
        unit_price=unit_price,
        vat_rate=vat_rate,
        unit_cost=unit_cost,
        min_stock=min_stock,
        supplier_id=supplier.id if supplier else None,
        is_active=is_active,
    )
    db.add(product)
    db.flush()
    if stock:
        user = created_by or make_user(db, role=UserRole.ADMIN)
        apply_movements(
            db,
            [
                MovementDraft(
                    product_id=product.id,
                    quantity=stock,
                    kind=StockMovementKind.ADJUSTMENT,
                    reason=AdjustmentReason.INITIAL_LOAD,
                )
            ],
            movement_date=today(),
            created_by=user.id,
        )
    return product


def make_customer(
    db: Session,
    *,
    name: str | None = None,
    tax_condition: TaxCondition = TaxCondition.RESPONSABLE_INSCRIPTO,
    cuit: str | None = None,
    payment_terms_days: int = 30,
) -> Customer:
    n = next(_counter)
    if cuit is None and tax_condition is not TaxCondition.CONSUMIDOR_FINAL:
        cuit = f"20{n:08d}3"
    customer = Customer(
        name=name or f"Customer {n}",
        cuit=cuit,
        tax_condition=tax_condition,
        payment_terms_days=payment_terms_days,
    )
    db.add(customer)
    db.flush()
    return customer
