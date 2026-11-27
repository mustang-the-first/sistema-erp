"""Catalog: products, customers and suppliers.

Rules:
- Products: SKU unique (case-insensitive) -> ``sku_taken``; prices and cost >= 0 with at most two
  decimals; ``min_stock`` >= 0 with at most three decimals; the preferred supplier must exist and
  be active. ``stock_qty`` is never written here (only ``stock_ledger`` does). No deletes: a
  product is retired with ``is_active = False``.
- CUIT: 11 digits (dashes and spaces accepted on input, digits stored), valid AFIP check digit
  (mod 11), unique per table -> ``cuit_taken``. Customers need one unless they are
  ``consumidor_final``; suppliers always need one.
"""

import re
from collections.abc import Callable
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation
from app.core.money import CENT, MILLI
from app.models.base import Base
from app.models.catalog import Customer, Product, Supplier
from app.models.enums import TaxCondition
from app.repositories.catalog import CustomerRepository, ProductRepository, SupplierRepository
from app.schemas.catalog import (
    CustomerCreate,
    CustomerUpdate,
    ProductCreate,
    ProductUpdate,
    SupplierCreate,
    SupplierUpdate,
)

# Numeric(14,2) / Numeric(12,3): keep values strictly below the column capacity.
MAX_MONEY = Decimal("1e12")
MAX_QUANTITY = Decimal("1e9")
MAX_PAYMENT_TERMS_DAYS = 365

CUIT_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
_CUIT_SEPARATORS = re.compile(r"[\s-]")


# CUIT -------------------------------------------------------------------------------------


def cuit_check_digit(first_ten: str) -> int | None:
    """AFIP mod-11 check digit for the first ten digits; None when no valid digit exists."""
    total = sum(int(d) * w for d, w in zip(first_ten, CUIT_WEIGHTS, strict=True))
    digit = 11 - total % 11
    if digit == 11:
        return 0
    if digit == 10:
        return None
    return digit


def normalize_cuit(raw: str) -> str:
    """``"20-12345678-6"`` -> ``"20123456786"``. Raises ``invalid_cuit`` if malformed."""
    digits = _CUIT_SEPARATORS.sub("", raw)
    if not re.fullmatch(r"[0-9]{11}", digits):
        raise BusinessRuleViolation("CUIT must have 11 digits", code="invalid_cuit")
    if cuit_check_digit(digits[:10]) != int(digits[10]):
        raise BusinessRuleViolation(f"CUIT {raw} has an invalid check digit", code="invalid_cuit")
    return digits


def _optional_cuit(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    return normalize_cuit(raw)


# Amount checks ----------------------------------------------------------------------------


def check_money(value: Decimal, field: str) -> None:
    if not value.is_finite() or value < 0 or value >= MAX_MONEY or value != value.quantize(CENT):
        raise BusinessRuleViolation(
            f"{field} must be a non-negative amount with at most two decimals",
            code="invalid_amount",
        )


def check_quantity(value: Decimal, field: str) -> None:
    if (
        not value.is_finite()
        or value < 0
        or value >= MAX_QUANTITY
        or value != value.quantize(MILLI)
    ):
        raise BusinessRuleViolation(
            f"{field} must be a non-negative quantity with at most three decimals",
            code="invalid_quantity",
        )


def check_payment_terms(days: int) -> None:
    if not 0 <= days <= MAX_PAYMENT_TERMS_DAYS:
        raise BusinessRuleViolation(
            f"payment_terms_days must be between 0 and {MAX_PAYMENT_TERMS_DAYS}",
            code="invalid_payment_terms",
        )


# Persistence helper -----------------------------------------------------------------------


def _flush_unique(
    session: Session, obj: Base, *, constraint: str, error: Callable[[], BusinessRuleViolation]
) -> None:
    """Flush in a SAVEPOINT so a concurrent duplicate becomes a domain error (not a 500).

    The row is then reloaded so callers see the stored values (Numeric scale, server defaults).
    """
    try:
        with session.begin_nested():
            session.add(obj)
            session.flush()
    except IntegrityError as exc:
        if constraint in str(exc.orig):
            raise error() from exc
        raise
    session.refresh(obj)


# Products ---------------------------------------------------------------------------------


def _sku_taken(sku: str) -> BusinessRuleViolation:
    return BusinessRuleViolation(f"SKU {sku} is already in use", code="sku_taken")


def _check_supplier(session: Session, supplier_id: int) -> None:
    supplier = SupplierRepository(session).get(supplier_id)
    if supplier is None or not supplier.is_active:
        raise BusinessRuleViolation(
            f"Supplier {supplier_id} does not exist or is inactive", code="invalid_supplier"
        )


def _check_product_values(
    *, unit_price: Decimal | None, unit_cost: Decimal | None, min_stock: Decimal | None
) -> None:
    if unit_price is not None:
        check_money(unit_price, "unit_price")
    if unit_cost is not None:
        check_money(unit_cost, "unit_cost")
    if min_stock is not None:
        check_quantity(min_stock, "min_stock")


def list_products(
    session: Session, *, q: str | None, active: bool | None, limit: int, offset: int
) -> tuple[list[Product], int]:
    return ProductRepository(session).search(q=q, active=active, limit=limit, offset=offset)


def get_product(session: Session, product_id: int) -> Product:
    return ProductRepository(session).get_or_raise(product_id)


def create_product(session: Session, data: ProductCreate) -> Product:
    _check_product_values(
        unit_price=data.unit_price, unit_cost=data.unit_cost, min_stock=data.min_stock
    )
    if ProductRepository(session).sku_taken(data.sku):
        raise _sku_taken(data.sku)
    if data.supplier_id is not None:
        _check_supplier(session, data.supplier_id)
    product = Product(**data.model_dump())
    _flush_unique(
        session, product, constraint="uq_products_sku", error=lambda: _sku_taken(data.sku)
    )
    return product


def update_product(session: Session, product_id: int, data: ProductUpdate) -> Product:
    repo = ProductRepository(session)
    product = repo.get_or_raise(product_id)
    changes = data.changes()
    _check_product_values(
        unit_price=data.unit_price, unit_cost=data.unit_cost, min_stock=data.min_stock
    )
    if data.sku is not None and repo.sku_taken(data.sku, exclude_id=product.id):
        raise _sku_taken(data.sku)
    if data.supplier_id is not None and data.supplier_id != product.supplier_id:
        _check_supplier(session, data.supplier_id)
    for field, value in changes.items():
        setattr(product, field, value)
    sku = product.sku
    _flush_unique(session, product, constraint="uq_products_sku", error=lambda: _sku_taken(sku))
    return product


# Customers --------------------------------------------------------------------------------


def _cuit_taken(cuit: str) -> BusinessRuleViolation:
    return BusinessRuleViolation(f"CUIT {cuit} is already registered", code="cuit_taken")


def _check_customer_cuit(cuit: str | None, tax_condition: TaxCondition) -> None:
    if cuit is None and tax_condition is not TaxCondition.CONSUMIDOR_FINAL:
        raise BusinessRuleViolation(
            "CUIT is required unless the customer is a final consumer", code="cuit_required"
        )


def list_customers(
    session: Session, *, q: str | None, active: bool | None, limit: int, offset: int
) -> tuple[list[Customer], int]:
    return CustomerRepository(session).search(q=q, active=active, limit=limit, offset=offset)


def get_customer(session: Session, customer_id: int) -> Customer:
    return CustomerRepository(session).get_or_raise(customer_id)


def create_customer(session: Session, data: CustomerCreate) -> Customer:
    cuit = _optional_cuit(data.cuit)
    _check_customer_cuit(cuit, data.tax_condition)
    check_payment_terms(data.payment_terms_days)
    if cuit is not None and CustomerRepository(session).cuit_taken(cuit):
        raise _cuit_taken(cuit)
    customer = Customer(**(data.model_dump() | {"cuit": cuit}))
    _flush_unique(
        session, customer, constraint="uq_customers_cuit", error=lambda: _cuit_taken(str(cuit))
    )
    return customer


def update_customer(session: Session, customer_id: int, data: CustomerUpdate) -> Customer:
    repo = CustomerRepository(session)
    customer = repo.get_or_raise(customer_id)
    changes = data.changes()
    cuit = _optional_cuit(data.cuit) if "cuit" in changes else customer.cuit
    if "cuit" in changes:
        changes["cuit"] = cuit
    _check_customer_cuit(cuit, data.tax_condition or customer.tax_condition)
    if data.payment_terms_days is not None:
        check_payment_terms(data.payment_terms_days)
    if cuit is not None and repo.cuit_taken(cuit, exclude_id=customer.id):
        raise _cuit_taken(cuit)
    for field, value in changes.items():
        setattr(customer, field, value)
    _flush_unique(
        session, customer, constraint="uq_customers_cuit", error=lambda: _cuit_taken(str(cuit))
    )
    return customer


# Suppliers --------------------------------------------------------------------------------


def list_suppliers(
    session: Session, *, q: str | None, active: bool | None, limit: int, offset: int
) -> tuple[list[Supplier], int]:
    return SupplierRepository(session).search(q=q, active=active, limit=limit, offset=offset)


def list_active_suppliers(session: Session) -> list[Supplier]:
    return SupplierRepository(session).list_active()


def get_supplier(session: Session, supplier_id: int) -> Supplier:
    return SupplierRepository(session).get_or_raise(supplier_id)


def create_supplier(session: Session, data: SupplierCreate) -> Supplier:
    cuit = normalize_cuit(data.cuit)
    if SupplierRepository(session).cuit_taken(cuit):
        raise _cuit_taken(cuit)
    supplier = Supplier(**(data.model_dump() | {"cuit": cuit}))
    _flush_unique(
        session, supplier, constraint="uq_suppliers_cuit", error=lambda: _cuit_taken(cuit)
    )
    return supplier


def update_supplier(session: Session, supplier_id: int, data: SupplierUpdate) -> Supplier:
    repo = SupplierRepository(session)
    supplier = repo.get_or_raise(supplier_id)
    changes = data.changes()
    if data.cuit is not None:
        cuit = normalize_cuit(data.cuit)
        if repo.cuit_taken(cuit, exclude_id=supplier.id):
            raise _cuit_taken(cuit)
        changes["cuit"] = cuit
    for field, value in changes.items():
        setattr(supplier, field, value)
    cuit_now = supplier.cuit
    _flush_unique(
        session, supplier, constraint="uq_suppliers_cuit", error=lambda: _cuit_taken(cuit_now)
    )
    return supplier
