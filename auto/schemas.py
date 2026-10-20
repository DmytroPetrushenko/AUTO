import re
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class VehicleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")

    brand_id: int
    model_id: int
    vehicle_type_id: int
    support_service_id: int
    work_unit: Literal["км", "мотогодини", "без лічильника"]
    military_number: str | None = None
    vin: str | None = None
    vin_explanation: str | None = None
    chassis_number: str | None = None
    serial_number: str | None = None
    unit_id: int | None = None
    mvo_id: int | None = None
    accounting_status: Literal[
        "На обліку", "Тимчасово передано", "Передано", "Списано", "Втрачено (знищено)"
    ] = "На обліку"
    technical_condition: Literal["Не визначено", "Справний", "Несправний"] = "Не визначено"
    technical_category: Literal["Не визначено", "I", "II", "III", "IV", "V"] = "Не визначено"
    is_operated: bool = False
    manufacture_value: str | None = None
    manufacture_precision: Literal["невідомо", "рік", "місяць і рік", "точна дата"] = "невідомо"
    engine_type_id: int | None = None
    fuel_type_id: int | None = None
    engine_not_applicable: bool = False
    engine_displacement_cm3: float | None = Field(default=None, ge=0)
    engine_power_kw: float | None = Field(default=None, ge=0)
    engine_number: str | None = None
    payload_kg: float | None = Field(default=None, ge=0)
    seats: int | None = Field(default=None, ge=0)
    gross_weight_kg: float | None = Field(default=None, ge=0)
    unloaded_weight_kg: float | None = Field(default=None, ge=0)
    color: str | None = None
    accounting_date: date | None = None
    vk_code: str | None = None
    nomenclature_number: str | None = None
    current_location: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def empty_is_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_card(self) -> Self:
        if (
            self.gross_weight_kg is not None
            and self.unloaded_weight_kg is not None
            and self.gross_weight_kg < self.unloaded_weight_kg
        ):
            raise ValueError("Повна маса має бути не меншою за масу без навантаження")
        if self.is_operated and self.accounting_status in ("Списано", "Втрачено (знищено)"):
            raise ValueError("Списана або втрачена техніка не може експлуатуватися")
        if self.vin and not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", self.vin.strip().upper()):
            if not self.vin_explanation or not self.vin_explanation.strip():
                raise ValueError("Для нестандартного VIN потрібне пояснення")
        value = self.manufacture_value
        precision = self.manufacture_precision
        patterns = {
            "рік": r"\d{4}",
            "місяць і рік": r"\d{4}-\d{2}",
            "точна дата": r"\d{4}-\d{2}-\d{2}",
        }
        if precision == "невідомо":
            if value is not None:
                raise ValueError("При невідомій даті виготовлення значення має бути порожнім")
        else:
            if value is None or not re.fullmatch(patterns[precision], value):
                raise ValueError(
                    "Дата виготовлення не відповідає точності: YYYY / YYYY-MM / YYYY-MM-DD"
                )
            # Validate actual calendar values without storing invented month/day.
            date.fromisoformat(
                value + {"рік": "-01-01", "місяць і рік": "-01", "точна дата": ""}[precision]
            )
        if self.engine_not_applicable and any(
            getattr(self, field) is not None
            for field in (
                "engine_type_id",
                "fuel_type_id",
                "engine_displacement_cm3",
                "engine_power_kw",
                "engine_number",
            )
        ):
            raise ValueError(
                "Для двигуна «не застосовується» очистіть характеристики двигуна і пального"
            )
        return self
