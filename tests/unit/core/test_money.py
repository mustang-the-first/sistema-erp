from decimal import Decimal

import pytest

from app.core.money import (
    compute_line_amounts,
    format_ars,
    has_at_most_two_decimals,
    round_money,
    round_quantity,
)

D = Decimal


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (D("0.005"), D("0.01")),
        (D("2.675"), D("2.68")),
        (D("-0.005"), D("-0.01")),
        (D("1"), D("1.00")),
    ],
)
def test_round_money_rounds_half_up_to_cents(value: Decimal, expected: Decimal) -> None:
    assert round_money(value) == expected


def test_round_quantity_keeps_three_decimals() -> None:
    assert round_quantity(D("1.2345")) == D("1.235")


def test_has_at_most_two_decimals() -> None:
    assert has_at_most_two_decimals(D("10.50"))
    assert not has_at_most_two_decimals(D("10.505"))


@pytest.mark.parametrize(
    ("quantity", "price", "rate", "net", "vat"),
    [
        (D("10"), D("100.00"), D("0.21"), D("1000.00"), D("210.00")),
        (D("5"), D("100.00"), D("0.105"), D("500.00"), D("52.50")),
        (D("3"), D("33.33"), D("0.21"), D("99.99"), D("21.00")),  # 20.9979 -> 21.00
        (D("1.5"), D("0.99"), D("0.105"), D("1.49"), D("0.16")),  # 1.485 -> 1.49; 0.15645 -> 0.16
    ],
)
def test_compute_line_amounts_rounds_net_then_vat(
    quantity: Decimal, price: Decimal, rate: Decimal, net: Decimal, vat: Decimal
) -> None:
    amounts = compute_line_amounts(quantity, price, rate)

    assert (amounts.net, amounts.vat) == (net, vat)
    assert amounts.total == net + vat


def test_compute_line_amounts_rejects_non_positive_quantity() -> None:
    with pytest.raises(ValueError, match="quantity"):
        compute_line_amounts(D("0"), D("1"), D("0.21"))


def test_compute_line_amounts_rejects_negative_price() -> None:
    with pytest.raises(ValueError, match="unit_price"):
        compute_line_amounts(D("1"), D("-1"), D("0.21"))


@pytest.mark.parametrize(
    ("value", "text"),
    [(D("1762.5"), "1.762,50"), (D("1234567.891"), "1.234.567,89"), (D("0"), "0,00")],
)
def test_format_ars_uses_argentine_separators(value: Decimal, text: str) -> None:
    assert format_ars(value) == text
