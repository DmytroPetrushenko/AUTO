from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from auto.models import AuditEvent, Vehicle
from auto.services import (
    Actor,
    BusinessError,
    ConflictError,
    Role,
    add_person,
    completeness,
    get_vehicle,
    list_audit,
    list_lookup,
    list_vehicles,
    save_lookup,
    save_vehicle,
)

ADMIN = Actor("test-admin", Role.ADMIN)
VIEWER = Actor("test-viewer", Role.VIEWER)


def test_minimum_optional_numbers_and_no_reading(engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum, actor)
    assert UUID(vehicle.id)
    assert vehicle.military_number is None
    assert vehicle.vin is None
    assert vehicle.engine_type_id is None
    assert not hasattr(vehicle, "current_reading")
    assert vehicle.version == 1
    assert vehicle.technical_condition == vehicle.technical_category == "Не визначено"
    assert len(list_audit(engine, vehicle.id)) == 1


@pytest.mark.parametrize(
    "field", ["brand_id", "model_id", "vehicle_type_id", "support_service_id", "work_unit"]
)
def test_required_fields(engine, minimum, actor, field):
    del minimum[field]
    with pytest.raises(ValidationError):
        save_vehicle(engine, minimum, actor)
    assert list_vehicles(engine) == []


@pytest.mark.parametrize(
    "data",
    [
        {"gross_weight_kg": 10, "unloaded_weight_kg": 20},
        {"payload_kg": -1},
        {"seats": 1.5},
        {"engine_power_kw": float("nan")},
        {"engine_power_kw": float("inf")},
        {"accounting_status": "Списано", "is_operated": True},
        {"manufacture_precision": "місяць і рік", "manufacture_value": "2020-13"},
        {"manufacture_precision": "точна дата", "manufacture_value": "2023-02-29"},
        {"manufacture_value": "2020"},
        {"vin": "нестандартний"},
        {"engine_not_applicable": True, "engine_power_kw": 10},
    ],
)
def test_invalid_input_is_atomic(engine, minimum, actor, data):
    vehicle = save_vehicle(engine, minimum, actor)
    before = len(list_audit(engine, vehicle.id))
    with pytest.raises(ValidationError):
        save_vehicle(engine, minimum | data, actor, vehicle_id=vehicle.id, expected_version=1)
    assert get_vehicle(engine, vehicle.id).version == 1
    assert len(list_audit(engine, vehicle.id)) == before


@pytest.mark.parametrize(
    ("precision", "value"),
    [
        ("рік", "2020"),
        ("місяць і рік", "2020-02"),
        ("точна дата", "2020-02-29"),
    ],
)
def test_manufacture_precision_is_preserved(engine, minimum, actor, precision, value):
    vehicle = save_vehicle(
        engine, minimum | dict(manufacture_precision=precision, manufacture_value=value), actor
    )
    assert get_vehicle(engine, vehicle.id).manufacture_value == value


def test_trailer_needs_no_engine(engine, minimum, actor):
    trailer = next(v for v in list_lookup(engine, "vehicle_types") if v.name == "причіп")
    vehicle = save_vehicle(
        engine,
        minimum
        | dict(vehicle_type_id=trailer.id, work_unit="без лічильника", engine_not_applicable=True),
        actor,
    )
    assert vehicle.engine_type_id is None
    assert "Тип двигуна" not in completeness(vehicle)


def test_nonstandard_vin_with_explanation(engine, minimum, actor):
    vehicle = save_vehicle(
        engine, minimum | dict(vin="старий-1", vin_explanation="Старе шасі"), actor
    )
    assert vehicle.vin == "старий-1"


def test_model_must_match_brand(engine, minimum, actor):
    brand = save_lookup(engine, "vehicle_brands", "Інша марка", actor)
    with pytest.raises(BusinessError, match="не належить"):
        save_vehicle(engine, minimum | {"brand_id": brand.id}, actor)


@pytest.mark.parametrize("field", ["military_number", "vin", "chassis_number", "serial_number"])
def test_duplicates_include_archive_and_preserve_original(engine, minimum, actor, field):
    first = save_vehicle(
        engine,
        minimum
        | {
            field: "  AB  123  ",
            "vin_explanation": "Тестовий ідентифікатор",
            "accounting_status": "Передано",
        },
        actor,
    )
    assert getattr(first, field) == "  AB  123  "
    with pytest.raises(BusinessError, match=first.id):
        save_vehicle(engine, minimum | {field: "ab 123", "vin_explanation": "Тест"}, actor)
    assert len(list_vehicles(engine, include_archived=True)) == 1


def test_empty_identifiers_are_not_duplicates(engine, minimum, actor):
    save_vehicle(engine, minimum, actor)
    save_vehicle(engine, minimum | {"military_number": "  "}, actor)
    assert len(list_vehicles(engine)) == 2


def test_updates_keep_id_check_version_and_audit(engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum | {"technical_category": "III"}, actor)
    updated = save_vehicle(
        engine,
        minimum
        | {
            "technical_category": "III",
            "technical_condition": "Несправний",
        },
        actor,
        vehicle_id=vehicle.id,
        expected_version=1,
    )
    assert updated.id == vehicle.id
    assert updated.version == 2
    assert updated.technical_category == "III"
    with pytest.raises(ConflictError):
        save_vehicle(engine, minimum, actor, vehicle_id=vehicle.id, expected_version=1)
    event = list_audit(engine, vehicle.id)[0]
    assert event.user_id == actor.user_id
    assert event.old_values["technical_condition"] == "Не визначено"
    assert event.new_values["technical_condition"] == "Несправний"


def test_concurrent_duplicate_creation_is_serialized(engine, minimum, actor):
    def create():
        try:
            return save_vehicle(engine, minimum | {"chassis_number": "same"}, actor).id
        except BusinessError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: create(), range(2)))
    assert sum(x is not None for x in ids) == 1


def test_viewer_blocked_in_services(engine, minimum):
    with pytest.raises(PermissionError):
        save_vehicle(engine, minimum, VIEWER)
    with pytest.raises(PermissionError):
        save_lookup(engine, "units", "test", VIEWER)
    with pytest.raises(PermissionError):
        add_person(engine, "Ім'я", "Прізвище", VIEWER)


def test_lookup_normalization_rename_and_deactivation(engine, minimum, actor):
    with pytest.raises(BusinessError, match="Дублікат"):
        save_lookup(engine, "vehicle_brands", "  ТЕСТОВА   МАРКА ", actor)
    with pytest.raises(PermissionError):
        save_lookup(engine, "vehicle_brands", "new", actor, entry_id=minimum["brand_id"])
    first = save_vehicle(engine, minimum, actor)
    save_lookup(
        engine,
        "vehicle_brands",
        "Перейменована",
        ADMIN,
        entry_id=minimum["brand_id"],
        is_active=False,
    )
    assert list_lookup(engine, "vehicle_brands") == []
    with pytest.raises(BusinessError, match="активне"):
        save_vehicle(engine, minimum, actor)
    # Existing cards can still be edited with an unchanged retired reference.
    save_vehicle(
        engine, minimum | {"color": "Зелений"}, actor, vehicle_id=first.id, expected_version=1
    )


def test_status_archive_and_filters(engine, minimum, actor):
    save_vehicle(engine, minimum | {"military_number": "AA 1"}, actor)
    save_vehicle(engine, minimum | {"accounting_status": "Тимчасово передано"}, actor)
    archive = save_vehicle(engine, minimum | {"accounting_status": "Списано"}, actor)
    assert len(list_vehicles(engine)) == 2
    assert len(list_vehicles(engine, include_archived=True)) == 3
    assert list_vehicles(engine, accounting_status="Списано")[0].id == archive.id
    assert len(list_vehicles(engine, search="aa   1")) == 1
    assert len(list_vehicles(engine, search="тестова марка")) == 2
    assert len(list_vehicles(engine, incomplete_only=True)) == 2
    assert len(list_vehicles(engine, is_operated=True)) == 0


def test_audit_is_append_only(engine, minimum, actor):
    save_vehicle(engine, minimum, actor)
    with engine.begin() as connection:
        with pytest.raises(IntegrityError, match="append-only"):
            connection.exec_driver_sql("DELETE FROM audit_events")
        with pytest.raises(IntegrityError, match="append-only"):
            connection.exec_driver_sql("UPDATE audit_events SET user_id='changed'")


def test_person_requires_names(engine, actor):
    with pytest.raises(BusinessError):
        add_person(engine, "", "Test", actor)
    person = add_person(engine, "Тест", "Тестовий", actor)
    assert person.id
    with Session(engine) as session:
        assert session.exec(select(AuditEvent).where(AuditEvent.entity_type == "personnel")).first()
        assert session.exec(select(Vehicle)).all() == []
