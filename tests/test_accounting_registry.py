from datetime import date

import pytest
from sqlmodel import Session, select

from auto.accounting import add_reading, current_reading, list_norms, preview_norm, save_norm
from auto.history import delete_vehicle, register_document_lock
from auto.models import VehicleDocumentLock, VehicleReading
from auto.registry import CLONE_FIELDS, clone_draft, delete_preset, list_presets, save_preset
from auto.services import Actor, BusinessError, ConflictError, Role, get_vehicle, save_vehicle

ADMIN = Actor("admin", Role.ADMIN)
VIEWER = Actor("viewer", Role.VIEWER)


def norm(engine, vid, actor, day, rate=10):
    data = dict(valid_from=day, rate=rate, unit="л/100 км", order_number="Order 1", order_date=day)
    p = preview_norm(engine, vid, data, actor)
    save_norm(engine, vid, data, actor, preview_token=p["token"], confirmed=True)
    return p


def test_norms_dates_preview_reconcile_and_lock(engine, minimum, actor):
    v = save_vehicle(engine, minimum, actor)
    norm(engine, v.id, actor, date(2025, 6, 1), 12)
    norm(engine, v.id, actor, date(2025, 1, 1), 10)
    rows = list_norms(engine, v.id)
    assert rows[0].valid_to == date(2025, 6, 1)
    register_document_lock(
        engine, v.id, "fuel-doc", date(2025, 3, 1), actor, adjustment_percent=-10
    )
    data = dict(
        valid_from=date(2025, 2, 1),
        rate=11,
        unit="л/100 км",
        order_number="2",
        order_date=date(2025, 2, 1),
    )
    p = preview_norm(engine, v.id, data, actor)
    assert p["blocked_documents"] == ["fuel-doc"]
    with pytest.raises(BusinessError, match="fuel-doc"):
        save_norm(engine, v.id, data, actor, preview_token=p["token"], confirmed=True)
    with Session(engine) as s:
        snapshot = s.exec(select(VehicleDocumentLock)).first().snapshot
        assert snapshot["_fuel_norm"]["rate"] == 10
        assert snapshot["_fuel_adjustment_percent"] == -10
    assert not hasattr(get_vehicle(engine, v.id), "adjustment_percent")


def test_norm_requires_confirm_and_rejects_stale(engine, minimum, actor):
    v = save_vehicle(engine, minimum, actor)
    data = dict(
        valid_from=date(2025, 1, 1),
        rate=10,
        unit="л/100 км",
        order_number="1",
        order_date=date(2025, 1, 1),
    )
    p = preview_norm(engine, v.id, data, actor)
    with pytest.raises(ConflictError):
        save_norm(engine, v.id, data, actor, preview_token=p["token"])
    save_vehicle(engine, minimum | {"color": "changed"}, actor, vehicle_id=v.id, expected_version=1)
    with pytest.raises(ConflictError):
        save_norm(engine, v.id, data, actor, preview_token=p["token"], confirmed=True)
    assert list_norms(engine, v.id) == []


def test_optional_reading_zero_journal_dates_and_source(engine, minimum, actor):
    v = save_vehicle(engine, minimum, actor)
    assert current_reading(engine, v.id) is None
    assert add_reading(engine, v.id, {"value": None}, actor) is None
    with Session(engine) as s:
        assert s.exec(select(VehicleReading)).all() == []
    zero = add_reading(
        engine,
        v.id,
        dict(value=0, unit="км", measured_on=date(2025, 1, 1), source="initial"),
        actor,
    )
    assert current_reading(engine, v.id).value == 0
    journal = add_reading(
        engine,
        v.id,
        dict(value=250, unit="км", measured_on=date(2025, 2, 1), source="journal", confirmed=True),
        actor,
        kind="journal",
        external_id="journal-1",
    )
    assert current_reading(engine, v.id).id == journal.id
    assert current_reading(engine, v.id, date(2025, 1, 1)).id == zero.id
    assert current_reading(engine, v.id).confirmed
    with pytest.raises(BusinessError):
        add_reading(
            engine,
            v.id,
            dict(value=1, unit="мотогодини", measured_on=date(2025, 3, 1), source="wrong"),
            actor,
        )
    with pytest.raises(BusinessError):
        delete_vehicle(engine, v.id, ADMIN, expected_version=1, reason="error", confirmed=True)


def test_missing_date_and_no_counter(engine, minimum, actor):
    from pydantic import ValidationError

    v = save_vehicle(engine, minimum, actor)
    with pytest.raises(ValidationError):
        add_reading(engine, v.id, dict(value=10, unit="км", source="test"), actor)
    trailer = save_vehicle(engine, minimum | {"work_unit": "без лічильника"}, actor)
    assert current_reading(engine, trailer.id) is None
    with pytest.raises(PermissionError):
        add_reading(engine, v.id, {"value": None}, VIEWER)


def test_clone_whitelist_unique_id_and_no_independent_records(engine, minimum, actor):
    v = save_vehicle(
        engine,
        minimum
        | {
            "military_number": "SECRET",
            "chassis_number": "FRAME",
            "current_location": "place",
            "engine_number": "engine-id",
            "color": "Green",
        },
        actor,
    )
    draft = clone_draft(engine, v.id, actor)
    assert draft["color"] == "Green"
    for f in (
        "military_number",
        "chassis_number",
        "current_location",
        "engine_number",
        "mvo_id",
        "unit_id",
    ):
        assert draft[f] is None
    clone = save_vehicle(engine, draft, actor)
    assert clone.id != v.id
    assert current_reading(engine, clone.id) is None and list_norms(engine, clone.id) == []
    assert "vin" not in CLONE_FIELDS


def test_filter_presets_private_and_viewer_allowed(engine, actor):
    save_preset(engine, "Mine", {"registry_search": "test", "registry_columns": ["ID"]}, actor)
    save_preset(engine, "Viewer", {"include_archived": True}, VIEWER)
    row = list_presets(engine, actor)[0]
    assert len(list_presets(engine, actor)) == len(list_presets(engine, VIEWER)) == 1
    with pytest.raises(BusinessError):
        delete_preset(engine, row.id, VIEWER)
    save_preset(engine, "Mine", {"registry_search": "updated"}, actor)
    assert list_presets(engine, actor)[0].settings == {"registry_search": "updated"}
    delete_preset(engine, row.id, actor)
    assert list_presets(engine, actor) == []
