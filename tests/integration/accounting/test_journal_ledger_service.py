"""Journal (libro diario) and ledger (libro mayor) services with concrete amounts."""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, NotFound
from app.models.enums import JournalSourceType, PaymentMethod
from app.services import accounting as service
from tests.integration.accounting.conftest import (
    AccountByCode,
    PostInitialLoad,
    PostInvoice,
    PostReceipt,
)

D = Decimal
OCT_20, NOV_05, NOV_12, NOV_20, DEC_02 = (
    dt.date(2026, 10, 20),
    dt.date(2026, 11, 5),
    dt.date(2026, 11, 12),
    dt.date(2026, 11, 20),
    dt.date(2026, 12, 2),
)


@pytest.fixture
def receivables_history(post_invoice: PostInvoice, post_receipt: PostReceipt) -> None:
    """Deudores por ventas (1.1.3.01). Posted out of date order on purpose: the 12/11 invoice
    gets a higher entry number than the 20/11 receipt.

    20/10 invoice  D 1210.00            (#1)
    05/11 receipt            C  400.00  (#2)
    20/11 receipt            C 1000.00  (#3)
    02/12 invoice  D  121.00            (#4)
    12/11 invoice  D  552.50            (#5)
    """
    post_invoice(OCT_20, "1000.00", vat_21="210.00")
    post_receipt(NOV_05, "400.00")
    post_receipt(NOV_20, "1000.00", method=PaymentMethod.BANK_TRANSFER)
    post_invoice(DEC_02, "100.00", vat_21="21.00")
    post_invoice(NOV_12, "500.00", vat_10_5="52.50")


# Journal ----------------------------------------------------------------------------------


@pytest.mark.usefixtures("receivables_history")
def test_list_journal_orders_by_date_then_number(db: Session) -> None:
    page = service.list_journal(db)

    assert page.total == 5
    assert [(e.date, e.number) for e in page.items] == [
        (OCT_20, 1),
        (NOV_05, 2),
        (NOV_12, 5),
        (NOV_20, 3),
        (DEC_02, 4),
    ]


@pytest.mark.usefixtures("receivables_history")
def test_list_journal_filters_by_date_range_inclusive(db: Session) -> None:
    page = service.list_journal(db, date_from=NOV_05, date_to=NOV_20)

    assert page.total == 3
    assert [e.number for e in page.items] == [2, 5, 3]


@pytest.mark.usefixtures("receivables_history")
def test_list_journal_filters_by_source_type(db: Session) -> None:
    page = service.list_journal(db, source_type=JournalSourceType.RECEIPT)

    assert [e.number for e in page.items] == [2, 3]


@pytest.mark.usefixtures("receivables_history")
def test_list_journal_paginates_with_total_of_all_matches(db: Session) -> None:
    first = service.list_journal(db, limit=2, offset=0)
    second = service.list_journal(db, limit=2, offset=2)
    last = service.list_journal(db, limit=2, offset=4)

    assert (first.total, second.total, last.total) == (5, 5, 5)
    assert [e.number for e in first.items] == [1, 2]
    assert [e.number for e in second.items] == [5, 3]
    assert [e.number for e in last.items] == [4]


def test_list_journal_with_from_after_to_is_rejected(db: Session) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        service.list_journal(db, date_from=NOV_20, date_to=NOV_05)

    assert exc.value.code == "invalid_date_range"


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (201, 0), (10, -1)])
def test_list_journal_with_bad_pagination_is_rejected(db: Session, limit: int, offset: int) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        service.list_journal(db, limit=limit, offset=offset)

    assert exc.value.code == "invalid_pagination"


def test_get_journal_entry_loads_lines_and_balanced_totals(
    db: Session, post_invoice: PostInvoice
) -> None:
    posted = post_invoice(NOV_12, "1500.00", vat_21="210.00", vat_10_5="52.50")
    db.expire_all()

    entry = service.get_journal_entry(db, posted.id)

    assert {line.account.code: (line.debit, line.credit) for line in entry.lines} == {
        "1.1.3.01": (D("1762.50"), D("0.00")),
        "4.1.01": (D("0.00"), D("1500.00")),
        "2.1.3.01": (D("0.00"), D("210.00")),
        "2.1.3.02": (D("0.00"), D("52.50")),
    }
    assert service.entry_totals(entry) == (D("1762.50"), D("1762.50"))


def test_get_journal_entry_unknown_id_raises_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        service.get_journal_entry(db, 999_999)


# Ledger -----------------------------------------------------------------------------------


@pytest.mark.usefixtures("receivables_history")
def test_ledger_with_range_has_opening_running_and_closing_balance(
    db: Session, account_by_code: AccountByCode
) -> None:
    deudores = account_by_code("1.1.3.01")

    ledger = service.get_ledger(
        db, deudores.id, date_from=dt.date(2026, 11, 1), date_to=dt.date(2026, 11, 30)
    )

    assert ledger.opening_balance == D("1210.00")  # only the 20/10 invoice is before from
    assert [(m.date, m.entry_number, m.debit, m.credit, m.balance) for m in ledger.movements] == [
        (NOV_05, 2, D("0.00"), D("400.00"), D("810.00")),
        (NOV_12, 5, D("552.50"), D("0.00"), D("1362.50")),
        (NOV_20, 3, D("0.00"), D("1000.00"), D("362.50")),
    ]
    assert (ledger.total_debit, ledger.total_credit) == (D("552.50"), D("1400.00"))
    assert ledger.closing_balance == D("362.50")
    assert ledger.nature == "deudor"
    assert ledger.includes_descendants is False
    assert ledger.movements[0].description == f"Cobranza {NOV_05}"


@pytest.mark.usefixtures("receivables_history")
def test_ledger_without_dates_starts_at_zero_and_covers_everything(
    db: Session, account_by_code: AccountByCode
) -> None:
    ledger = service.get_ledger(db, account_by_code("1.1.3.01").id)

    assert ledger.opening_balance == D("0.00")
    assert [m.balance for m in ledger.movements] == [
        D("1210.00"),
        D("810.00"),
        D("1362.50"),
        D("362.50"),
        D("483.50"),
    ]
    assert ledger.closing_balance == D("483.50")


@pytest.mark.usefixtures("receivables_history")
def test_ledger_with_only_to_excludes_later_entries(
    db: Session, account_by_code: AccountByCode
) -> None:
    ledger = service.get_ledger(db, account_by_code("1.1.3.01").id, date_to=NOV_12)

    assert [m.entry_number for m in ledger.movements] == [1, 2, 5]
    assert ledger.closing_balance == D("1362.50")


@pytest.mark.usefixtures("receivables_history")
def test_ledger_with_range_without_movements_closes_at_opening(
    db: Session, account_by_code: AccountByCode
) -> None:
    ledger = service.get_ledger(db, account_by_code("1.1.3.01").id, date_from=dt.date(2027, 1, 1))

    assert ledger.movements == []
    assert ledger.opening_balance == ledger.closing_balance == D("483.50")


@pytest.mark.usefixtures("receivables_history")
def test_ledger_of_income_account_has_credit_nature(
    db: Session, account_by_code: AccountByCode
) -> None:
    ledger = service.get_ledger(db, account_by_code("4.1.01").id)

    assert ledger.closing_balance == D("-1600.00")  # 1000 + 100 + 500 of net sales
    assert ledger.nature == "acreedor"


def test_ledger_of_account_without_movements_has_no_nature(
    db: Session, account_by_code: AccountByCode
) -> None:
    ledger = service.get_ledger(db, account_by_code("5.2.01").id)

    assert (ledger.closing_balance, ledger.nature) == (D("0.00"), None)


@pytest.mark.usefixtures("receivables_history")
def test_ledger_of_rubro_aggregates_postable_descendants(
    db: Session, account_by_code: AccountByCode, post_initial_load: PostInitialLoad
) -> None:
    post_initial_load(NOV_05, "800.00")  # Mercaderías (1.1.4.01) is outside 1.1.1

    ledger = service.get_ledger(db, account_by_code("1.1.1").id)  # Caja y bancos

    assert ledger.includes_descendants is True
    assert [(m.account_code, m.debit, m.balance) for m in ledger.movements] == [
        ("1.1.1.01", D("400.00"), D("400.00")),
        ("1.1.1.02", D("1000.00"), D("1400.00")),
    ]
    assert ledger.closing_balance == D("1400.00")


@pytest.mark.usefixtures("receivables_history")
def test_ledger_of_top_rubro_nets_movements_within_subtree(
    db: Session, account_by_code: AccountByCode
) -> None:
    ledger = service.get_ledger(db, account_by_code("1").id)  # Activo

    # Receipts move funds from Deudores to Caja/Banco: the asset total equals the invoices.
    assert ledger.closing_balance == D("1883.50")
    assert len(ledger.movements) == 7


def test_ledger_unknown_account_raises_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        service.get_ledger(db, 999_999)


def test_ledger_with_from_after_to_is_rejected(db: Session, account_by_code: AccountByCode) -> None:
    with pytest.raises(BusinessRuleViolation) as exc:
        service.get_ledger(db, account_by_code("1.1.1.01").id, date_from=NOV_20, date_to=NOV_05)

    assert exc.value.code == "invalid_date_range"
