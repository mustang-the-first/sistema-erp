"""Pure accounting rules: code ordering and validation, tree building, running balance."""

import datetime as dt
from decimal import Decimal

import pytest

from app.core.errors import BusinessRuleViolation
from app.models.accounting import Account
from app.models.enums import AccountType, JournalSourceType
from app.repositories.accounting import LedgerRow
from app.services.accounting import (
    balance_nature,
    build_account_tree,
    code_sort_key,
    validate_child_code,
    validate_date_range,
    with_running_balance,
)

D = Decimal


def test_code_sort_key_orders_segments_numerically() -> None:
    codes = ["1.1.10", "1.1.2", "1", "1.1", "2", "1.1.2.01"]

    assert sorted(codes, key=code_sort_key) == ["1", "1.1", "1.1.2", "1.1.2.01", "1.1.10", "2"]


@pytest.mark.parametrize(
    ("parent", "code"), [("1.1.1", "1.1.1.03"), ("3.1", "3.1.02"), ("4", "4.3")]
)
def test_validate_child_code_with_parent_prefix_and_one_segment_passes(
    parent: str, code: str
) -> None:
    validate_child_code(parent, code)


@pytest.mark.parametrize(
    "code",
    [
        "1.1.2.01",
        "1.1.1",
        "1.1.1.",
        "1.1.1.03.01",
        "1.1.10",
        "1.1.1.0a",
        "x1.1.1.01",
        "1.1.1.12345",
    ],
)
def test_validate_child_code_without_parent_prefix_is_rejected(code: str) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        validate_child_code("1.1.1", code)

    assert exc.value.code == "invalid_code"


def test_validate_date_range_with_from_after_to_is_rejected() -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        validate_date_range(dt.date(2026, 11, 2), dt.date(2026, 11, 1))

    assert exc.value.code == "invalid_date_range"


NOV_1 = dt.date(2026, 11, 1)


@pytest.mark.parametrize(
    ("date_from", "date_to"), [(None, None), (NOV_1, None), (None, NOV_1), (NOV_1, NOV_1)]
)
def test_validate_date_range_with_open_or_equal_bounds_passes(
    date_from: dt.date | None, date_to: dt.date | None
) -> None:
    validate_date_range(date_from, date_to)


@pytest.mark.parametrize(
    ("balance", "nature"),
    [(D("0.01"), "deudor"), (D("-0.01"), "acreedor"), (D("0.00"), None)],
)
def test_balance_nature_follows_sign_of_debit_minus_credit(
    balance: Decimal, nature: str | None
) -> None:
    assert balance_nature(balance) == nature


def _account(id_: int, code: str, parent_id: int | None) -> Account:
    return Account(
        id=id_, code=code, name=code, type=AccountType.ASSET, parent_id=parent_id, is_postable=True
    )


def test_build_account_tree_nests_by_parent_and_orders_siblings_by_code() -> None:
    accounts = [
        _account(4, "1.1.10", 2),
        _account(2, "1.1", 1),
        _account(3, "1.1.2", 2),
        _account(1, "1", None),
        _account(5, "2", None),
    ]

    tree = build_account_tree(accounts)

    assert [n.account.code for n in tree] == ["1", "2"]
    (current,) = tree[0].children
    assert current.account.code == "1.1"
    assert [n.account.code for n in current.children] == ["1.1.2", "1.1.10"]
    assert tree[1].children == []


def test_build_account_tree_with_missing_parent_makes_orphan_a_root() -> None:
    tree = build_account_tree([_account(7, "1.1.2", 99)])

    assert [n.account.code for n in tree] == ["1.1.2"]


def _row(number: int, debit: str, credit: str) -> LedgerRow:
    return LedgerRow(
        entry_id=number,
        entry_number=number,
        date=dt.date(2026, 11, number),
        description=f"Entry {number}",
        source_type=JournalSourceType.INVOICE,
        source_id=number,
        account_id=1,
        account_code="1.1.3.01",
        account_name="Deudores por ventas",
        debit=D(debit),
        credit=D(credit),
    )


def test_with_running_balance_accumulates_from_opening_balance() -> None:
    rows = [_row(1, "100.00", "0"), _row(2, "0", "250.50"), _row(3, "40.25", "0")]

    movements = with_running_balance(D("1000.00"), rows)

    assert [m.balance for m in movements] == [D("1100.00"), D("849.50"), D("889.75")]
    assert [m.entry_number for m in movements] == [1, 2, 3]


def test_with_running_balance_without_rows_is_empty() -> None:
    assert with_running_balance(D("5.00"), []) == []
