from sqlmodel import Session, select

from auto.constants import normalize
from auto.models import FilterPreset
from auto.records import require_reader
from auto.schemas import VehicleInput
from auto.services import BusinessError, get_vehicle, require_editor

CLONE_FIELDS = (
    "brand_id",
    "model_id",
    "vehicle_type_id",
    "support_service_id",
    "work_unit",
    "engine_type_id",
    "fuel_type_id",
    "engine_not_applicable",
    "engine_displacement_cm3",
    "engine_power_kw",
    "payload_kg",
    "seats",
    "gross_weight_kg",
    "unloaded_weight_kg",
    "color",
)
PRESET_KEYS = {
    "registry_search",
    "include_archived",
    "incomplete_only",
    "registry_columns",
    "registry_sort",
    "registry_reverse",
    "registry_operated",
    "filter_unit_id",
    "filter_mvo_id",
    "filter_support_service_id",
    "filter_vehicle_type_id",
    "filter_accounting_status",
    "filter_technical_condition",
    "filter_technical_category",
}


def clone_draft(engine, vehicle_id, actor):
    require_editor(actor)
    vehicle = get_vehicle(engine, vehicle_id)
    if not vehicle:
        raise BusinessError("Автомобіль не знайдено")
    return VehicleInput.model_validate({f: getattr(vehicle, f) for f in CLONE_FIELDS}).model_dump()


def list_presets(engine, actor):
    require_reader(actor)
    with Session(engine) as s:
        return s.exec(
            select(FilterPreset)
            .where(FilterPreset.user_id == actor.user_id)
            .order_by(FilterPreset.name)
        ).all()


def save_preset(engine, name, settings, actor):
    require_reader(actor)
    if not normalize(name) or len(name) > 100 or set(settings) - PRESET_KEYS:
        raise BusinessError("Неправильна назва або поля набору фільтрів")
    if not all(v is None or isinstance(v, (str, bool, int, list)) for v in settings.values()):
        raise BusinessError("Неправильні значення набору фільтрів")
    with Session(engine) as s:
        s.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = s.exec(
            select(FilterPreset).where(
                FilterPreset.user_id == actor.user_id,
                FilterPreset.normalized_name == normalize(name),
            )
        ).first()
        row = row or FilterPreset(
            user_id=actor.user_id, name=name, normalized_name=normalize(name), settings={}
        )
        row.name, row.settings = name, settings
        s.add(row)
        s.commit()


def delete_preset(engine, preset_id, actor):
    require_reader(actor)
    with Session(engine) as s:
        row = s.get(FilterPreset, preset_id)
        if not row or row.user_id != actor.user_id:
            raise BusinessError("Набір фільтрів недоступний")
        s.delete(row)
        s.commit()
