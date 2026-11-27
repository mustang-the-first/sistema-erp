from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Money, Quantity, fk, str_enum
from app.models.enums import TaxCondition, VatRate

CUIT_FORMAT = "cuit ~ '^[0-9]{11}$'"


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (CheckConstraint(CUIT_FORMAT, name="cuit_format"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    cuit: Mapped[str] = mapped_column(String(11), unique=True)
    tax_condition: Mapped[TaxCondition] = mapped_column(str_enum(TaxCondition, "tax_condition"))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")

    products: Mapped[list["Product"]] = relationship(back_populates="supplier")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_non_negative"),
        CheckConstraint("stock_qty >= 0", name="stock_qty_non_negative"),
        CheckConstraint("min_stock >= 0", name="min_stock_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20), default="unit", server_default="unit")
    unit_price: Mapped[Money]  # net, VAT excluded
    vat_rate: Mapped[VatRate] = mapped_column(str_enum(VatRate, "vat_rate"))
    unit_cost: Mapped[Money]  # standard cost, used to value stock movements
    # Cache of SUM(stock_movements.quantity); only app/services/stock_ledger.py writes it.
    stock_qty: Mapped[Quantity] = mapped_column(default=0, server_default="0")
    min_stock: Mapped[Quantity] = mapped_column(default=0, server_default="0")
    supplier_id: Mapped[int | None] = mapped_column(fk("suppliers.id"), index=True)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")

    supplier: Mapped[Supplier | None] = relationship(back_populates="products")


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint(f"cuit IS NULL OR {CUIT_FORMAT}", name="cuit_format"),
        CheckConstraint(
            "tax_condition = 'consumidor_final' OR cuit IS NOT NULL",
            name="cuit_required_unless_final_consumer",
        ),
        CheckConstraint("payment_terms_days >= 0", name="payment_terms_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    cuit: Mapped[str | None] = mapped_column(String(11), unique=True)
    tax_condition: Mapped[TaxCondition] = mapped_column(str_enum(TaxCondition, "tax_condition"))
    address: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    payment_terms_days: Mapped[int] = mapped_column(default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
