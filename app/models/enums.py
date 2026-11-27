from decimal import Decimal
from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    SELLER = "seller"
    ACCOUNTANT = "accountant"


class TaxCondition(StrEnum):
    RESPONSABLE_INSCRIPTO = "responsable_inscripto"
    MONOTRIBUTO = "monotributo"
    EXENTO = "exento"
    CONSUMIDOR_FINAL = "consumidor_final"


class VatRate(StrEnum):
    VAT_21 = "21.0"
    VAT_10_5 = "10.5"

    @property
    def fraction(self) -> Decimal:
        return Decimal(self.value) / 100


class StockMovementKind(StrEnum):
    DELIVERY = "delivery"
    ADJUSTMENT = "adjustment"


class AdjustmentReason(StrEnum):
    INITIAL_LOAD = "initial_load"
    COUNT_DIFFERENCE = "count_difference"
    DAMAGE = "damage"


class OrderStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PARTIALLY_DELIVERED = "partially_delivered"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class InvoiceLetter(StrEnum):
    A = "A"
    B = "B"


class DocumentType(StrEnum):
    ORDER = "order"
    DELIVERY_NOTE = "delivery_note"
    INVOICE = "invoice"
    RECEIPT = "receipt"
    JOURNAL_ENTRY = "journal_entry"


class PaymentMethod(StrEnum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"


class AccountType(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


class SystemAccountKey(StrEnum):
    CASH = "CASH"
    BANK = "BANK"
    RECEIVABLES = "RECEIVABLES"
    INVENTORY = "INVENTORY"
    VAT_21 = "VAT_21"
    VAT_10_5 = "VAT_10_5"
    CAPITAL = "CAPITAL"
    SALES = "SALES"
    INVENTORY_GAIN = "INVENTORY_GAIN"
    COGS = "COGS"
    INVENTORY_LOSS = "INVENTORY_LOSS"


class JournalSourceType(StrEnum):
    INVOICE = "invoice"
    DELIVERY_NOTE = "delivery_note"
    RECEIPT = "receipt"
    STOCK_ADJUSTMENT = "stock_adjustment"
