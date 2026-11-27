"""All ORM models. Importing this package registers every table on ``Base.metadata``."""

from app.models.accounting import Account, JournalEntry, JournalLine, SystemAccount
from app.models.base import Base
from app.models.catalog import Customer, Product, Supplier
from app.models.collections import Receipt, ReceiptAllocation
from app.models.sales import DeliveryNote, DeliveryNoteLine, Invoice, InvoiceLine, Order, OrderLine
from app.models.sequences import DocumentSequence
from app.models.stock import StockMovement
from app.models.users import User, UserSession

__all__ = [
    "Account",
    "Base",
    "Customer",
    "DeliveryNote",
    "DeliveryNoteLine",
    "DocumentSequence",
    "Invoice",
    "InvoiceLine",
    "JournalEntry",
    "JournalLine",
    "Order",
    "OrderLine",
    "Product",
    "Receipt",
    "ReceiptAllocation",
    "StockMovement",
    "Supplier",
    "SystemAccount",
    "User",
    "UserSession",
]
