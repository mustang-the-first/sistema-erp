import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.errors import BusinessRuleViolation
from app.models.collections import Receipt
from app.models.enums import InvoiceLetter, PaymentMethod
from app.models.sales import Invoice
from app.schemas.collections import parse_receipt_form
from app.services.collections import (
    AllocationRequest,
    MovementKind,
    build_movements,
    check_allocation_against_invoice,
    days_overdue,
    format_document_number,
    format_invoice_number,
    validate_allocation_requests,
)

D = Decimal
DAY = dt.date(2026, 11, 10)


def invoice(
    invoice_id: int = 1, *, customer_id: int = 7, day: dt.date = DAY, total: str = "121.00"
) -> Invoice:
    return Invoice(
        id=invoice_id,
        letter=InvoiceLetter.A,
        point_of_sale=1,
        number=invoice_id,
        customer_id=customer_id,
        date=day,
        due_date=day + dt.timedelta(days=30),
        total=D(total),
    )


def receipt(receipt_id: int, day: dt.date, amount: str) -> Receipt:
    return Receipt(
        id=receipt_id,
        point_of_sale=1,
        number=receipt_id,
        customer_id=7,
        date=day,
        method=PaymentMethod.CASH,
        amount=D(amount),
    )


# Allocation requests ------------------------------------------------------------------------


def test_validate_allocations_valid_list_passes() -> None:
    validate_allocation_requests([AllocationRequest(1, D("10.50")), AllocationRequest(2, D("1"))])


@pytest.mark.parametrize(
    ("allocations", "code"),
    [
        ([], "no_allocations"),
        ([AllocationRequest(1, D("1")), AllocationRequest(1, D("2"))], "duplicate_invoice"),
        ([AllocationRequest(1, D("0"))], "invalid_amount"),
        ([AllocationRequest(1, D("-5.00"))], "invalid_amount"),
        ([AllocationRequest(1, D("1.001"))], "invalid_amount"),
        ([AllocationRequest(1, D("NaN"))], "invalid_amount"),
    ],
)
def test_validate_allocations_invalid_input_is_rejected(
    allocations: list[AllocationRequest], code: str
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc_info:
        validate_allocation_requests(allocations)

    assert exc_info.value.code == code


def test_check_allocation_within_balance_passes() -> None:
    check_allocation_against_invoice(
        AllocationRequest(1, D("121.00")),
        invoice(),
        customer_id=7,
        receipt_date=DAY,
        balance=D("121.00"),
    )


@pytest.mark.parametrize(
    ("customer_id", "receipt_date", "amount", "code"),
    [
        (8, DAY, "10.00", "invoice_not_owned"),
        (7, DAY - dt.timedelta(days=1), "10.00", "invoice_after_receipt"),
        (7, DAY, "121.01", "overpayment"),
    ],
)
def test_check_allocation_against_invoice_violation_is_rejected(
    customer_id: int, receipt_date: dt.date, amount: str, code: str
) -> None:
    with pytest.raises(BusinessRuleViolation) as exc_info:
        check_allocation_against_invoice(
            AllocationRequest(1, D(amount)),
            invoice(),
            customer_id=customer_id,
            receipt_date=receipt_date,
            balance=D("121.00"),
        )

    assert exc_info.value.code == code


# Formatting ---------------------------------------------------------------------------------


def test_document_numbers_are_zero_padded() -> None:
    assert format_document_number(1, 12) == "0001-00000012"
    assert format_invoice_number(invoice(3)) == "A 0001-00000003"


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [(dt.date(2026, 12, 9), 0), (dt.date(2026, 12, 10), 0), (dt.date(2026, 12, 15), 5)],
)
def test_days_overdue_counts_days_after_due_date(as_of: dt.date, expected: int) -> None:
    assert days_overdue(dt.date(2026, 12, 10), as_of) == expected


# Statement movements ------------------------------------------------------------------------


def test_build_movements_orders_chronologically_with_running_balance() -> None:
    later = DAY + dt.timedelta(days=5)
    movements = build_movements(
        D("100.00"),
        [invoice(2, day=later, total="50.00"), invoice(1, day=DAY, total="200.00")],
        [receipt(9, later, "120.00"), receipt(8, DAY, "30.00")],
    )

    assert [(m.kind, m.document_id, m.debit, m.credit, m.balance) for m in movements] == [
        (MovementKind.INVOICE, 1, D("200.00"), D("0"), D("300.00")),
        (MovementKind.RECEIPT, 8, D("0"), D("30.00"), D("270.00")),
        (MovementKind.INVOICE, 2, D("50.00"), D("0"), D("320.00")),
        (MovementKind.RECEIPT, 9, D("0"), D("120.00"), D("200.00")),
    ]
    assert movements[0].document_number == "Factura A 0001-00000001"
    assert movements[1].document_number == "Cobranza 0001-00000008"


def test_build_movements_without_documents_is_empty() -> None:
    assert build_movements(D("10.00"), [], []) == []


# HTML form parsing --------------------------------------------------------------------------


def test_parse_receipt_form_skips_blank_amounts() -> None:
    body = parse_receipt_form(
        {
            "customer_id": "7",
            "date": "2026-11-10",
            "method": "bank_transfer",
            "reference": "",
            "amount_3": " 50.25 ",
            "amount_4": "",
        }
    )

    assert body.customer_id == 7
    assert body.date == DAY
    assert body.method is PaymentMethod.BANK_TRANSFER
    assert body.reference is None
    assert [(a.invoice_id, a.amount) for a in body.allocations] == [(3, D("50.25"))]


def test_parse_receipt_form_without_date_leaves_default() -> None:
    body = parse_receipt_form({"customer_id": "7", "date": "", "method": "cash"})

    assert body.date is None
    assert body.allocations == []


def test_parse_receipt_form_garbage_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        parse_receipt_form({"customer_id": "x", "method": "cheque", "amount_1": "abc"})
