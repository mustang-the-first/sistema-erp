import datetime as dt
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.clock import today
from app.core.config import get_settings
from app.models.catalog import Customer
from app.models.collections import Receipt, ReceiptAllocation
from app.models.enums import DocumentType, PaymentMethod
from app.models.sales import Invoice
from app.models.users import User
from app.services.sequences import next_number
from tests.factories.users import make_user


def make_receipt(
    db: Session,
    *,
    customer: Customer,
    allocations: Sequence[tuple[Invoice, Decimal]],
    receipt_date: dt.date | None = None,
    method: PaymentMethod = PaymentMethod.CASH,
    created_by: User | None = None,
) -> Receipt:
    """Document only: does not validate balances nor post the journal entry."""
    point_of_sale = get_settings().point_of_sale
    receipt = Receipt(
        point_of_sale=point_of_sale,
        number=next_number(db, DocumentType.RECEIPT, point_of_sale=point_of_sale),
        customer_id=customer.id,
        date=receipt_date or today(),
        method=method,
        amount=sum((amount for _, amount in allocations), Decimal("0")),
        created_by=(created_by or make_user(db)).id,
        allocations=[
            ReceiptAllocation(invoice_id=invoice.id, amount=amount)
            for invoice, amount in allocations
        ],
    )
    db.add(receipt)
    db.flush()
    return receipt
