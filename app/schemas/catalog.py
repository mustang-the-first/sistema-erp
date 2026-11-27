"""Pydantic schemas for products, customers and suppliers.

Schemas only parse types and shapes; business rules (amount ranges, CUIT check digit,
uniqueness, supplier status) live in app/services/catalog.py so the API and the web views
enforce exactly the same rules.
"""

from decimal import Decimal
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import TaxCondition, VatRate


class Page[T](BaseModel):
    items: list[T]
    total: int


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Patch(_Input):
    """PATCH body: only sent fields change; ``null`` is accepted only for nullable columns."""

    NULLABLE: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _no_null_for_required(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None and name not in self.NULLABLE:
                raise ValueError(f"{name} cannot be null")
        return self

    def changes(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


# Products ---------------------------------------------------------------------------------


class ProductCreate(_Input):
    sku: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(default="unit", min_length=1, max_length=20)
    unit_price: Decimal
    vat_rate: VatRate
    unit_cost: Decimal
    min_stock: Decimal = Decimal("0")
    supplier_id: int | None = None


class ProductUpdate(_Patch):
    NULLABLE: ClassVar[frozenset[str]] = frozenset({"supplier_id"})

    sku: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    unit_price: Decimal | None = None
    vat_rate: VatRate | None = None
    unit_cost: Decimal | None = None
    min_stock: Decimal | None = None
    supplier_id: int | None = None
    is_active: bool | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    unit: str
    unit_price: Decimal
    vat_rate: VatRate
    unit_cost: Decimal
    stock_qty: Decimal
    min_stock: Decimal
    supplier_id: int | None
    is_active: bool


# Customers --------------------------------------------------------------------------------


class CustomerCreate(_Input):
    name: str = Field(min_length=1, max_length=200)
    cuit: str | None = Field(default=None, max_length=20)
    tax_condition: TaxCondition
    address: str | None = Field(default=None, max_length=300)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    payment_terms_days: int = 0


class CustomerUpdate(_Patch):
    NULLABLE: ClassVar[frozenset[str]] = frozenset({"cuit", "address", "email", "phone"})

    name: str | None = Field(default=None, min_length=1, max_length=200)
    cuit: str | None = Field(default=None, max_length=20)
    tax_condition: TaxCondition | None = None
    address: str | None = Field(default=None, max_length=300)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    payment_terms_days: int | None = None
    is_active: bool | None = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cuit: str | None
    tax_condition: TaxCondition
    address: str | None
    email: str | None
    phone: str | None
    payment_terms_days: int
    is_active: bool


# Suppliers --------------------------------------------------------------------------------


class SupplierCreate(_Input):
    name: str = Field(min_length=1, max_length=200)
    cuit: str = Field(max_length=20)
    tax_condition: TaxCondition
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)


class SupplierUpdate(_Patch):
    NULLABLE: ClassVar[frozenset[str]] = frozenset({"email", "phone"})

    name: str | None = Field(default=None, min_length=1, max_length=200)
    cuit: str | None = Field(default=None, max_length=20)
    tax_condition: TaxCondition | None = None
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    is_active: bool | None = None


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cuit: str
    tax_condition: TaxCondition
    email: str | None
    phone: str | None
    is_active: bool
