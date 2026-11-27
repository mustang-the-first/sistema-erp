"""Catalog HTML views (Jinja2 + HTMX): /products, /customers, /suppliers.

Owner: agent B · Catalog & stock (docs/plan.md).

Forms submit with ``hx-post``/``hx-patch`` and target ``#flash``. On success the response is a
flash message plus an ``HX-Trigger`` event that makes the list table reload itself; on a domain
or validation error it is the error partial (status 200 so HTMX swaps it) and the service work
done inside the request's SAVEPOINT is rolled back.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.db import DbSession
from app.core.deps import CurrentUser, require_roles
from app.core.errors import DomainError
from app.core.templating import templates
from app.models.enums import TaxCondition, UserRole, VatRate
from app.models.users import User
from app.schemas.catalog import (
    CustomerCreate,
    CustomerUpdate,
    ProductCreate,
    ProductUpdate,
    SupplierCreate,
    SupplierUpdate,
)
from app.services import catalog

router = APIRouter(tags=["catalog"])

AdminOnly = Annotated[User, Depends(require_roles())]
SellerOrAdmin = Annotated[User, Depends(require_roles(UserRole.SELLER))]
PAGE_SIZE = 100

TAX_CONDITION_LABELS = {
    TaxCondition.RESPONSABLE_INSCRIPTO: "Responsable Inscripto",
    TaxCondition.MONOTRIBUTO: "Monotributo",
    TaxCondition.EXENTO: "Exento",
    TaxCondition.CONSUMIDOR_FINAL: "Consumidor Final",
}
VAT_LABELS = {VatRate.VAT_21: "21%", VatRate.VAT_10_5: "10,5%"}


# HTMX helpers (also used by app/routers/web/stock.py) -------------------------------------


class FormError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def render(request: Request, template: str, context: dict[str, Any], **kwargs: Any) -> HTMLResponse:
    base = {
        "tax_condition_labels": TAX_CONDITION_LABELS,
        "vat_labels": VAT_LABELS,
        "user": getattr(request.state, "user", None),
    }
    return templates.TemplateResponse(request, template, base | context, **kwargs)


async def _read_form(request: Request, checkboxes: tuple[str, ...]) -> dict[str, Any]:
    form = await request.form()
    data: dict[str, Any] = {}
    for key, value in form.items():
        if isinstance(value, str):
            data[key] = value.strip() or None
    for name in checkboxes:
        data[name] = name in form
    return data


async def create_form(request: Request) -> dict[str, Any]:
    """Form fields as a dict; blank values become None."""
    return await _read_form(request, ())


async def edit_form(request: Request) -> dict[str, Any]:
    """Like ``create_form`` plus the ``is_active`` checkbox as a boolean."""
    return await _read_form(request, ("is_active",))


CreateForm = Annotated[dict[str, Any], Depends(create_form)]
EditForm = Annotated[dict[str, Any], Depends(edit_form)]


def parse[S: BaseModel](schema: type[S], data: dict[str, Any], *, drop_none: bool) -> S:
    if drop_none:
        data = {k: v for k, v in data.items() if v is not None}
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'form'}: {err['msg']}"
            for err in exc.errors()
        )
        raise FormError(details) from exc


@contextmanager
def form_action(db: Session) -> Iterator[None]:
    """Run service calls in a SAVEPOINT: a rejected form leaves nothing behind."""
    with db.begin_nested():
        yield


def handle_form(
    request: Request,
    db: Session,
    action: Callable[[], str],
    *,
    event: str,
) -> HTMLResponse:
    """Run ``action`` (returns the success message) and render the flash or the error."""
    try:
        with form_action(db):
            message = action()
    except (DomainError, FormError) as exc:
        return render(request, "partials/error.html", {"detail": exc.message})
    return render(
        request,
        "partials/flash.html",
        {"message": message, "level": "success"},
        headers={"HX-Trigger": event},
    )


# Products ---------------------------------------------------------------------------------


def _bool_filter(value: str | None) -> bool | None:
    return {"true": True, "false": False}.get(value or "")


@router.get("/products", response_class=HTMLResponse)
def products_page(
    request: Request,
    db: DbSession,
    _: CurrentUser,
    q: str | None = None,
    active: str | None = "true",
    partial: bool = False,
) -> Response:
    items, total = catalog.list_products(
        db, q=q or None, active=_bool_filter(active), limit=PAGE_SIZE, offset=0
    )
    context = {"products": items, "total": total, "q": q or "", "active": active or ""}
    if partial:
        return render(request, "catalog/_products_table.html", context)
    context["suppliers"] = catalog.list_active_suppliers(db)
    return render(request, "catalog/products.html", context)


@router.post("/products", response_class=HTMLResponse)
def create_product(request: Request, data: CreateForm, db: DbSession, _: AdminOnly) -> Response:

    def action() -> str:
        product = catalog.create_product(db, parse(ProductCreate, data, drop_none=True))
        return f"Producto {product.sku} creado"

    return handle_form(request, db, action, event="products-changed")


@router.get("/products/{product_id}", response_class=HTMLResponse)
def product_page(request: Request, product_id: int, db: DbSession, _: AdminOnly) -> Response:
    product = catalog.get_product(db, product_id)
    suppliers = catalog.list_active_suppliers(db)
    return render(
        request, "catalog/product_edit.html", {"product": product, "suppliers": suppliers}
    )


@router.patch("/products/{product_id}", response_class=HTMLResponse)
def update_product(
    request: Request, product_id: int, data: EditForm, db: DbSession, _: AdminOnly
) -> Response:

    def action() -> str:
        product = catalog.update_product(
            db, product_id, parse(ProductUpdate, data, drop_none=False)
        )
        return f"Producto {product.sku} actualizado"

    return handle_form(request, db, action, event="products-changed")


# Customers --------------------------------------------------------------------------------


@router.get("/customers", response_class=HTMLResponse)
def customers_page(
    request: Request,
    db: DbSession,
    _: SellerOrAdmin,
    q: str | None = None,
    active: str | None = "true",
    partial: bool = False,
) -> Response:
    items, total = catalog.list_customers(
        db, q=q or None, active=_bool_filter(active), limit=PAGE_SIZE, offset=0
    )
    context = {"customers": items, "total": total, "q": q or "", "active": active or ""}
    template = "catalog/_customers_table.html" if partial else "catalog/customers.html"
    return render(request, template, context)


@router.post("/customers", response_class=HTMLResponse)
def create_customer(
    request: Request, data: CreateForm, db: DbSession, _: SellerOrAdmin
) -> Response:

    def action() -> str:
        customer = catalog.create_customer(db, parse(CustomerCreate, data, drop_none=True))
        return f"Cliente {customer.name} creado"

    return handle_form(request, db, action, event="customers-changed")


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def customer_page(request: Request, customer_id: int, db: DbSession, _: SellerOrAdmin) -> Response:
    customer = catalog.get_customer(db, customer_id)
    return render(request, "catalog/customer_edit.html", {"customer": customer})


@router.patch("/customers/{customer_id}", response_class=HTMLResponse)
def update_customer(
    request: Request, customer_id: int, data: EditForm, db: DbSession, _: SellerOrAdmin
) -> Response:

    def action() -> str:
        customer = catalog.update_customer(
            db, customer_id, parse(CustomerUpdate, data, drop_none=False)
        )
        return f"Cliente {customer.name} actualizado"

    return handle_form(request, db, action, event="customers-changed")


# Suppliers --------------------------------------------------------------------------------


@router.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(
    request: Request,
    db: DbSession,
    _: AdminOnly,
    q: str | None = None,
    active: str | None = "true",
    partial: bool = False,
) -> Response:
    items, total = catalog.list_suppliers(
        db, q=q or None, active=_bool_filter(active), limit=PAGE_SIZE, offset=0
    )
    context = {"suppliers": items, "total": total, "q": q or "", "active": active or ""}
    template = "catalog/_suppliers_table.html" if partial else "catalog/suppliers.html"
    return render(request, template, context)


@router.post("/suppliers", response_class=HTMLResponse)
def create_supplier(request: Request, data: CreateForm, db: DbSession, _: AdminOnly) -> Response:

    def action() -> str:
        supplier = catalog.create_supplier(db, parse(SupplierCreate, data, drop_none=True))
        return f"Proveedor {supplier.name} creado"

    return handle_form(request, db, action, event="suppliers-changed")


@router.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
def supplier_page(request: Request, supplier_id: int, db: DbSession, _: AdminOnly) -> Response:
    supplier = catalog.get_supplier(db, supplier_id)
    return render(request, "catalog/supplier_edit.html", {"supplier": supplier})


@router.patch("/suppliers/{supplier_id}", response_class=HTMLResponse)
def update_supplier(
    request: Request, supplier_id: int, data: EditForm, db: DbSession, _: AdminOnly
) -> Response:

    def action() -> str:
        supplier = catalog.update_supplier(
            db, supplier_id, parse(SupplierUpdate, data, drop_none=False)
        )
        return f"Proveedor {supplier.name} actualizado"

    return handle_form(request, db, action, event="suppliers-changed")
