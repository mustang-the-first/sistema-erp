from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Annotated

from sqlalchemy import DateTime, Enum, ForeignKey, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Column types -----------------------------------------------------------------------------
Money = Annotated[Decimal, mapped_column(Numeric(14, 2))]
Quantity = Annotated[Decimal, mapped_column(Numeric(12, 3))]
CreatedAt = Annotated[
    datetime, mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
]


def str_enum[E: PyEnum](enum_cls: type[E], name: str) -> Enum:
    """Enum stored as VARCHAR + named CHECK constraint (no native PG type: clean downgrades)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [m.value for m in members],
    )


def fk(target: str, *, ondelete: str | None = None) -> ForeignKey:
    return ForeignKey(target, ondelete=ondelete)
