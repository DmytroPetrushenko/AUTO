from datetime import date
from pathlib import Path

import pytest

from auto.accounting import current_reading, list_norms
from auto.card_bundle import preview_bundle, save_bundle
from auto.records import list_documents, list_equipment, list_notes, save_note
from auto.services import Actor, ConflictError, Role, get_vehicle, list_audit, save_vehicle


def children():
    return [
        {"kind": "document", "values": {"kind": "Act"}, "files": [("a.pdf", b"%PDF-1.4\n%%EOF")]},
        {"kind": "equipment", "values": {"name": "Radio", "quantity": 2}},
        {"kind": "note", "values": {"text": "note", "is_hidden": False}},
        {
            "kind": "reading",
            "values": {
                "value": 0,
                "measured_on": date(2025, 1, 1),
                "source": "test",
                "unit": "км",
                "confirmed": False,
            },
        },
        {
            "kind": "norm",
            "values": {
                "valid_from": date(2025, 1, 1),
                "rate": 10,
                "unit": "л/100 км",
                "order_number": "1",
                "order_date": date(2025, 1, 1),
            },
        },
    ]


def test_complete_card_one_transaction(engine, minimum, actor):
    v = save_vehicle(engine, minimum, actor)
    data = minimum | {"color": "Green"}
    ops = children()
    plan = preview_bundle(engine, data, ops, actor, vehicle_id=v.id, expected_version=1)
    assert get_vehicle(engine, v.id).color is None and not list_documents(engine, v.id, actor)
    with pytest.raises(ValueError):
        save_bundle(
            engine,
            data,
            ops,
            actor,
            vehicle_id=v.id,
            expected_version=1,
            preview_token=plan["token"],
        )
    result = save_bundle(
        engine,
        data,
        ops,
        actor,
        vehicle_id=v.id,
        expected_version=1,
        preview_token=plan["token"],
        confirmed=True,
    )
    assert result.version == 3 and result.color == "Green"
    assert len(list_documents(engine, v.id, actor)) == 1
    assert list_equipment(engine, v.id, actor)[0].quantity == 2
    assert list_notes(engine, v.id, actor)[0].text == "note"
    assert list_norms(engine, v.id)[0].rate == 10
    assert current_reading(engine, v.id).value == 0


def test_bundle_late_failure_rolls_back_card_records_files_and_audit(
    engine, minimum, actor, monkeypatch
):
    v = save_vehicle(engine, minimum, actor)
    data, ops = minimum | {"color": "Green"}, children()
    before = len(list_audit(engine, v.id))
    p = preview_bundle(engine, data, ops, actor, vehicle_id=v.id, expected_version=1)

    def fail(*args, **kwargs):
        raise ValueError("synthetic later operation failure")

    monkeypatch.setattr("auto.card_bundle.save_note", fail)
    with pytest.raises(ValueError, match="synthetic"):
        save_bundle(
            engine,
            data,
            ops,
            actor,
            vehicle_id=v.id,
            expected_version=1,
            preview_token=p["token"],
            confirmed=True,
        )
    saved = get_vehicle(engine, v.id)
    assert saved.color is None and saved.version == 1
    assert len(list_audit(engine, v.id)) == before
    assert not list_documents(engine, v.id, actor) and not list_equipment(engine, v.id, actor)
    assert not list((Path(engine.url.database).parent / "attachments").iterdir())


def test_bundle_conflict_and_rights_preserve_other_changes(engine, minimum, actor):
    v = save_vehicle(engine, minimum, actor)
    note = save_note(engine, v.id, "original", actor)
    ops = [
        {
            "kind": "note",
            "id": note.id,
            "version": 1,
            "values": {"text": "draft", "is_hidden": False},
        }
    ]
    p = preview_bundle(engine, minimum, ops, actor, vehicle_id=v.id, expected_version=1)
    save_note(engine, v.id, "other", actor, note_id=note.id, expected_version=1)
    with pytest.raises(ConflictError):
        save_bundle(
            engine,
            minimum,
            ops,
            actor,
            vehicle_id=v.id,
            expected_version=1,
            preview_token=p["token"],
            confirmed=True,
        )
    assert list_notes(engine, v.id, actor)[0].text == "other"
    with pytest.raises(PermissionError):
        preview_bundle(
            engine, minimum, [], Actor("reader", Role.VIEWER), vehicle_id=v.id, expected_version=1
        )
