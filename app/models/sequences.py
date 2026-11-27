from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum
from app.models.enums import DocumentType, InvoiceLetter

# Point of sale used by documents numbered globally (orders, journal entries).
GLOBAL_POINT_OF_SALE = 0


class DocumentSequence(Base):
    """Next number per (document type, letter, point of sale). Rows are created on demand and
    always read with SELECT ... FOR UPDATE (see app/services/sequences.py)."""

    __tablename__ = "document_sequences"
    __table_args__ = (
        UniqueConstraint("doc_type", "letter", "point_of_sale", postgresql_nulls_not_distinct=True),
        CheckConstraint("point_of_sale >= 0", name="point_of_sale_non_negative"),
        CheckConstraint("next_number >= 1", name="next_number_positive"),
        CheckConstraint(
            "(doc_type = 'invoice') = (letter IS NOT NULL)", name="letter_only_for_invoices"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[DocumentType] = mapped_column(str_enum(DocumentType, "document_type"))
    letter: Mapped[InvoiceLetter | None] = mapped_column(str_enum(InvoiceLetter, "invoice_letter"))
    point_of_sale: Mapped[int]
    next_number: Mapped[int] = mapped_column(default=1, server_default="1")
