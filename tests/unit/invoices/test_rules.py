from decimal import Decimal

import pytest

from app.core.errors import BusinessRuleViolation
from app.models.enums import InvoiceLetter, TaxCondition, VatRate
from app.services.invoices import (
    LEY_27618_LEGEND,
    InvoiceStatus,
    LineInput,
    build_lines,
    format_number,
    legend_for,
    resolve_letter,
    status_for,
)

D = Decimal
T = TaxCondition
CUIT = "20123456783"


# Letter -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tax_condition", "cuit", "expected"),
    [
        (T.RESPONSABLE_INSCRIPTO, CUIT, InvoiceLetter.A),
        (T.MONOTRIBUTO, CUIT, InvoiceLetter.A),
        (T.EXENTO, CUIT, InvoiceLetter.B),
        (T.CONSUMIDOR_FINAL, None, InvoiceLetter.B),
        (T.CONSUMIDOR_FINAL, CUIT, InvoiceLetter.B),
    ],
)
def test_resolve_letter_by_tax_condition_returns_expected_letter(
    tax_condition: TaxCondition, cuit: str | None, expected: InvoiceLetter
) -> None:
    assert resolve_letter(tax_condition, cuit) is expected


@pytest.mark.parametrize("tax_condition", [T.RESPONSABLE_INSCRIPTO, T.MONOTRIBUTO])
@pytest.mark.parametrize("cuit", [None, ""])
def test_resolve_letter_a_without_cuit_is_rejected(
    tax_condition: TaxCondition, cuit: str | None
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        resolve_letter(tax_condition, cuit)

    assert exc.value.code == "cuit_required"


# Legend -----------------------------------------------------------------------------------


def test_legend_text_is_the_exact_ley_27618_text() -> None:
    assert LEY_27618_LEGEND == (
        "El crédito fiscal discriminado en el presente comprobante, sólo podrá ser computado a "
        "efectos del Régimen de Sostenimiento e Inclusión Fiscal para Pequeños Contribuyentes "
        "de la Ley Nº 27.618"
    )


def test_legend_for_letter_a_to_monotributo_returns_ley_27618_text() -> None:
    assert legend_for(InvoiceLetter.A, T.MONOTRIBUTO) == LEY_27618_LEGEND


@pytest.mark.parametrize(
    ("letter", "tax_condition"),
    [
        (InvoiceLetter.A, T.RESPONSABLE_INSCRIPTO),
        (InvoiceLetter.B, T.EXENTO),
        (InvoiceLetter.B, T.CONSUMIDOR_FINAL),
        (InvoiceLetter.B, T.MONOTRIBUTO),  # inconsistent snapshot: still no legend on B
    ],
)
def test_legend_for_other_combinations_returns_none(
    letter: InvoiceLetter, tax_condition: TaxCondition
) -> None:
    assert legend_for(letter, tax_condition) is None


# Numbering, status and totals -------------------------------------------------------------


def test_format_number_pads_point_of_sale_and_number() -> None:
    assert format_number(1, 1) == "0001-00000001"
    assert format_number(12, 345678) == "0012-00345678"


@pytest.mark.parametrize(
    ("balance", "expected"),
    [
        (D("0.01"), InvoiceStatus.OPEN),
        (D("0.00"), InvoiceStatus.PAID),
        (D("-1.00"), InvoiceStatus.PAID),
    ],
)
def test_status_for_balance_returns_open_or_paid(balance: Decimal, expected: InvoiceStatus) -> None:
    assert status_for(balance) is expected


def test_build_lines_with_mixed_vat_matches_plan_example() -> None:
    lines, totals = build_lines(
        [
            LineInput(1, "P21", D("10"), D("100.00"), VatRate.VAT_21),
            LineInput(2, "P105", D("5"), D("100.00"), VatRate.VAT_10_5),
        ]
    )

    assert [(line.net_amount, line.vat_amount) for line in lines] == [
        (D("1000.00"), D("210.00")),
        (D("500.00"), D("52.50")),
    ]
    assert (totals.net_total, totals.vat_21, totals.vat_10_5, totals.total) == (
        D("1500.00"),
        D("210.00"),
        D("52.50"),
        D("1762.50"),
    )


def test_build_lines_rounds_vat_per_line_and_totals_are_the_sum() -> None:
    # 0.05 x 10.5% = 0.00525 -> 0.01 per line (ROUND_HALF_UP); two lines -> 0.02, whereas
    # rounding the document VAT (0.0105) would give 0.01.
    _, totals = build_lines(
        [
            LineInput(1, "a", D("1"), D("0.05"), VatRate.VAT_10_5),
            LineInput(2, "b", D("1"), D("0.05"), VatRate.VAT_10_5),
        ]
    )

    assert totals.vat_10_5 == D("0.02")
    assert totals.total == D("0.12")
