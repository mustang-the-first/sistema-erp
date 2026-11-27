from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.errors import BusinessRuleViolation
from app.schemas.catalog import CustomerUpdate, ProductCreate, ProductUpdate, SupplierUpdate
from app.services.catalog import (
    check_money,
    check_payment_terms,
    check_quantity,
    cuit_check_digit,
    normalize_cuit,
)

D = Decimal


# CUIT -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20123456786", "20123456786"),
        ("20-12345678-6", "20123456786"),
        (" 20 12345678 6 ", "20123456786"),
        ("30-71234567-1", "30712345671"),
    ],
)
def test_normalize_cuit_valid_input_returns_digits(raw: str, expected: str) -> None:
    assert normalize_cuit(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "2012345678", "201234567861", "20-1234567A-6", "20.12345678.6"]
)
def test_normalize_cuit_malformed_raises_invalid_cuit(raw: str) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        normalize_cuit(raw)

    assert exc.value.code == "invalid_cuit"


def test_normalize_cuit_wrong_check_digit_raises_invalid_cuit() -> None:
    with pytest.raises(BusinessRuleViolation, match="check digit") as exc:
        normalize_cuit("20-12345678-7")

    assert exc.value.code == "invalid_cuit"


def test_cuit_check_digit_regular_case_is_eleven_minus_remainder() -> None:
    # 2*5 + 0*4 + 1*3 + 2*2 + 3*7 + 4*6 + 5*5 + 6*4 + 7*3 + 8*2 = 148; 148 % 11 = 5 -> 6
    assert cuit_check_digit("2012345678") == 6


def test_cuit_check_digit_remainder_zero_gives_zero() -> None:
    assert cuit_check_digit("0000000000") == 0


def test_cuit_check_digit_ten_means_no_valid_cuit() -> None:
    # 9*5 = 45; 45 % 11 = 1 -> 11 - 1 = 10: no check digit can make this CUIT valid
    assert cuit_check_digit("9000000000") is None


# Amounts ----------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [D("0"), D("0.01"), D("1500.50"), D("999999999999.99")])
def test_check_money_valid_amount_passes(value: Decimal) -> None:
    check_money(value, "unit_price")


@pytest.mark.parametrize("value", [D("-0.01"), D("1.001"), D("1e12"), D("NaN"), D("Infinity")])
def test_check_money_invalid_amount_raises(value: Decimal) -> None:
    with pytest.raises(BusinessRuleViolation, match="unit_price") as exc:
        check_money(value, "unit_price")

    assert exc.value.code == "invalid_amount"


@pytest.mark.parametrize("value", [D("0"), D("2.5"), D("10.125")])
def test_check_quantity_valid_passes(value: Decimal) -> None:
    check_quantity(value, "min_stock")


@pytest.mark.parametrize("value", [D("-1"), D("0.0001"), D("1e9")])
def test_check_quantity_invalid_raises(value: Decimal) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        check_quantity(value, "min_stock")

    assert exc.value.code == "invalid_quantity"


@pytest.mark.parametrize("days", [-1, 366])
def test_check_payment_terms_out_of_range_raises(days: int) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        check_payment_terms(days)

    assert exc.value.code == "invalid_payment_terms"


# Schemas ----------------------------------------------------------------------------------


def test_product_update_null_on_required_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="name cannot be null"):
        ProductUpdate.model_validate({"name": None})


def test_product_update_null_supplier_is_allowed_and_tracked() -> None:
    update = ProductUpdate.model_validate({"supplier_id": None})

    assert update.changes() == {"supplier_id": None}


def test_product_update_stock_qty_is_not_writable() -> None:
    with pytest.raises(ValidationError, match="stock_qty"):
        ProductUpdate.model_validate({"stock_qty": "10"})


def test_product_create_parses_decimal_strings() -> None:
    product = ProductCreate.model_validate(
        {
            "sku": " A-1 ",
            "name": "Tornillo",
            "unit_price": "10.50",
            "vat_rate": "10.5",
            "unit_cost": "6",
        }
    )

    assert product.sku == "A-1"
    assert product.unit_price == D("10.50")
    assert product.min_stock == D("0")


def test_customer_update_null_cuit_allowed_null_name_rejected() -> None:
    assert CustomerUpdate.model_validate({"cuit": None}).changes() == {"cuit": None}
    with pytest.raises(ValidationError):
        CustomerUpdate.model_validate({"name": None})


def test_supplier_update_null_cuit_rejected() -> None:
    with pytest.raises(ValidationError, match="cuit cannot be null"):
        SupplierUpdate.model_validate({"cuit": None})
