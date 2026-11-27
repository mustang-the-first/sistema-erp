import datetime as dt

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt, Money, Quantity, fk, str_enum
from app.models.catalog import Customer, Product
from app.models.enums import InvoiceLetter, OrderStatus, TaxCondition, VatRate


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(unique=True)
    customer_id: Mapped[int] = mapped_column(fk("customers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    status: Mapped[OrderStatus] = mapped_column(
        str_enum(OrderStatus, "order_status"), index=True, default=OrderStatus.DRAFT
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(fk("users.id"), index=True)
    created_at: Mapped[CreatedAt]

    customer: Mapped[Customer] = relationship()
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.id"
    )
    delivery_notes: Mapped[list["DeliveryNote"]] = relationship(back_populates="order")


class OrderLine(Base):
    __tablename__ = "order_lines"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(fk("orders.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(fk("products.id"), index=True)
    quantity: Mapped[Quantity]
    unit_price: Mapped[Money]  # snapshot of Product.unit_price
    vat_rate: Mapped[VatRate] = mapped_column(str_enum(VatRate, "vat_rate"))  # snapshot

    order: Mapped[Order] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()


class DeliveryNote(Base):
    __tablename__ = "delivery_notes"
    __table_args__ = (
        UniqueConstraint("point_of_sale", "number"),
        CheckConstraint("point_of_sale > 0", name="point_of_sale_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    point_of_sale: Mapped[int]
    number: Mapped[int]
    order_id: Mapped[int] = mapped_column(fk("orders.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    created_by: Mapped[int] = mapped_column(fk("users.id"), index=True)
    created_at: Mapped[CreatedAt]

    order: Mapped[Order] = relationship(back_populates="delivery_notes")
    lines: Mapped[list["DeliveryNoteLine"]] = relationship(
        back_populates="delivery_note", cascade="all, delete-orphan", order_by="DeliveryNoteLine.id"
    )
    invoice: Mapped["Invoice | None"] = relationship(back_populates="delivery_note")


class DeliveryNoteLine(Base):
    __tablename__ = "delivery_note_lines"
    __table_args__ = (
        UniqueConstraint("delivery_note_id", "order_line_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_note_id: Mapped[int] = mapped_column(fk("delivery_notes.id", ondelete="CASCADE"))
    order_line_id: Mapped[int] = mapped_column(fk("order_lines.id"), index=True)
    product_id: Mapped[int] = mapped_column(fk("products.id"), index=True)
    quantity: Mapped[Quantity]

    delivery_note: Mapped[DeliveryNote] = relationship(back_populates="lines")
    order_line: Mapped[OrderLine] = relationship()
    product: Mapped[Product] = relationship()


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("letter", "point_of_sale", "number"),
        CheckConstraint("point_of_sale > 0", name="point_of_sale_positive"),
        CheckConstraint("due_date >= date", name="due_date_after_date"),
        CheckConstraint(
            "net_total >= 0 AND vat_21 >= 0 AND vat_10_5 >= 0", name="amounts_non_negative"
        ),
        CheckConstraint("total > 0", name="total_positive"),
        CheckConstraint("total = net_total + vat_21 + vat_10_5", name="total_consistency"),
        CheckConstraint(
            "letter <> 'A' OR customer_cuit IS NOT NULL", name="letter_a_requires_cuit"
        ),
        Index("ix_invoices_date", "date"),
        Index("ix_invoices_due_date", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    letter: Mapped[InvoiceLetter] = mapped_column(str_enum(InvoiceLetter, "invoice_letter"))
    point_of_sale: Mapped[int]
    number: Mapped[int]
    customer_id: Mapped[int] = mapped_column(fk("customers.id"), index=True)
    delivery_note_id: Mapped[int] = mapped_column(fk("delivery_notes.id"), unique=True)
    date: Mapped[dt.date]
    due_date: Mapped[dt.date]
    # Customer snapshot at issue time (the printable invoice must not change afterwards).
    customer_name: Mapped[str] = mapped_column(String(200))
    customer_cuit: Mapped[str | None] = mapped_column(String(11))
    customer_tax_condition: Mapped[TaxCondition] = mapped_column(
        str_enum(TaxCondition, "tax_condition")
    )
    net_total: Mapped[Money]
    vat_21: Mapped[Money]
    vat_10_5: Mapped[Money]
    total: Mapped[Money]
    created_by: Mapped[int] = mapped_column(fk("users.id"), index=True)
    created_at: Mapped[CreatedAt]

    customer: Mapped[Customer] = relationship()
    delivery_note: Mapped[DeliveryNote] = relationship(back_populates="invoice")
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.id"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("net_amount >= 0 AND vat_amount >= 0", name="amounts_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(fk("invoices.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(fk("products.id"), index=True)
    description: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[Quantity]
    unit_price: Mapped[Money]
    vat_rate: Mapped[VatRate] = mapped_column(str_enum(VatRate, "vat_rate"))
    net_amount: Mapped[Money]
    vat_amount: Mapped[Money]

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()
