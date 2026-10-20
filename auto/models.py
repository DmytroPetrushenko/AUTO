from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import registry
from sqlmodel import Field, SQLModel


class Base(SQLModel, registry=registry()):
    """Own mappings/metadata per module load, including Streamlit source reloads."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LookupBase(Base):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=200)
    normalized_name: str = Field(max_length=200, index=True)
    is_active: bool = True


class VehicleBrand(LookupBase, table=True):
    __tablename__ = "vehicle_brands"
    __table_args__ = (UniqueConstraint("normalized_name"),)


class VehicleModel(LookupBase, table=True):
    __tablename__ = "vehicle_models"
    __table_args__ = (
        UniqueConstraint("brand_id", "normalized_name"),
        UniqueConstraint("id", "brand_id"),
    )
    brand_id: int = Field(foreign_key="vehicle_brands.id", index=True)


class VehicleType(LookupBase, table=True):
    __tablename__ = "vehicle_types"
    __table_args__ = (UniqueConstraint("normalized_name"),)
    suggested_work_unit: str | None = None


class SupportService(LookupBase, table=True):
    __tablename__ = "support_services"
    __table_args__ = (UniqueConstraint("normalized_name"),)


class EngineType(LookupBase, table=True):
    __tablename__ = "engine_types"
    __table_args__ = (UniqueConstraint("normalized_name"),)


class FuelType(LookupBase, table=True):
    __tablename__ = "fuel_types"
    __table_args__ = (UniqueConstraint("normalized_name"),)


class Unit(LookupBase, table=True):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("normalized_name"),)


class Personnel(Base, table=True):
    __tablename__ = "personnel"
    id: int | None = Field(default=None, primary_key=True)
    first_name: str
    last_name: str
    patronymic: str | None = None
    rank: str | None = None
    position: str | None = None
    unit_id: int | None = Field(default=None, foreign_key="units.id")
    personal_number: str | None = None
    personal_number_normalized: str | None = Field(default=None, unique=True, index=True)
    is_active: bool = True
    version: int = 1


class Vehicle(Base, table=True):
    __tablename__ = "vehicles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id", "brand_id"],
            ["vehicle_models.id", "vehicle_models.brand_id"],
        ),
        CheckConstraint("version >= 1", name="ck_vehicle_version"),
        CheckConstraint(
            "gross_weight_kg IS NULL OR unloaded_weight_kg IS NULL "
            "OR gross_weight_kg >= unloaded_weight_kg",
            name="ck_vehicle_weights",
        ),
        CheckConstraint(
            "accounting_status IN ('На обліку','Тимчасово передано','Передано',"
            "'Списано','Втрачено (знищено)')",
            name="ck_vehicle_status",
        ),
        CheckConstraint(
            "NOT is_operated OR accounting_status NOT IN ('Списано','Втрачено (знищено)')",
            name="ck_vehicle_operation",
        ),
        CheckConstraint(
            "work_unit IN ('км','мотогодини','без лічильника')",
            name="ck_vehicle_work_unit",
        ),
        CheckConstraint(
            "technical_condition IN ('Не визначено','Справний','Несправний')",
            name="ck_vehicle_condition",
        ),
        CheckConstraint(
            "technical_category IN ('Не визначено','I','II','III','IV','V')",
            name="ck_vehicle_category",
        ),
        *(
            CheckConstraint(f"{field} IS NULL OR {field} >= 0", name=f"ck_vehicle_{field}")
            for field in (
                "engine_displacement_cm3",
                "engine_power_kw",
                "payload_kg",
                "seats",
                "gross_weight_kg",
                "unloaded_weight_kg",
            )
        ),
        Index("ix_vehicle_unit_status", "unit_id", "accounting_status"),
    )
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    military_number: str | None = None
    military_number_normalized: str | None = Field(default=None, index=True)
    vin: str | None = None
    vin_normalized: str | None = Field(default=None, index=True)
    vin_explanation: str | None = None
    chassis_number: str | None = None
    chassis_number_normalized: str | None = Field(default=None, index=True)
    serial_number: str | None = None
    serial_number_normalized: str | None = Field(default=None, index=True)
    brand_id: int = Field(foreign_key="vehicle_brands.id")
    model_id: int = Field(foreign_key="vehicle_models.id")
    vehicle_type_id: int = Field(foreign_key="vehicle_types.id", index=True)
    support_service_id: int = Field(foreign_key="support_services.id", index=True)
    unit_id: int | None = Field(default=None, foreign_key="units.id", index=True)
    mvo_id: int | None = Field(default=None, foreign_key="personnel.id", index=True)
    work_unit: str
    accounting_status: str = Field(default="На обліку", index=True)
    technical_condition: str = Field(default="Не визначено", index=True)
    technical_category: str = Field(default="Не визначено", index=True)
    is_operated: bool = False
    manufacture_value: str | None = None
    manufacture_precision: str = "невідомо"
    engine_type_id: int | None = Field(default=None, foreign_key="engine_types.id")
    fuel_type_id: int | None = Field(default=None, foreign_key="fuel_types.id")
    engine_not_applicable: bool = False
    engine_displacement_cm3: float | None = None
    engine_power_kw: float | None = None
    engine_number: str | None = None
    payload_kg: float | None = None
    seats: int | None = None
    gross_weight_kg: float | None = None
    unloaded_weight_kg: float | None = None
    color: str | None = None
    accounting_date: date | None = Field(default=None, index=True)
    vk_code: str | None = None
    nomenclature_number: str | None = None
    current_location: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str
    updated_at: datetime = Field(default_factory=utcnow)
    updated_by: str
    version: int = 1


class AuditEvent(Base, table=True):
    """No FK to vehicles: audit survives a future authorized deletion."""

    __tablename__ = "audit_events"
    id: int | None = Field(default=None, primary_key=True)
    entity_type: str = Field(index=True)
    entity_id: str = Field(index=True)
    action: str
    user_id: str
    role: str
    recorded_at: datetime = Field(default_factory=utcnow, index=True)
    old_values: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    new_values: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


class VehicleHistory(Base, table=True):
    """Typed timelines; NULL valid_from means the actual start is unknown."""

    __tablename__ = "vehicle_history"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "kind", "valid_from"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_history_interval",
        ),
        CheckConstraint(
            "kind IN ('unit','service','mvo','status','military_number',"
            "'vin','chassis_number','serial_number')",
            name="ck_history_kind",
        ),
        Index(
            "ix_history_unknown_start",
            "vehicle_id",
            "kind",
            unique=True,
            sqlite_where=text("valid_from IS NULL"),
        ),
    )
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    vehicle_id: str = Field(foreign_key="vehicles.id", index=True)
    kind: str = Field(index=True)
    valid_from: date | None = Field(default=None, index=True)
    valid_to: date | None = Field(default=None, index=True)
    value: dict = Field(sa_column=Column(JSON, nullable=False))
    unit_id: int | None = Field(default=None, foreign_key="units.id")
    support_service_id: int | None = Field(default=None, foreign_key="support_services.id")
    person_id: int | None = Field(default=None, foreign_key="personnel.id")
    recorded_at: datetime = Field(default_factory=utcnow)
    recorded_by: str
    updated_at: datetime = Field(default_factory=utcnow)
    updated_by: str
    is_initial: bool = False


class DuplicateApproval(Base, table=True):
    __tablename__ = "duplicate_approvals"
    id: int | None = Field(default=None, primary_key=True)
    vehicle_id: str = Field(foreign_key="vehicles.id", index=True)
    field: str
    normalized_value: str = Field(index=True)
    matched_vehicle_ids: list = Field(sa_column=Column(JSON, nullable=False))
    reason: str
    approved_by: str
    approved_role: str
    approved_at: datetime = Field(default_factory=utcnow)


class VehicleDocumentLock(Base, table=True):
    """Integration contract for applied documents; no waybill module or UI."""

    __tablename__ = "vehicle_document_locks"
    id: int | None = Field(default=None, primary_key=True)
    vehicle_id: str = Field(foreign_key="vehicles.id", index=True)
    document_reference: str = Field(unique=True)
    document_date: date = Field(index=True)
    snapshot: dict = Field(sa_column=Column(JSON, nullable=False))
    registered_at: datetime = Field(default_factory=utcnow)
    registered_by: str


LOOKUPS = {
    "vehicle_brands": VehicleBrand,
    "vehicle_models": VehicleModel,
    "vehicle_types": VehicleType,
    "support_services": SupportService,
    "engine_types": EngineType,
    "fuel_types": FuelType,
    "units": Unit,
}
