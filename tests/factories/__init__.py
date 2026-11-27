"""Test data builders. They insert rows directly (no business services) except where noted:

- ``make_product(stock=...)`` loads stock through ``stock_ledger`` (initial_load), so
  ``stock_qty`` always equals the sum of movements.
- Document numbers come from ``sequences.next_number``, so factories and services never collide.
- Sales factories do NOT move stock or post journal entries: they only build documents.
"""

from tests.factories.catalog import make_customer, make_product, make_supplier
from tests.factories.collections import make_receipt
from tests.factories.sales import make_delivery_note, make_invoice, make_order
from tests.factories.users import make_user

__all__ = [
    "make_customer",
    "make_delivery_note",
    "make_invoice",
    "make_order",
    "make_product",
    "make_receipt",
    "make_supplier",
    "make_user",
]
