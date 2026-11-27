"""Gap-free document numbering.

The sequence row is locked with SELECT ... FOR UPDATE and incremented inside the caller's
transaction: if the transaction rolls back, so does the increment, so no numbers are lost.
Concurrent callers for the same (type, letter, point of sale) are serialized by the row lock.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.enums import DocumentType, InvoiceLetter
from app.models.sequences import DocumentSequence


def next_number(
    session: Session,
    doc_type: DocumentType,
    *,
    point_of_sale: int,
    letter: InvoiceLetter | None = None,
) -> int:
    if (doc_type is DocumentType.INVOICE) != (letter is not None):
        raise ValueError("letter is required for invoices and forbidden otherwise")

    session.execute(
        insert(DocumentSequence)
        .values(doc_type=doc_type, letter=letter, point_of_sale=point_of_sale, next_number=1)
        .on_conflict_do_nothing()
    )
    letter_filter = (
        DocumentSequence.letter.is_(None) if letter is None else DocumentSequence.letter == letter
    )
    sequence = session.scalars(
        select(DocumentSequence)
        .where(
            DocumentSequence.doc_type == doc_type,
            DocumentSequence.point_of_sale == point_of_sale,
            letter_filter,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    number = sequence.next_number
    sequence.next_number = number + 1
    session.flush()
    return number
