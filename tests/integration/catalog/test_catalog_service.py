from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, NotFound
from app.models.enums import TaxCondition, VatRate
from app.repositories.catalog import CustomerRepository, ProductRepository, SupplierRepository
from app.schemas.catalog import (
    CustomerCreate,
    CustomerUpdate,
    ProductCreate,
    ProductUpdate,
    SupplierCreate,
    SupplierUpdate,
)
from app.services import catalog
from tests.factories import make_customer, make_product, make_supplier
from tests.integration.catalog.cuits import valid_cuit

D = Decimal


def product_body(**overrides: Any) -> ProductCreate:
    data: dict[str, Any] = {
        "sku": "TOR-001",
        "name": "Tornillo 6mm",
        "unit": "unit",
        "unit_price": "12.50",
        "vat_rate": "21.0",
        "unit_cost": "7.25",
        "min_stock": "100",
    }
    return ProductCreate.model_validate(data | overrides)


def rule_code(exc: pytest.ExceptionInfo[BusinessRuleViolation]) -> str:
    return exc.value.code


# Products ---------------------------------------------------------------------------------


def test_create_product_valid_data_persists_with_zero_stock(db: Session) -> None:
    supplier = make_supplier(db)

    product = catalog.create_product(db, product_body(supplier_id=supplier.id))

    stored = ProductRepository(db).get_or_raise(product.id)
    assert (stored.sku, stored.unit_price, stored.unit_cost) == ("TOR-001", D("12.50"), D("7.25"))
    assert stored.vat_rate is VatRate.VAT_21
    assert stored.stock_qty == 0
    assert stored.supplier_id == supplier.id
    assert stored.is_active


def test_create_product_duplicate_sku_any_case_raises_sku_taken(db: Session) -> None:
    make_product(db, sku="TOR-001")

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_product(db, product_body(sku="tor-001"))

    assert rule_code(exc) == "sku_taken"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("unit_price", "-1", "invalid_amount"),
        ("unit_price", "1.005", "invalid_amount"),
        ("unit_cost", "-0.01", "invalid_amount"),
        ("unit_cost", "3.141", "invalid_amount"),
        ("min_stock", "-1", "invalid_quantity"),
        ("min_stock", "1.0001", "invalid_quantity"),
    ],
)
def test_create_product_invalid_amounts_are_rejected(
    db: Session, field: str, value: str, code: str
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_product(db, product_body(**{field: value}))

    assert rule_code(exc) == code


def test_create_product_unknown_supplier_raises_invalid_supplier(db: Session) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_product(db, product_body(supplier_id=999_999))

    assert rule_code(exc) == "invalid_supplier"


def test_create_product_inactive_supplier_raises_invalid_supplier(db: Session) -> None:
    supplier = make_supplier(db)
    supplier.is_active = False
    db.flush()

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_product(db, product_body(supplier_id=supplier.id))

    assert rule_code(exc) == "invalid_supplier"


def test_create_product_concurrent_duplicate_maps_unique_violation(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_product(db, sku="TOR-001")
    # Simulate a concurrent insert that slipped past the pre-check: the DB constraint decides.
    monkeypatch.setattr(ProductRepository, "sku_taken", lambda *_a, **_k: False)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_product(db, product_body(sku="TOR-001"))

    assert rule_code(exc) == "sku_taken"
    assert ProductRepository(db).search(q="TOR-001", active=None, limit=10, offset=0)[1] == 1


def test_update_product_changes_only_sent_fields(db: Session) -> None:
    supplier = make_supplier(db)
    product = make_product(db, unit_price=D("100.00"), supplier=supplier)

    updated = catalog.update_product(
        db,
        product.id,
        ProductUpdate.model_validate(
            {"unit_price": "110.00", "vat_rate": "10.5", "supplier_id": None, "is_active": False}
        ),
    )

    assert updated.unit_price == D("110.00")
    assert updated.vat_rate is VatRate.VAT_10_5
    assert updated.supplier_id is None
    assert updated.is_active is False
    assert updated.unit_cost == D("60.00")  # untouched


def test_update_product_own_sku_is_not_a_conflict(db: Session) -> None:
    product = make_product(db, sku="ABC-1")

    updated = catalog.update_product(db, product.id, ProductUpdate(sku="abc-1"))

    assert updated.sku == "abc-1"


def test_update_product_sku_of_another_raises_sku_taken(db: Session) -> None:
    make_product(db, sku="ABC-1")
    product = make_product(db, sku="ABC-2")

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.update_product(db, product.id, ProductUpdate(sku="ABC-1"))

    assert rule_code(exc) == "sku_taken"


def test_update_product_negative_cost_raises_invalid_amount(db: Session) -> None:
    product = make_product(db)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.update_product(db, product.id, ProductUpdate(unit_cost=D("-5")))

    assert rule_code(exc) == "invalid_amount"


def test_update_product_inactive_supplier_raises_invalid_supplier(db: Session) -> None:
    product = make_product(db)
    supplier = make_supplier(db)
    supplier.is_active = False
    db.flush()

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.update_product(db, product.id, ProductUpdate(supplier_id=supplier.id))

    assert rule_code(exc) == "invalid_supplier"


def test_update_product_keeping_its_now_inactive_supplier_is_allowed(db: Session) -> None:
    supplier = make_supplier(db)
    product = make_product(db, supplier=supplier)
    supplier.is_active = False
    db.flush()

    updated = catalog.update_product(
        db, product.id, ProductUpdate(supplier_id=supplier.id, name="Renamed")
    )

    assert updated.name == "Renamed"


def test_update_product_concurrent_duplicate_maps_unique_violation(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_product(db, sku="ABC-1")
    product = make_product(db, sku="ABC-2")
    monkeypatch.setattr(ProductRepository, "sku_taken", lambda *_a, **_k: False)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.update_product(db, product.id, ProductUpdate(sku="ABC-1"))

    assert rule_code(exc) == "sku_taken"


def test_get_and_update_missing_product_raise_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        catalog.get_product(db, 999_999)
    with pytest.raises(NotFound):
        catalog.update_product(db, 999_999, ProductUpdate(name="x"))


def test_list_products_filters_by_text_and_active_and_paginates(db: Session) -> None:
    make_product(db, sku="FILTRO-1", name="Filtro de aire")
    make_product(db, sku="FILTRO-2", name="Filtro de aceite", is_active=False)
    make_product(db, sku="BUJIA-1", name="Bujía 100% iridio")

    _, total_text = catalog.list_products(db, q="filtro", active=None, limit=50, offset=0)
    active_items, active_total = catalog.list_products(
        db, q="filtro", active=True, limit=50, offset=0
    )
    page, page_total = catalog.list_products(db, q="FILTRO", active=None, limit=1, offset=1)
    by_name, _ = catalog.list_products(db, q="100%", active=None, limit=50, offset=0)

    assert total_text == 2
    assert [p.sku for p in active_items] == ["FILTRO-1"]
    assert active_total == 1
    assert [p.sku for p in page] == ["FILTRO-2"]
    assert page_total == 2
    assert [p.sku for p in by_name] == ["BUJIA-1"]


def test_list_products_like_wildcards_are_literal(db: Session) -> None:
    make_product(db, sku="A_B-1")
    make_product(db, sku="AXB-1")

    items, _ = catalog.list_products(db, q="A_B", active=None, limit=50, offset=0)

    assert [p.sku for p in items] == ["A_B-1"]


# Customers --------------------------------------------------------------------------------


def customer_body(**overrides: Any) -> CustomerCreate:
    data: dict[str, Any] = {
        "name": "Ferretería El Tornillo SRL",
        "cuit": valid_cuit("33", dashed=True),
        "tax_condition": "responsable_inscripto",
        "address": "Av. Siempre Viva 742",
        "email": "compras@eltornillo.com.ar",
        "phone": "011 4444-5555",
        "payment_terms_days": 30,
    }
    return CustomerCreate.model_validate(data | overrides)


def test_create_customer_dashed_cuit_is_stored_as_digits(db: Session) -> None:
    cuit = valid_cuit("33")
    dashed = f"{cuit[:2]}-{cuit[2:10]}-{cuit[10]}"

    customer = catalog.create_customer(db, customer_body(cuit=dashed))

    assert CustomerRepository(db).get_or_raise(customer.id).cuit == cuit
    assert customer.payment_terms_days == 30


def test_create_customer_final_consumer_without_cuit_is_allowed(db: Session) -> None:
    customer = catalog.create_customer(
        db, customer_body(cuit="  ", tax_condition="consumidor_final")
    )

    assert customer.cuit is None
    assert customer.tax_condition is TaxCondition.CONSUMIDOR_FINAL


def test_create_customer_final_consumer_with_cuit_validates_it(db: Session) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_customer(
            db, customer_body(cuit="20-12345678-0", tax_condition="consumidor_final")
        )

    assert rule_code(exc) == "invalid_cuit"


@pytest.mark.parametrize("condition", ["responsable_inscripto", "monotributo", "exento"])
def test_create_customer_without_cuit_raises_cuit_required(db: Session, condition: str) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_customer(db, customer_body(cuit=None, tax_condition=condition))

    assert rule_code(exc) == "cuit_required"


def test_create_customer_invalid_cuit_raises_invalid_cuit(db: Session) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_customer(db, customer_body(cuit="20-12345678-9"))

    assert rule_code(exc) == "invalid_cuit"


def test_create_customer_duplicate_cuit_raises_cuit_taken(db: Session) -> None:
    cuit = valid_cuit("33")
    make_customer(db, cuit=cuit)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_customer(db, customer_body(cuit=cuit))

    assert rule_code(exc) == "cuit_taken"


def test_create_customer_concurrent_duplicate_maps_unique_violation(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cuit = valid_cuit("33")
    make_customer(db, cuit=cuit)
    monkeypatch.setattr(CustomerRepository, "cuit_taken", lambda *_a, **_k: False)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_customer(db, customer_body(cuit=cuit))

    assert rule_code(exc) == "cuit_taken"


def test_create_customer_negative_payment_terms_raises(db: Session) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_customer(db, customer_body(payment_terms_days=-1))

    assert rule_code(exc) == "invalid_payment_terms"


def test_update_customer_changes_fields_and_normalizes_cuit(db: Session) -> None:
    customer = make_customer(db, tax_condition=TaxCondition.CONSUMIDOR_FINAL)
    new_cuit = valid_cuit("27")

    updated = catalog.update_customer(
        db,
        customer.id,
        CustomerUpdate.model_validate(
            {
                "tax_condition": "monotributo",
                "cuit": f"{new_cuit[:2]}-{new_cuit[2:10]}-{new_cuit[10]}",
                "payment_terms_days": 15,
                "email": None,
            }
        ),
    )

    assert updated.cuit == new_cuit
    assert updated.tax_condition is TaxCondition.MONOTRIBUTO
    assert updated.payment_terms_days == 15
    assert updated.email is None


def test_update_customer_leaving_final_consumer_without_cuit_raises(db: Session) -> None:
    customer = make_customer(db, tax_condition=TaxCondition.CONSUMIDOR_FINAL)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.update_customer(
            db, customer.id, CustomerUpdate(tax_condition=TaxCondition.RESPONSABLE_INSCRIPTO)
        )

    assert rule_code(exc) == "cuit_required"


def test_update_customer_removing_cuit_of_registered_customer_raises(db: Session) -> None:
    customer = make_customer(db)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.update_customer(db, customer.id, CustomerUpdate.model_validate({"cuit": None}))

    assert rule_code(exc) == "cuit_required"


def test_update_customer_cuit_of_another_raises_cuit_taken(db: Session) -> None:
    cuit = valid_cuit("27")
    make_customer(db, cuit=cuit)
    customer = make_customer(db)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.update_customer(db, customer.id, CustomerUpdate(cuit=cuit))

    assert rule_code(exc) == "cuit_taken"


def test_update_customer_invalid_payment_terms_raises(db: Session) -> None:
    customer = make_customer(db)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.update_customer(db, customer.id, CustomerUpdate(payment_terms_days=400))

    assert rule_code(exc) == "invalid_payment_terms"


def test_get_and_update_missing_customer_raise_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        catalog.get_customer(db, 999_999)
    with pytest.raises(NotFound):
        catalog.update_customer(db, 999_999, CustomerUpdate(name="x"))


def test_list_customers_searches_name_and_cuit(db: Session) -> None:
    cuit = valid_cuit("27")
    make_customer(db, name="Zapatería Pérez", cuit=cuit)
    make_customer(db, name="Zapatería Gómez")

    by_name, total = catalog.list_customers(db, q="zapater", active=True, limit=50, offset=0)
    by_cuit, _ = catalog.list_customers(db, q=cuit[2:8], active=None, limit=50, offset=0)

    assert total == 2
    assert [c.name for c in by_name] == ["Zapatería Gómez", "Zapatería Pérez"]
    assert [c.cuit for c in by_cuit] == [cuit]


# Suppliers --------------------------------------------------------------------------------


def supplier_body(**overrides: Any) -> SupplierCreate:
    data: dict[str, Any] = {
        "name": "Bulonera Central SA",
        "cuit": valid_cuit("33", dashed=True),
        "tax_condition": "responsable_inscripto",
        "email": "ventas@bulonera.com.ar",
    }
    return SupplierCreate.model_validate(data | overrides)


def test_create_supplier_stores_digits(db: Session) -> None:
    supplier = catalog.create_supplier(db, supplier_body())

    stored = SupplierRepository(db).get_or_raise(supplier.id)
    assert stored.cuit.isdigit() and len(stored.cuit) == 11
    assert stored.phone is None


def test_create_supplier_invalid_cuit_raises(db: Session) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_supplier(db, supplier_body(cuit=""))

    assert rule_code(exc) == "invalid_cuit"


def test_create_supplier_duplicate_cuit_raises_cuit_taken(db: Session) -> None:
    cuit = valid_cuit("33")
    catalog.create_supplier(db, supplier_body(cuit=cuit))

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_supplier(db, supplier_body(cuit=cuit, name="Otro"))

    assert rule_code(exc) == "cuit_taken"


def test_create_supplier_concurrent_duplicate_maps_unique_violation(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cuit = valid_cuit("33")
    catalog.create_supplier(db, supplier_body(cuit=cuit))
    monkeypatch.setattr(SupplierRepository, "cuit_taken", lambda *_a, **_k: False)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.create_supplier(db, supplier_body(cuit=cuit))

    assert rule_code(exc) == "cuit_taken"


def test_update_supplier_changes_fields(db: Session) -> None:
    supplier = make_supplier(db)
    cuit = valid_cuit("23")

    updated = catalog.update_supplier(
        db,
        supplier.id,
        SupplierUpdate.model_validate(
            {"name": "Nuevo nombre", "cuit": cuit, "is_active": False, "phone": "123"}
        ),
    )

    assert (updated.name, updated.cuit, updated.is_active, updated.phone) == (
        "Nuevo nombre",
        cuit,
        False,
        "123",
    )


def test_update_supplier_cuit_of_another_raises_cuit_taken(db: Session) -> None:
    cuit = valid_cuit("23")
    catalog.create_supplier(db, supplier_body(cuit=cuit))
    supplier = make_supplier(db)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.update_supplier(db, supplier.id, SupplierUpdate(cuit=cuit))

    assert rule_code(exc) == "cuit_taken"


def test_update_supplier_invalid_cuit_raises(db: Session) -> None:
    supplier = make_supplier(db)

    with pytest.raises(BusinessRuleViolation) as exc:
        catalog.update_supplier(db, supplier.id, SupplierUpdate(cuit="123"))

    assert rule_code(exc) == "invalid_cuit"


def test_get_and_update_missing_supplier_raise_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        catalog.get_supplier(db, 999_999)
    with pytest.raises(NotFound):
        catalog.update_supplier(db, 999_999, SupplierUpdate(name="x"))


def test_list_suppliers_and_active_suppliers(db: Session) -> None:
    active = make_supplier(db, name="Aaa Proveedor")
    inactive = make_supplier(db, name="Aab Proveedor")
    inactive.is_active = False
    db.flush()

    items, total = catalog.list_suppliers(db, q="Aa", active=None, limit=50, offset=0)
    active_only = catalog.list_active_suppliers(db)

    assert total == 2
    assert [s.id for s in items] == [active.id, inactive.id]
    assert active.id in {s.id for s in active_only}
    assert inactive.id not in {s.id for s in active_only}
