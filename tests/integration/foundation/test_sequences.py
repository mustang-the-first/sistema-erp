import pytest
from sqlalchemy.orm import Session

from app.models.enums import DocumentType, InvoiceLetter
from app.services.sequences import next_number


def test_sequence_starts_at_one_and_increments(db: Session) -> None:
    numbers = [next_number(db, DocumentType.RECEIPT, point_of_sale=1) for _ in range(3)]

    assert numbers == [1, 2, 3]


def test_sequences_are_independent_per_type_letter_and_point_of_sale(db: Session) -> None:
    inv = DocumentType.INVOICE

    assert next_number(db, inv, point_of_sale=1, letter=InvoiceLetter.A) == 1
    assert next_number(db, inv, point_of_sale=1, letter=InvoiceLetter.A) == 2
    assert next_number(db, inv, point_of_sale=1, letter=InvoiceLetter.B) == 1
    assert next_number(db, inv, point_of_sale=2, letter=InvoiceLetter.A) == 1
    assert next_number(db, DocumentType.DELIVERY_NOTE, point_of_sale=1) == 1


def test_rolled_back_number_is_reused(db: Session) -> None:
    with db.begin_nested() as savepoint:
        assert next_number(db, DocumentType.RECEIPT, point_of_sale=1) == 1
        savepoint.rollback()

    assert next_number(db, DocumentType.RECEIPT, point_of_sale=1) == 1


def test_invoice_requires_letter(db: Session) -> None:
    with pytest.raises(ValueError, match="letter"):
        next_number(db, DocumentType.INVOICE, point_of_sale=1)


def test_letter_is_rejected_for_other_documents(db: Session) -> None:
    with pytest.raises(ValueError, match="letter"):
        next_number(db, DocumentType.RECEIPT, point_of_sale=1, letter=InvoiceLetter.A)
