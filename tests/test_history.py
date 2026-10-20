from datetime import date, timedelta

import pytest
from sqlmodel import Session, select

from auto.history import (
    KINDS,
    apply_history,
    delete_vehicle,
    list_history,
    preview_history,
    preview_vehicle,
    register_document_lock,
    today,
    values_at,
)
from auto.models import DuplicateApproval, Vehicle, VehicleDocumentLock
from auto.personnel import list_people, save_person
from auto.schemas import VehicleInput
from auto.services import (
    Actor,
    BusinessError,
    ConflictError,
    Role,
    add_person,
    get_vehicle,
    list_audit,
    list_vehicles,
    save_lookup,
    save_vehicle,
)

ADMIN = Actor("admin-test", Role.ADMIN)
VIEWER = Actor("viewer-test", Role.VIEWER)
D1, D2, D3 = date(2025, 1, 1), date(2025, 3, 1), date(2025, 6, 1)


def card(vehicle):
    return {f: getattr(vehicle, f) for f in VehicleInput.model_fields}


def change(engine, vehicle, actor, updates, day, **options):
    data = card(get_vehicle(engine, vehicle.id)) | updates
    preview = preview_vehicle(
        engine, data, actor, vehicle_id=vehicle.id, expected_version=vehicle.version, event_date=day
    )
    return save_vehicle(
        engine,
        data,
        actor,
        vehicle_id=vehicle.id,
        expected_version=vehicle.version,
        event_date=day,
        preview_token=preview["token"],
        confirmed=True,
        **options,
    )


def test_initial_unknown_dates_and_audit(engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum, actor)
    rows = list_history(engine, vehicle.id)
    assert len(rows) == len(KINDS)
    assert all(r["valid_from"] is None and r["valid_to"] is None for r in rows)
    assert any(a.action == "periods_reconciled" for a in list_audit(engine, vehicle.id))


def test_backdated_preview_splits_without_changing_later_assignment(engine, minimum, actor):
    u1 = save_lookup(engine, "units", "One", actor)
    u2 = save_lookup(engine, "units", "Two", actor)
    vehicle = save_vehicle(engine, minimum, actor)
    vehicle = change(engine, vehicle, actor, {"unit_id": u2.id}, D3)
    data = card(vehicle) | {"unit_id": u1.id}
    p = preview_vehicle(
        engine, data, actor, vehicle_id=vehicle.id, expected_version=2, event_date=D1
    )
    assert len([r for r in p["before"] if r["kind"] == "unit"]) == 2
    assert len([r for r in p["after"] if r["kind"] == "unit"]) == 3
    assert get_vehicle(engine, vehicle.id).version == 2
    saved = save_vehicle(
        engine,
        data,
        actor,
        vehicle_id=vehicle.id,
        expected_version=2,
        event_date=D1,
        preview_token=p["token"],
        confirmed=True,
    )
    rows = list_history(engine, vehicle.id)
    assert values_at(rows, D2)["unit_id"] == u1.id
    assert values_at(rows, D3)["unit_id"] == u2.id
    assert saved.unit_id == u2.id
    unit_rows = [r for r in rows if r["kind"] == "unit"]
    assert [(r["valid_from"], r["valid_to"]) for r in unit_rows] == [
        (None, D1),
        (D1, D3),
        (D3, None),
    ]


def test_history_date_move_reconciles_both_neighbors(engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum, actor)
    vehicle = change(engine, vehicle, actor, {"military_number": "A"}, D1)
    vehicle = change(engine, vehicle, actor, {"military_number": "B"}, D3)
    event = next(
        r
        for r in list_history(engine, vehicle.id)
        if r["kind"] == "military_number" and r["valid_from"] == D3
    )
    p = preview_history(engine, vehicle.id, event["id"], {"military_number": "B"}, D2, actor, 3)
    assert get_vehicle(engine, vehicle.id).version == 3
    saved = apply_history(
        engine,
        vehicle.id,
        event["id"],
        {"military_number": "B"},
        D2,
        actor,
        3,
        p["token"],
        confirmed=True,
    )
    assert saved.version == 4
    rows = [r for r in list_history(engine, vehicle.id) if r["kind"] == "military_number"]
    assert [(r["valid_from"], r["valid_to"]) for r in rows] == [(None, D1), (D1, D2), (D2, None)]


@pytest.mark.parametrize(
    "kind,updates",
    [
        ("service", {"support_service_id": 2}),
        ("status", {"accounting_status": "Тимчасово передано"}),
        ("military_number", {"military_number": "New"}),
        ("vin", {"vin": "OLD", "vin_explanation": "Old construction"}),
        ("chassis_number", {"chassis_number": "Frame"}),
        ("serial_number", {"serial_number": "Serial"}),
    ],
)
def test_dated_kinds(engine, minimum, actor, kind, updates):
    minimum["support_service_id"] = 1
    vehicle = save_vehicle(engine, minimum, actor)
    vehicle = change(engine, vehicle, actor, updates, D1)
    assert next(
        r for r in list_history(engine, vehicle.id) if r["kind"] == kind and r["valid_from"] == D1
    )["value"] == {f: getattr(vehicle, f) for f in KINDS[kind]}


def test_no_silent_dates_and_no_unconfirmed_write(engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum, actor)
    data = card(vehicle) | {"military_number": "A"}
    with pytest.raises(BusinessError, match="дата події"):
        save_vehicle(engine, data, actor, vehicle_id=vehicle.id, expected_version=1)
    p = preview_vehicle(
        engine, data, actor, vehicle_id=vehicle.id, expected_version=1, event_date=D1
    )
    with pytest.raises(BusinessError, match="підтвердження"):
        save_vehicle(
            engine,
            data,
            actor,
            vehicle_id=vehicle.id,
            expected_version=1,
            event_date=D1,
            preview_token=p["token"],
        )
    assert get_vehicle(engine, vehicle.id).version == 1


def test_preview_is_bound_to_payload_and_version(engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum, actor)
    data = card(vehicle) | {"military_number": "A"}
    p = preview_vehicle(
        engine, data, actor, vehicle_id=vehicle.id, expected_version=1, event_date=D1
    )
    with pytest.raises(ConflictError, match="Перегляд застарів"):
        save_vehicle(
            engine,
            data | {"color": "changed"},
            actor,
            vehicle_id=vehicle.id,
            expected_version=1,
            event_date=D1,
            preview_token=p["token"],
            confirmed=True,
        )
    save_vehicle(
        engine, card(vehicle) | {"color": "other"}, actor, vehicle_id=vehicle.id, expected_version=1
    )
    with pytest.raises(ConflictError):
        save_vehicle(
            engine,
            data,
            actor,
            vehicle_id=vehicle.id,
            expected_version=1,
            event_date=D1,
            preview_token=p["token"],
            confirmed=True,
        )


def test_collision_on_moving_event_is_blocked(engine, minimum, actor):
    vehicle = change(
        engine, save_vehicle(engine, minimum, actor), actor, {"military_number": "A"}, D1
    )
    vehicle = change(engine, vehicle, actor, {"military_number": "B"}, D3)
    event = next(
        r
        for r in list_history(engine, vehicle.id)
        if r["kind"] == "military_number" and r["valid_from"] == D3
    )
    with pytest.raises(BusinessError, match="одну дату"):
        preview_history(engine, vehicle.id, event["id"], event["value"], D1, actor, 3)


def test_future_projection_updates_without_background_write(engine, minimum, actor, monkeypatch):
    import auto.history as history

    day = today()
    vehicle = save_vehicle(engine, minimum, actor)
    vehicle = change(
        engine, vehicle, actor, {"accounting_status": "Списано"}, day + timedelta(days=2)
    )
    assert vehicle.accounting_status == "На обліку"
    monkeypatch.setattr(history, "today", lambda: day + timedelta(days=3))
    assert get_vehicle(engine, vehicle.id).accounting_status == "Списано"
    assert list_vehicles(engine) == []


def test_mvo_periods_and_personnel_rights(engine, minimum, actor):
    person = add_person(engine, "Test", "Person", actor, personal_number=" AA  1 ")
    vehicle = save_vehicle(engine, minimum, actor)
    vehicle = change(engine, vehicle, actor, {"mvo_id": person.id}, D1)
    assert vehicle.mvo_id == person.id
    assert values_at(list_history(engine, vehicle.id), D1)["mvo_id"] == person.id
    with pytest.raises(BusinessError, match="Повтор особистого"):
        add_person(engine, "Other", "Person", actor, personal_number="aa 1")
    data = {f: getattr(person, f) for f in ("first_name", "last_name", "personal_number")}
    with pytest.raises(PermissionError):
        save_person(engine, data, actor, person_id=person.id, expected_version=1)
    renamed = save_person(
        engine,
        data | {"last_name": "Changed", "is_active": False},
        ADMIN,
        person_id=person.id,
        expected_version=1,
    )
    assert renamed.version == 2
    with pytest.raises(ConflictError):
        save_person(engine, data, ADMIN, person_id=person.id, expected_version=1)
    assert not list_people(engine, active_only=True)
    assert list_vehicles(engine, search="Changed")[0].id == vehicle.id


@pytest.mark.parametrize("field", ["vin", "chassis_number", "serial_number", "military_number"])
def test_duplicate_roles_reasons_and_audit(engine, minimum, actor, field):
    first = save_vehicle(engine, minimum | {field: "ABC", "vin_explanation": "Old VIN"}, actor)
    data = minimum | {field: " abc ", "vin_explanation": "Old VIN"}
    p = preview_vehicle(engine, data, actor)
    assert p["duplicates"][0]["matches"][0]["vehicle_id"] == first.id
    approval = {field: {"confirmed": True, "reason": "Separate source records"}}
    if field != "military_number":
        with pytest.raises(PermissionError):
            save_vehicle(
                engine, data, actor, preview_token=p["token"], confirmed=True, approvals=approval
            )
        p = preview_vehicle(engine, data, ADMIN)
    approver = actor if field == "military_number" else ADMIN
    with pytest.raises(BusinessError, match="причина"):
        save_vehicle(
            engine,
            data,
            approver,
            preview_token=p["token"],
            confirmed=True,
            approvals={field: {"confirmed": True, "reason": " "}},
        )
    saved = save_vehicle(
        engine, data, approver, preview_token=p["token"], confirmed=True, approvals=approval
    )
    assert len(list_vehicles(engine)) == 2
    assert any(
        e.action == "duplicate_approved" and e.role == approver.role
        for e in list_audit(engine, saved.id)
    )
    # An approved duplicate does not block unrelated edits of either vehicle.
    change(engine, saved, actor, {"color": "Green"}, None)
    change(engine, first, actor, {"color": "Black"}, None)


def test_historic_and_archive_identifiers_are_checked(engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum | {"chassis_number": "OLD"}, actor)
    change(engine, vehicle, actor, {"chassis_number": "NEW", "accounting_status": "Передано"}, D1)
    p = preview_vehicle(engine, minimum | {"chassis_number": " old "}, actor)
    assert p["duplicates"][0]["matches"][0]["vehicle_id"] == vehicle.id


def test_new_duplicate_invalidates_preview(engine, minimum, actor):
    data = minimum | {"military_number": "SAME"}
    p = preview_vehicle(engine, data, actor)
    save_vehicle(engine, data, actor)
    with pytest.raises(ConflictError, match="Перегляд застарів"):
        save_vehicle(engine, data, actor, preview_token=p["token"], confirmed=True)


def test_applied_document_blocks_and_rolls_back_all_fields(engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum, actor)
    register_document_lock(engine, vehicle.id, "WAYBILL-TEST-1", D2, actor)
    data = card(vehicle) | {"military_number": "NEW", "color": "Green"}
    p = preview_vehicle(
        engine, data, actor, vehicle_id=vehicle.id, expected_version=1, event_date=D1
    )
    assert p["blocked_documents"] == ["WAYBILL-TEST-1"]
    with pytest.raises(BusinessError, match="WAYBILL-TEST-1"):
        save_vehicle(
            engine,
            data,
            actor,
            vehicle_id=vehicle.id,
            expected_version=1,
            event_date=D1,
            preview_token=p["token"],
            confirmed=True,
        )
    saved = get_vehicle(engine, vehicle.id)
    assert saved.version == 1 and saved.color is None and saved.military_number is None
    assert all(r["valid_from"] is None for r in list_history(engine, vehicle.id))
    with Session(engine) as session:
        assert session.exec(select(VehicleDocumentLock)).first().snapshot["military_number"] is None
    change(engine, vehicle, actor, {"military_number": "AFTER"}, D3)


def test_viewer_cannot_preview_apply_person_or_lock(engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum, actor)
    with pytest.raises(PermissionError):
        preview_vehicle(engine, minimum, VIEWER)
    with pytest.raises(PermissionError):
        register_document_lock(engine, vehicle.id, "blocked", D1, VIEWER)
    with pytest.raises(PermissionError):
        save_person(engine, {"first_name": "A", "last_name": "B"}, VIEWER)


def test_delete_rights_tombstone_and_dependencies(engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum, actor)
    for user in (actor, VIEWER):
        with pytest.raises(PermissionError):
            delete_vehicle(
                engine, vehicle.id, user, expected_version=1, reason="Error", confirmed=True
            )
    with pytest.raises(BusinessError):
        delete_vehicle(engine, vehicle.id, ADMIN, expected_version=1, reason="Error")
    delete_vehicle(
        engine, vehicle.id, ADMIN, expected_version=1, reason="Duplicate mistake", confirmed=True
    )
    assert get_vehicle(engine, vehicle.id) is None
    assert not list_history(engine, vehicle.id)
    assert list_audit(engine, vehicle.id)[0].action == "delete"
    second = save_vehicle(engine, minimum, actor)
    register_document_lock(engine, second.id, "WB-DELETE", D1, actor)
    with pytest.raises(BusinessError, match="документи"):
        delete_vehicle(engine, second.id, ADMIN, expected_version=1, reason="Error", confirmed=True)
    archived = save_vehicle(engine, minimum | {"accounting_status": "Списано"}, actor)
    with pytest.raises(BusinessError, match="не видаляються"):
        delete_vehicle(
            engine, archived.id, ADMIN, expected_version=1, reason="Error", confirmed=True
        )


def test_duplicate_approval_tables_are_immutable(engine, minimum, actor):
    from sqlalchemy.exc import IntegrityError

    save_vehicle(engine, minimum | {"military_number": "SAME"}, actor)
    data = minimum | {"military_number": "SAME"}
    p = preview_vehicle(engine, data, actor)
    save_vehicle(
        engine,
        data,
        actor,
        preview_token=p["token"],
        confirmed=True,
        approvals={"military_number": {"confirmed": True, "reason": "Test"}},
    )
    with engine.begin() as connection:
        with pytest.raises(IntegrityError, match="immutable"):
            connection.exec_driver_sql("DELETE FROM duplicate_approvals")
    with Session(engine) as session:
        assert len(session.exec(select(DuplicateApproval)).all()) == 1
        assert len(session.exec(select(Vehicle)).all()) == 2
