"""Pydantic schemas of the reports module (read-only)."""

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _blank_to_none(value: Any) -> Any:
    """HTML forms send empty strings for cleared inputs; treat them as "not given"."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


OptionalDate = Annotated[dt.date | None, BeforeValidator(_blank_to_none)]


class SalesGroupBy(StrEnum):
    DAY = "day"
    MONTH = "month"
    CUSTOMER = "customer"
    PRODUCT = "product"


class SalesAmounts(BaseModel):
    invoice_count: int | None = Field(
        default=None, description="Number of invoices; null when grouping by product."
    )
    net: Decimal
    vat_21: Decimal
    vat_10_5: Decimal
    total: Decimal


class SalesRow(SalesAmounts):
    key: str = Field(description="day: YYYY-MM-DD · month: YYYY-MM · customer/product: id")
    label: str


class SalesReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date_from: dt.date = Field(alias="from")
    date_to: dt.date = Field(alias="to")
    group_by: SalesGroupBy
    rows: list[SalesRow]
    totals: SalesAmounts


class LowStockItem(BaseModel):
    product_id: int
    sku: str
    name: str
    unit: str
    stock_qty: Decimal
    min_stock: Decimal
    shortfall: Decimal
    supplier_name: str | None


class LowStockReport(BaseModel):
    items: list[LowStockItem]
    total: int


class AgingAmounts(BaseModel):
    balance: Decimal
    days_0_30: Decimal = Field(description="Not yet due, or 0 to 30 days past the due date")
    days_31_60: Decimal
    days_61_90: Decimal
    days_over_90: Decimal


class ReceivablesRow(AgingAmounts):
    customer_id: int
    customer_name: str
    invoice_count: int


class ReceivablesReport(BaseModel):
    as_of: dt.date
    rows: list[ReceivablesRow]
    totals: AgingAmounts
