import datetime as dt

from sqlalchemy import CheckConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt, Money, fk, str_enum
from app.models.catalog import Customer
from app.models.enums import PaymentMethod
from app.models.sales import Invoice


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        UniqueConstraint("point_of_sale", "number"),
        CheckConstraint("point_of_sale > 0", name="point_of_sale_positive"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    point_of_sale: Mapped[int]
    number: Mapped[int]
    customer_id: Mapped[int] = mapped_column(fk("customers.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    method: Mapped[PaymentMethod] = mapped_column(str_enum(PaymentMethod, "payment_method"))
    reference: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Money]
    created_by: Mapped[int] = mapped_column(fk("users.id"), index=True)
    created_at: Mapped[CreatedAt]

    customer: Mapped[Customer] = relationship()
    allocations: Mapped[list["ReceiptAllocation"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", order_by="ReceiptAllocation.id"
    )


class ReceiptAllocation(Base):
    __tablename__ = "receipt_allocations"
    __table_args__ = (
        UniqueConstraint("receipt_id", "invoice_id"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(fk("receipts.id", ondelete="CASCADE"))
    invoice_id: Mapped[int] = mapped_column(fk("invoices.id"), index=True)
    amount: Mapped[Money]

    receipt: Mapped[Receipt] = relationship(back_populates="allocations")
    invoice: Mapped[Invoice] = relationship()
