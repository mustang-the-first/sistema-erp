"""Catalog JSON API (/api/v1): products, customers and suppliers.

Owner: agent B · Catalog & stock (docs/plan.md).
Roles: products read by everyone, written by admin; customers seller (+admin); suppliers admin.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db import DbSession
from app.core.deps import CurrentUser, require_roles
from app.models.enums import UserRole
from app.models.users import User
from app.schemas.catalog import (
    CustomerCreate,
    CustomerOut,
    CustomerUpdate,
    Page,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    SupplierCreate,
    SupplierOut,
    SupplierUpdate,
)
from app.services import catalog

router = APIRouter(tags=["catalog"])

AdminOnly = Annotated[User, Depends(require_roles())]
SellerOrAdmin = Annotated[User, Depends(require_roles(UserRole.SELLER))]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
Search = Annotated[str | None, Query(max_length=100)]


# Products ---------------------------------------------------------------------------------


@router.get("/products", response_model=Page[ProductOut])
def list_products(
    db: DbSession,
    _: CurrentUser,
    q: Search = None,
    active: bool | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[ProductOut]:
    items, total = catalog.list_products(db, q=q, active=active, limit=limit, offset=offset)
    return Page(items=[ProductOut.model_validate(p) for p in items], total=total)


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(body: ProductCreate, db: DbSession, _: AdminOnly) -> ProductOut:
    return ProductOut.model_validate(catalog.create_product(db, body))


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: DbSession, _: CurrentUser) -> ProductOut:
    return ProductOut.model_validate(catalog.get_product(db, product_id))


@router.patch("/products/{product_id}", response_model=ProductOut)
def update_product(product_id: int, body: ProductUpdate, db: DbSession, _: AdminOnly) -> ProductOut:
    return ProductOut.model_validate(catalog.update_product(db, product_id, body))


# Customers --------------------------------------------------------------------------------


@router.get("/customers", response_model=Page[CustomerOut])
def list_customers(
    db: DbSession,
    _: SellerOrAdmin,
    q: Search = None,
    active: bool | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[CustomerOut]:
    items, total = catalog.list_customers(db, q=q, active=active, limit=limit, offset=offset)
    return Page(items=[CustomerOut.model_validate(c) for c in items], total=total)


@router.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(body: CustomerCreate, db: DbSession, _: SellerOrAdmin) -> CustomerOut:
    return CustomerOut.model_validate(catalog.create_customer(db, body))


@router.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: int, db: DbSession, _: SellerOrAdmin) -> CustomerOut:
    return CustomerOut.model_validate(catalog.get_customer(db, customer_id))


@router.patch("/customers/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int, body: CustomerUpdate, db: DbSession, _: SellerOrAdmin
) -> CustomerOut:
    return CustomerOut.model_validate(catalog.update_customer(db, customer_id, body))


# Suppliers --------------------------------------------------------------------------------


@router.get("/suppliers", response_model=Page[SupplierOut])
def list_suppliers(
    db: DbSession,
    _: AdminOnly,
    q: Search = None,
    active: bool | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[SupplierOut]:
    items, total = catalog.list_suppliers(db, q=q, active=active, limit=limit, offset=offset)
    return Page(items=[SupplierOut.model_validate(s) for s in items], total=total)


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
def create_supplier(body: SupplierCreate, db: DbSession, _: AdminOnly) -> SupplierOut:
    return SupplierOut.model_validate(catalog.create_supplier(db, body))


@router.get("/suppliers/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: int, db: DbSession, _: AdminOnly) -> SupplierOut:
    return SupplierOut.model_validate(catalog.get_supplier(db, supplier_id))


@router.patch("/suppliers/{supplier_id}", response_model=SupplierOut)
def update_supplier(
    supplier_id: int, body: SupplierUpdate, db: DbSession, _: AdminOnly
) -> SupplierOut:
    return SupplierOut.model_validate(catalog.update_supplier(db, supplier_id, body))
