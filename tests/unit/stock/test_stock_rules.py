import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.catalog import Product
from app.models.enums import AdjustmentReason, StockMovementKind
from app.models.stock import StockMovement
from app.schemas.stock import AdjustmentCreate, MovementOut
from app.services.stock import REASON_LABELS, entry_description

D = Decimal


def test_entry_description_includes_reason_sku_and_signed_quantity() -> None:
    assert (
        entry_description(AdjustmentReason.DAMAGE, "SKU-1", D("-2.5"))
        == "Ajuste de stock (rotura) SKU-1 -2.5"
    )
    assert (
        entry_description(AdjustmentReason.INITIAL_LOAD, "SKU-1", D("10"))
        == "Ajuste de stock (carga inicial) SKU-1 +10"
    )


def test_every_reason_has_a_label() -> None:
    assert set(REASON_LABELS) == set(AdjustmentReason)


def test_movement_out_value_is_signed_and_rounded() -> None:
    product = Product(id=1, sku="SKU-1", name="Tornillo")
    movement = StockMovement(
        id=7,
        product_id=1,
        product=product,
        date=dt.date(2026, 11, 27),
        quantity=D("-1.333"),
        kind=StockMovementKind.ADJUSTMENT,
        reason=AdjustmentReason.DAMAGE,
        unit_cost=D("10.00"),
        created_by=1,
        created_at=dt.datetime(2026, 11, 27, tzinfo=dt.UTC),
    )

    out = MovementOut.from_movement(movement)

    assert out.value == D("-13.33")
    assert (out.product_sku, out.product_name) == ("SKU-1", "Tornillo")
    assert out.model_dump(mode="json")["quantity"] == "-1.333"


def test_adjustment_create_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AdjustmentCreate.model_validate(
            {"product_id": 1, "quantity": "1", "reason": "damage", "unit_cost": "5"}
        )
