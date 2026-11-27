import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.config import get_settings
from app.core.money import ZERO, compute_line_amounts
from app.models.catalog import Customer, Product
from app.models.enums import DocumentType, InvoiceLetter, OrderStatus, TaxCondition, VatRate
from app.models.sales import DeliveryNote, DeliveryNoteLine, Invoice, InvoiceLine, Order, OrderLine
from app.models.sequences import GLOBAL_POINT_OF_SALE
from app.models.users import User
from app.services.sequences import next_number
from tests.factories.catalog import make_customer, make_product
from tests.factories.users import make_user

LineSpec = Sequence[tuple[Product, Decimal]]


def _user(db: Session, user: User | None) -> User:
    return user or make_user(db)


def make_order(
    db: Session,
    *,
    customer: Customer | None = None,
    lines: LineSpec | None = None,
    status: OrderStatus = OrderStatus.CONFIRMED,
    order_date: dt.date | None = None,
    created_by: User | None = None,
) -> Order:
    customer = customer or make_customer(db)
    if lines is None:
        lines = [(make_product(db), Decimal("1"))]
    order = Order(
        number=next_number(db, DocumentType.ORDER, point_of_sale=GLOBAL_POINT_OF_SALE),
        customer_id=customer.id,
        date=order_date or today(),
        status=status,
        created_by=_user(db, created_by).id,
        lines=[
            OrderLine(
                product_id=product.id,
                quantity=quantity,
                unit_price=product.unit_price,
                vat_rate=product.vat_rate,
            )
            for product, quantity in lines
        ],
    )
    db.add(order)
    db.flush()
    return order


def make_delivery_note(
    db: Session,
    *,
    order: Order,
    quantities: Mapping[int, Decimal] | None = None,
    note_date: dt.date | None = None,
    created_by: User | None = None,
) -> DeliveryNote:
    """``quantities`` maps order_line_id -> quantity; default delivers every line in full."""
    point_of_sale = get_settings().point_of_sale
    wanted = quantities or {line.id: line.quantity for line in order.lines}
    by_id = {line.id: line for line in order.lines}
    note = DeliveryNote(
        point_of_sale=point_of_sale,
        number=next_number(db, DocumentType.DELIVERY_NOTE, point_of_sale=point_of_sale),
        order_id=order.id,
        date=note_date or order.date,
        created_by=_user(db, created_by).id,
        lines=[
            DeliveryNoteLine(
                order_line_id=line_id, product_id=by_id[line_id].product_id, quantity=quantity
            )
            for line_id, quantity in wanted.items()
        ],
    )
    db.add(note)
    db.flush()
    return note


def letter_for(tax_condition: TaxCondition) -> InvoiceLetter:
    a_conditions = {TaxCondition.RESPONSABLE_INSCRIPTO, TaxCondition.MONOTRIBUTO}
    return InvoiceLetter.A if tax_condition in a_conditions else InvoiceLetter.B


def make_invoice(
    db: Session,
    *,
    customer: Customer | None = None,
    lines: LineSpec | None = None,
    invoice_date: dt.date | None = None,
    created_by: User | None = None,
) -> Invoice:
    """Builds order -> delivery note -> invoice (documents only: no stock, no entries)."""
    customer = customer or make_customer(db)
    user = _user(db, created_by)
    issued = invoice_date or today()
    order = make_order(db, customer=customer, lines=lines, order_date=issued, created_by=user)
    note = make_delivery_note(db, order=order, note_date=issued, created_by=user)

    letter = letter_for(customer.tax_condition)
    point_of_sale = get_settings().point_of_sale
    invoice_lines: list[InvoiceLine] = []
    vat = {VatRate.VAT_21: ZERO, VatRate.VAT_10_5: ZERO}
    net_total = ZERO
    for line in order.lines:
        amounts = compute_line_amounts(line.quantity, line.unit_price, line.vat_rate.fraction)
        net_total += amounts.net
        vat[line.vat_rate] += amounts.vat
        invoice_lines.append(
            InvoiceLine(
                product_id=line.product_id,
                description=f"Product {line.product_id}",
                quantity=line.quantity,
                unit_price=line.unit_price,
                vat_rate=line.vat_rate,
                net_amount=amounts.net,
                vat_amount=amounts.vat,
            )
        )
    invoice = Invoice(
        letter=letter,
        point_of_sale=point_of_sale,
        number=next_number(db, DocumentType.INVOICE, point_of_sale=point_of_sale, letter=letter),
        customer_id=customer.id,
        delivery_note_id=note.id,
        date=issued,
        due_date=issued + dt.timedelta(days=customer.payment_terms_days),
        customer_name=customer.name,
        customer_cuit=customer.cuit,
        customer_tax_condition=customer.tax_condition,
        net_total=net_total,
        vat_21=vat[VatRate.VAT_21],
        vat_10_5=vat[VatRate.VAT_10_5],
        total=net_total + vat[VatRate.VAT_21] + vat[VatRate.VAT_10_5],
        created_by=user.id,
        lines=invoice_lines,
    )
    db.add(invoice)
    db.flush()
    return invoice
