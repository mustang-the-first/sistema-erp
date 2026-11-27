import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, NotFound
from app.models.enums import InvoiceLetter, TaxCondition, VatRate
from app.models.sales import Invoice
from app.models.users import User
from app.services import invoices as service
from app.services.invoices import LEY_27618_LEGEND, InvoiceStatus
from tests.factories import (
    make_customer,
    make_delivery_note,
    make_order,
    make_product,
    make_receipt,
    make_user,
)
from tests.integration.invoices.helpers import (
    NOTE_DATE,
    delivered_note,
    entry_lines_by_code,
    mixed_vat_lines,
)

D = Decimal
T = TaxCondition
INVOICE_DATE = dt.date(2026, 11, 5)


@pytest.fixture
def seller(db: Session) -> User:
    return make_user(db)


def issue(db: Session, note_id: int, seller: User) -> Invoice:
    return service.issue_invoice(
        db, note_id, created_by=seller.id, invoice_date=INVOICE_DATE
    ).invoice


# Letter and snapshot ----------------------------------------------------------------------


def test_issue_invoice_to_responsable_inscripto_is_letter_a_without_legend(
    db: Session, seller: User
) -> None:
    customer = make_customer(db, name="ACME SA", tax_condition=T.RESPONSABLE_INSCRIPTO)
    note = delivered_note(db, customer=customer)

    view = service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE)

    inv = view.invoice
    assert inv.letter is InvoiceLetter.A
    assert (inv.customer_id, inv.delivery_note_id, inv.created_by) == (
        customer.id,
        note.id,
        seller.id,
    )
    assert (inv.customer_name, inv.customer_cuit, inv.customer_tax_condition) == (
        "ACME SA",
        customer.cuit,
        T.RESPONSABLE_INSCRIPTO,
    )
    assert view.legend is None


def test_issue_invoice_to_monotributo_is_letter_a_with_legend(db: Session, seller: User) -> None:
    note = delivered_note(db, tax_condition=T.MONOTRIBUTO)

    view = service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE)

    assert view.invoice.letter is InvoiceLetter.A
    assert view.invoice.customer_tax_condition is T.MONOTRIBUTO
    assert view.legend == LEY_27618_LEGEND


@pytest.mark.parametrize("tax_condition", [T.CONSUMIDOR_FINAL, T.EXENTO])
def test_issue_invoice_to_cf_or_exento_is_letter_b(
    db: Session, seller: User, tax_condition: TaxCondition
) -> None:
    note = delivered_note(db, tax_condition=tax_condition)

    view = service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE)

    assert view.invoice.letter is InvoiceLetter.B
    assert view.legend is None


def test_customer_snapshot_does_not_follow_later_customer_changes(
    db: Session, seller: User
) -> None:
    customer = make_customer(db, name="Old name")
    note = delivered_note(db, customer=customer)
    view = service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE)

    customer.name = "New name"
    db.flush()

    assert service.get_invoice(db, view.invoice.id).invoice.customer_name == "Old name"


# Amounts, dates and numbering ------------------------------------------------------------


def test_issue_invoice_with_mixed_vat_totals_match_plan_example(db: Session, seller: User) -> None:
    note = delivered_note(db, lines=mixed_vat_lines(db))

    inv = issue(db, note.id, seller)

    assert (inv.net_total, inv.vat_21, inv.vat_10_5, inv.total) == (
        D("1500.00"),
        D("210.00"),
        D("52.50"),
        D("1762.50"),
    )
    assert [
        (line.description, line.quantity, line.unit_price, line.net_amount, line.vat_amount)
        for line in inv.lines
    ] == [
        ("Tornillo", D("10"), D("100.00"), D("1000.00"), D("210.00")),
        ("Harina", D("10"), D("50.00"), D("500.00"), D("52.50")),
    ]


def test_issue_invoice_uses_order_snapshot_price_not_current_product_price(
    db: Session, seller: User
) -> None:
    product = make_product(db, unit_price=D("100.00"), vat_rate=VatRate.VAT_21)
    note = delivered_note(db, lines=[(product, D("2"))])
    product.unit_price = D("999.00")
    product.vat_rate = VatRate.VAT_10_5
    db.flush()

    inv = issue(db, note.id, seller)

    assert (inv.lines[0].unit_price, inv.lines[0].vat_rate) == (D("100.00"), VatRate.VAT_21)
    assert inv.total == D("242.00")


def test_issue_invoice_invoices_delivered_quantity_only(db: Session, seller: User) -> None:
    product = make_product(db, unit_price=D("10.00"))
    order = make_order(db, lines=[(product, D("10"))], order_date=NOTE_DATE)
    note = make_delivery_note(db, order=order, quantities={order.lines[0].id: D("3")})

    inv = issue(db, note.id, seller)

    assert (inv.lines[0].quantity, inv.net_total, inv.total) == (D("3"), D("30.00"), D("36.30"))


def test_issue_invoice_due_date_adds_payment_terms(db: Session, seller: User) -> None:
    customer = make_customer(db, payment_terms_days=30)
    note = delivered_note(db, customer=customer)

    inv = issue(db, note.id, seller)

    assert (inv.date, inv.due_date) == (INVOICE_DATE, dt.date(2026, 12, 5))


def test_issue_invoice_without_date_uses_today(
    db: Session, seller: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "today", lambda: dt.date(2026, 11, 20))
    note = delivered_note(db)

    inv = service.issue_invoice(db, note.id, created_by=seller.id).invoice

    assert inv.date == dt.date(2026, 11, 20)


def test_issue_invoice_on_delivery_note_date_is_accepted(db: Session, seller: User) -> None:
    note = delivered_note(db)

    inv = service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=NOTE_DATE).invoice

    assert inv.date == NOTE_DATE


def test_issue_invoice_before_delivery_note_date_is_rejected(db: Session, seller: User) -> None:
    note = delivered_note(db)

    with pytest.raises(BusinessRuleViolation) as exc:
        service.issue_invoice(
            db, note.id, created_by=seller.id, invoice_date=NOTE_DATE - dt.timedelta(days=1)
        )

    assert exc.value.code == "invoice_date_before_delivery"


def test_numbering_is_separate_per_letter(db: Session, seller: User) -> None:
    def issue(tax_condition: TaxCondition) -> tuple[InvoiceLetter, int, str]:
        note = delivered_note(db, tax_condition=tax_condition)
        view = service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE)
        return view.invoice.letter, view.invoice.number, view.formatted_number

    a1 = issue(T.RESPONSABLE_INSCRIPTO)
    b1 = issue(T.CONSUMIDOR_FINAL)
    a2 = issue(T.MONOTRIBUTO)
    b2 = issue(T.EXENTO)

    assert a1[:2] == (InvoiceLetter.A, 1)
    assert b1[:2] == (InvoiceLetter.B, 1)
    assert a2[:2] == (InvoiceLetter.A, 2)
    assert b2[:2] == (InvoiceLetter.B, 2)
    assert (a1[2], a2[2]) == ("0001-00000001", "0001-00000002")


def test_issue_invoice_twice_for_same_delivery_note_is_rejected(db: Session, seller: User) -> None:
    note = delivered_note(db)
    service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE)

    with pytest.raises(BusinessRuleViolation) as exc:
        service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE)

    assert exc.value.code == "already_invoiced"


def test_issue_invoice_for_missing_delivery_note_is_not_found(db: Session, seller: User) -> None:
    with pytest.raises(NotFound):
        service.issue_invoice(db, 999_999, created_by=seller.id, invoice_date=INVOICE_DATE)


def test_issue_invoice_with_zero_total_is_rejected(db: Session, seller: User) -> None:
    free = make_product(db, unit_price=D("0.00"))
    note = delivered_note(db, lines=[(free, D("1"))])

    with pytest.raises(BusinessRuleViolation) as exc:
        service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE)

    assert exc.value.code == "invoice_total_zero"


# Journal entry ----------------------------------------------------------------------------


def test_issue_invoice_posts_balanced_entry_with_concrete_amounts(
    db: Session, seller: User
) -> None:
    note = delivered_note(db, lines=mixed_vat_lines(db))

    view = service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE)

    entry, lines = entry_lines_by_code(db, view.invoice.id)
    assert lines == {
        "1.1.3.01": (D("1762.50"), D("0.00")),  # Deudores por ventas
        "4.1.01": (D("0.00"), D("1500.00")),  # Ventas
        "2.1.3.01": (D("0.00"), D("210.00")),  # IVA DF 21%
        "2.1.3.02": (D("0.00"), D("52.50")),  # IVA DF 10,5%
    }
    assert sum(d for d, _ in lines.values()) == sum(c for _, c in lines.values())
    assert entry.date == INVOICE_DATE
    assert entry.description == "Factura A 0001-00000001"
    assert entry.created_by == seller.id


def test_issue_invoice_with_single_rate_omits_zero_vat_line(db: Session, seller: User) -> None:
    note = delivered_note(db, tax_condition=T.CONSUMIDOR_FINAL)  # 1 x 100 @ 21%

    view = service.issue_invoice(db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE)

    entry, lines = entry_lines_by_code(db, view.invoice.id)
    assert lines == {
        "1.1.3.01": (D("121.00"), D("0.00")),
        "4.1.01": (D("0.00"), D("100.00")),
        "2.1.3.01": (D("0.00"), D("21.00")),
    }
    assert entry.description == "Factura B 0001-00000001"


# Balance, status and listing --------------------------------------------------------------


def test_invoice_status_goes_from_open_to_paid_with_receipts(db: Session, seller: User) -> None:
    customer = make_customer(db)
    note = delivered_note(db, customer=customer)  # total 121.00
    invoice = service.issue_invoice(
        db, note.id, created_by=seller.id, invoice_date=INVOICE_DATE
    ).invoice

    opened = service.get_invoice(db, invoice.id)
    make_receipt(db, customer=customer, allocations=[(invoice, D("21.00"))])
    partial = service.get_invoice(db, invoice.id)
    make_receipt(db, customer=customer, allocations=[(invoice, D("100.00"))])
    paid = service.get_invoice(db, invoice.id)

    assert (opened.balance, opened.status) == (D("121.00"), InvoiceStatus.OPEN)
    assert (partial.balance, partial.status) == (D("100.00"), InvoiceStatus.OPEN)
    assert (paid.balance, paid.status) == (D("0.00"), InvoiceStatus.PAID)


def test_get_invoice_missing_is_not_found(db: Session) -> None:
    with pytest.raises(NotFound):
        service.get_invoice(db, 999_999)


def test_list_invoices_filters_by_status_customer_letter_and_dates(
    db: Session, seller: User
) -> None:
    ri = make_customer(db, tax_condition=T.RESPONSABLE_INSCRIPTO)
    cf = make_customer(db, tax_condition=T.CONSUMIDOR_FINAL)

    def issue(customer: object, day: int) -> int:
        note = delivered_note(db, customer=customer)  # type: ignore[arg-type]
        view = service.issue_invoice(
            db, note.id, created_by=seller.id, invoice_date=dt.date(2026, 11, day)
        )
        return view.invoice.id

    a_paid, a_open, b_open = issue(ri, 3), issue(ri, 10), issue(cf, 20)
    paid_invoice = service.get_invoice(db, a_paid).invoice
    make_receipt(db, customer=ri, allocations=[(paid_invoice, D("121.00"))])

    def ids(**filters: object) -> list[int]:
        return [v.invoice.id for v in service.list_invoices(db, **filters).items]  # type: ignore[arg-type]

    assert ids() == [b_open, a_open, a_paid]  # newest first
    assert ids(status=InvoiceStatus.OPEN) == [b_open, a_open]
    assert ids(status=InvoiceStatus.PAID) == [a_paid]
    assert ids(customer_id=ri.id) == [a_open, a_paid]
    assert ids(letter=InvoiceLetter.B) == [b_open]
    assert ids(date_from=dt.date(2026, 11, 5), date_to=dt.date(2026, 11, 15)) == [a_open]


def test_list_invoices_paginates_and_reports_total(db: Session, seller: User) -> None:
    for _ in range(3):
        service.issue_invoice(
            db, delivered_note(db).id, created_by=seller.id, invoice_date=INVOICE_DATE
        )

    page = service.list_invoices(db, limit=2, offset=0)
    rest = service.list_invoices(db, limit=2, offset=2)

    assert (len(page.items), page.total) == (2, 3)
    assert (len(rest.items), rest.total) == (1, 3)


def test_uninvoiced_delivery_notes_excludes_invoiced_ones(db: Session, seller: User) -> None:
    invoiced, pending = delivered_note(db), delivered_note(db)
    service.issue_invoice(db, invoiced.id, created_by=seller.id, invoice_date=INVOICE_DATE)

    ids = [note.id for note in service.uninvoiced_delivery_notes(db)]

    assert pending.id in ids
    assert invoiced.id not in ids


def test_customer_choices_are_sorted_by_name(db: Session) -> None:
    zeta, alfa = make_customer(db, name="Zeta"), make_customer(db, name="Alfa")

    names = [c.id for c in service.customer_choices(db) if c.id in {zeta.id, alfa.id}]

    assert names == [alfa.id, zeta.id]
