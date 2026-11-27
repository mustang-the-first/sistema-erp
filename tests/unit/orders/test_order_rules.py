from decimal import Decimal

import pytest

from app.core.errors import BusinessRuleViolation
from app.models.enums import OrderStatus, VatRate
from app.services.delivery_notes import format_number
from app.services.orders import compute_totals, status_after_delivery, validate_quantity

D = Decimal


@pytest.mark.parametrize("value", ["1", "0.001", "2.5", "999999999.999"])
def test_validate_quantity_valid_value_is_returned(value: str) -> None:
    assert validate_quantity(D(value)) == D(value)


@pytest.mark.parametrize(
    "value", ["0", "-1", "0.0001", "1.2345", "1000000000", "NaN", "Infinity", "sNaN"]
)
def test_validate_quantity_invalid_value_is_rejected(value: str) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        validate_quantity(D(value))

    assert exc.value.code == "bad_quantity"


def test_compute_totals_mixed_rates_splits_vat_by_rate() -> None:
    totals = compute_totals(
        [
            (D("10"), D("100.00"), VatRate.VAT_21),
            (D("5"), D("100.00"), VatRate.VAT_10_5),
        ]
    )

    assert totals.net_total == D("1500.00")
    assert totals.vat_21 == D("210.00")
    assert totals.vat_10_5 == D("52.50")
    assert totals.total == D("1762.50")


def test_compute_totals_rounds_each_line_half_up() -> None:
    # 0.333 x 10.05 = 3.34665 -> 3.35 net; VAT 21% of 3.35 = 0.7035 -> 0.70 (per line)
    totals = compute_totals([(D("0.333"), D("10.05"), VatRate.VAT_21)] * 2)

    assert totals.net_total == D("6.70")
    assert totals.vat_21 == D("1.40")
    assert totals.vat_10_5 == D("0.00")
    assert totals.total == D("8.10")


def test_compute_totals_no_lines_is_zero() -> None:
    assert compute_totals([]).total == D("0.00")


def test_status_after_delivery_with_pending_is_partially_delivered() -> None:
    assert status_after_delivery([D("0"), D("1.5")]) is OrderStatus.PARTIALLY_DELIVERED


def test_status_after_delivery_nothing_pending_is_delivered() -> None:
    assert status_after_delivery([D("0.000"), D("0")]) is OrderStatus.DELIVERED


def test_format_number_pads_point_of_sale_and_number() -> None:
    assert format_number(1, 42) == "0001-00000042"
