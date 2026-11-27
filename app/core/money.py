"""Money and quantity helpers. Always Decimal, never float."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
MILLI = Decimal("0.001")
ZERO = Decimal("0.00")


def round_money(value: Decimal) -> Decimal:
    """Round to cents, half up (the rounding used on every invoice line)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def round_quantity(value: Decimal) -> Decimal:
    return value.quantize(MILLI, rounding=ROUND_HALF_UP)


def has_at_most_two_decimals(value: Decimal) -> bool:
    return value == value.quantize(CENT)


@dataclass(frozen=True, slots=True)
class LineAmounts:
    net: Decimal
    vat: Decimal

    @property
    def total(self) -> Decimal:
        return self.net + self.vat


def compute_line_amounts(quantity: Decimal, unit_price: Decimal, vat_rate: Decimal) -> LineAmounts:
    """Net and VAT of one document line.

    ``vat_rate`` is a fraction (``Decimal("0.21")``). Net is rounded first and VAT is computed on
    the rounded net, so line totals always add up to the document totals.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if unit_price < 0:
        raise ValueError("unit_price must not be negative")
    net = round_money(quantity * unit_price)
    vat = round_money(net * vat_rate)
    return LineAmounts(net=net, vat=vat)


def format_ars(value: Decimal) -> str:
    """Argentine format: 1.762,50"""
    text = f"{round_money(value):,.2f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")
