from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from auto.services import Actor, Role, get_vehicle, list_vehicles, save_vehicle


@pytest.fixture
def ui(engine, minimum, monkeypatch):
    monkeypatch.setenv("AUTO_DB_PATH", engine.url.database)
    monkeypatch.setenv("AUTO_ROLE", "operator")
    monkeypatch.setenv("AUTO_USER_ID", "ui-test")
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    at.run()
    assert not at.exception
    return at


def open_new(at, minimum):
    at.button(key="new_vehicle").click().run()
    assert not at.exception
    for field in ("brand_id", "model_id", "vehicle_type_id", "support_service_id"):
        at.selectbox(key=f"card_{field}").set_value(minimum[field]).run()
        assert not at.exception
    at.selectbox(key="card_work_unit").set_value("км").run()
    return at


def test_ui_create_then_edit(ui, engine, minimum):
    at = open_new(ui, minimum)
    assert len(at.tabs) == 7
    at.text_input(key="card_military_number").set_value("UI 101").run()
    at.text_input(key="card_color").set_value("Зелений").run()
    assert any("Є незбережені" in w.value for w in at.warning)
    # Widgets on different tabs share a single unsaved draft.
    assert at.text_input(key="card_military_number").value == "UI 101"
    assert list_vehicles(engine) == []
    at.button(key="save_card").click().run()
    assert not at.exception
    vehicle = list_vehicles(engine)[0]
    assert vehicle.military_number == "UI 101"
    assert vehicle.color == "Зелений"
    assert vehicle.engine_power_kw is None
    at.text_input(key="card_current_location").set_value("Тестове місце").run()
    at.button(key="save_card").click().run()
    assert not at.exception
    updated = get_vehicle(engine, vehicle.id)
    assert updated.version == 2
    assert updated.current_location == "Тестове місце"


def test_cancel_restores_last_saved_card(ui, engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum | {"color": "Чорний"}, actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    ui.text_input(key="card_color").set_value("Білий").run()
    ui.button(key="cancel_card").click().run()
    assert not ui.exception
    assert ui.text_input(key="card_color").value == "Чорний"
    assert get_vehicle(engine, vehicle.id).color == "Чорний"


def test_dirty_navigation_stay_then_discard(ui, engine, minimum):
    at = open_new(ui, minimum)
    at.text_input(key="card_color").set_value("Чернетка").run()
    at.button(key="nav_dict").click().run()
    assert at.session_state["page"] == "card"
    at.button(key="pending_stay").click().run()
    assert at.text_input(key="card_color").value == "Чернетка"
    at.button(key="back_registry").click().run()
    at.button(key="pending_discard").click().run()
    assert not at.exception
    assert at.session_state["page"] == "registry"
    assert list_vehicles(engine) == []
    at.button(key="new_vehicle").click().run()
    assert at.text_input(key="card_color").value == ""


def test_save_before_navigation(ui, engine, minimum):
    at = open_new(ui, minimum)
    at.button(key="back_registry").click().run()
    at.button(key="pending_save").click().run()
    assert not at.exception
    assert at.session_state["page"] == "registry"
    assert len(list_vehicles(engine)) == 1


def test_invalid_card_stays_unsaved(ui, engine, minimum):
    at = open_new(ui, minimum)
    at.number_input(key="card_gross_weight_kg").set_value(1.0).run()
    at.number_input(key="card_unloaded_weight_kg").set_value(2.0).run()
    at.button(key="save_card").click().run()
    assert not at.exception
    assert len(at.error) == 1
    assert list_vehicles(engine) == []
    assert at.number_input(key="card_unloaded_weight_kg").value == 2.0


def test_registry_search_archive_and_columns(ui, engine, minimum, actor):
    save_vehicle(engine, minimum | {"military_number": "TEST 001"}, actor)
    save_vehicle(engine, minimum | {"accounting_status": "Списано"}, actor)
    ui.run()
    assert len(ui.table[0].value) == 1
    ui.checkbox(key="include_archived").check().run()
    assert len(ui.table[0].value) == 2
    ui.text_input(key="registry_search").set_value("test 001").run()
    assert len(ui.table[0].value) == 1
    assert "Військовий номер" in ui.table[0].value.columns


def test_viewer_ui_read_only(ui, engine, minimum, actor, monkeypatch):
    save_vehicle(engine, minimum, actor)
    monkeypatch.setenv("AUTO_ROLE", "viewer")
    ui.run()
    assert ui.button(key="new_vehicle").disabled
    ui.button(key="open_vehicle").click().run()
    assert not ui.exception
    assert ui.button(key="save_card").disabled
    assert ui.text_input(key="card_color").disabled


def test_plus_adds_brand_without_losing_draft(ui, minimum):
    at = open_new(ui, minimum)
    at.text_input(key="card_color").set_value("Зелений").run()
    at.text_input(key="plus_brand_id").set_value("Додана марка").run()
    at.button(key="add_brand_id").click().run()
    assert not at.exception
    assert at.text_input(key="card_color").value == "Зелений"
    assert at.selectbox(key="card_model_id").value is None
    assert at.selectbox(key="card_brand_id").value != minimum["brand_id"]


def test_uninitialized_app_does_not_create_database(tmp_path, monkeypatch):
    path = tmp_path / "does-not-exist.sqlite3"
    monkeypatch.setenv("AUTO_DB_PATH", str(path))
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    at.run()
    assert not at.exception
    assert not path.exists()
    assert "БД ще не створена" in at.info[0].value


def test_conflict_keeps_unsaved_draft(ui, engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum, actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    ui.text_input(key="card_color").set_value("Моя чернетка").run()
    save_vehicle(
        engine,
        minimum | {"color": "Інший оператор"},
        actor,
        vehicle_id=vehicle.id,
        expected_version=1,
    )
    ui.button(key="save_card").click().run()
    assert not ui.exception
    assert "вже змінено" in ui.error[0].value
    assert ui.text_input(key="card_color").value == "Моя чернетка"
    assert get_vehicle(engine, vehicle.id).color == "Інший оператор"


def test_dictionary_ui_adds_value(ui, engine):
    ui.button(key="nav_dict").click().run()
    assert not ui.exception
    ui.text_input[0].set_value("Марка через UI")
    next(b for b in ui.button if b.label == "Додати значення").click().run()
    assert not ui.exception
    from auto.services import list_lookup

    assert "Марка через UI" in [e.name for e in list_lookup(engine, "vehicle_brands")]


def test_service_has_no_default_on_new_card(ui):
    ui.button(key="new_vehicle").click().run()
    assert ui.selectbox(key="card_support_service_id").value is None
    assert ui.selectbox(key="card_work_unit").value is None


def test_dated_mvo_requires_preview_and_confirmation(ui, engine, minimum, actor):
    from datetime import date

    from auto.history import list_history
    from auto.services import add_person

    person = add_person(engine, "UI", "Responsible", actor)
    vehicle = save_vehicle(engine, minimum, actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    ui.selectbox(key="card_mvo_id").set_value(person.id).run()
    ui.button(key="save_card").click().run()
    assert "дата події" in ui.error[0].value
    assert get_vehicle(engine, vehicle.id).mvo_id is None
    ui.date_input(key="card_event_date").set_value(date(2025, 1, 1)).run()
    ui.button(key="save_card").click().run()
    assert not ui.exception
    assert get_vehicle(engine, vehicle.id).mvo_id is None
    ui.button(key="card_commit").click().run()
    assert "підтвердження" in ui.error[0].value
    ui.checkbox(key="card_confirmed").check().run()
    ui.button(key="card_commit").click().run()
    assert not ui.exception
    assert get_vehicle(engine, vehicle.id).mvo_id == person.id
    assert len([r for r in list_history(engine, vehicle.id) if r["kind"] == "mvo"]) == 2


def test_military_duplicate_reason_and_confirmation_ui(ui, engine, minimum, actor):
    save_vehicle(engine, minimum | {"military_number": "SAME"}, actor)
    at = open_new(ui, minimum)
    at.text_input(key="card_military_number").set_value(" same ").run()
    at.button(key="save_card").click().run()
    assert not at.exception
    assert len(list_vehicles(engine)) == 1
    at.checkbox(key="card_confirmed").check().run()
    at.checkbox(key="card_approve_military_number").check().run()
    at.button(key="card_commit").click().run()
    assert "причина" in at.error[0].value
    at.text_input(key="card_reason_military_number").set_value("Different original cards").run()
    at.button(key="card_commit").click().run()
    assert not at.exception
    assert len(list_vehicles(engine)) == 2


def test_physical_duplicate_operator_blocked_admin_approves(
    ui, engine, minimum, actor, monkeypatch
):
    save_vehicle(engine, minimum | {"chassis_number": "FRAME"}, actor)
    at = open_new(ui, minimum)
    at.text_input(key="card_chassis_number").set_value("FRAME").run()
    at.button(key="save_card").click().run()
    assert at.button(key="card_commit").disabled
    monkeypatch.setenv("AUTO_ROLE", "admin")
    at.run()
    at.button(key="save_card").click().run()
    assert not at.button(key="card_commit").disabled
    at.checkbox(key="card_approve_chassis_number").check().run()
    at.text_input(key="card_reason_chassis_number").set_value("Confirmed archive source").run()
    at.checkbox(key="card_confirmed").check().run()
    at.button(key="card_commit").click().run()
    assert not at.exception
    assert len(list_vehicles(engine)) == 2


def test_history_edit_ui_moves_date_atomically(ui, engine, minimum, actor):
    from datetime import date

    from auto.history import list_history, preview_vehicle

    vehicle = save_vehicle(engine, minimum, actor)
    data = minimum | {"military_number": "DATED"}
    p = preview_vehicle(
        engine, data, actor, vehicle_id=vehicle.id, expected_version=1, event_date=date(2025, 3, 1)
    )
    save_vehicle(
        engine,
        data,
        actor,
        vehicle_id=vehicle.id,
        expected_version=1,
        event_date=date(2025, 3, 1),
        preview_token=p["token"],
        confirmed=True,
    )
    ui.run()
    ui.button(key="open_vehicle").click().run()
    ui.selectbox(key="history_kind").set_value("military_number").run()
    event = next(
        r
        for r in list_history(engine, vehicle.id)
        if r["kind"] == "military_number" and r["valid_from"] == date(2025, 3, 1)
    )
    ui.selectbox(key="history_event_military_number").set_value(event["id"]).run()
    ui.date_input(key=f"history_date_{event['id']}_2").set_value(date(2025, 2, 1))
    ui.button(key="history_preview_button").click().run()
    assert not ui.exception
    assert get_vehicle(engine, vehicle.id).version == 2
    ui.checkbox(key="history_confirmed").check().run()
    ui.button(key="history_commit").click().run()
    assert not ui.exception
    rows = [r for r in list_history(engine, vehicle.id) if r["kind"] == "military_number"]
    assert rows[0]["valid_to"] == rows[1]["valid_from"] == date(2025, 2, 1)
    assert get_vehicle(engine, vehicle.id).version == 3


def test_document_conflict_ui_identifies_reference(ui, engine, minimum, actor):
    from datetime import date

    from auto.history import register_document_lock

    vehicle = save_vehicle(engine, minimum, actor)
    register_document_lock(engine, vehicle.id, "UI-WAYBILL-001", date(2025, 6, 1), actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    ui.text_input(key="card_military_number").set_value("BLOCKED").run()
    ui.date_input(key="card_event_date").set_value(date(2025, 1, 1)).run()
    ui.button(key="save_card").click().run()
    assert not ui.exception
    assert ui.button(key="card_commit").disabled
    assert any("UI-WAYBILL-001" in e.value for e in ui.error)
    assert get_vehicle(engine, vehicle.id).military_number is None


def test_personnel_ui_operator_create_admin_deactivate(ui, engine, monkeypatch):
    from auto.personnel import list_people

    ui.button(key="nav_dict").click().run()
    ui.selectbox(key="dictionary_kind").set_value("personnel").run()
    ui.text_input(key="person_first_name_None").set_value("Test")
    ui.text_input(key="person_last_name_None").set_value("Responsible")
    next(b for b in ui.button if b.label == "Додати особу").click().run()
    assert not ui.exception
    person = list_people(engine)[0]
    ui.selectbox(key="person_edit").set_value(person.id).run()
    assert next(b for b in ui.button if b.label == "Зберегти особу").disabled
    monkeypatch.setenv("AUTO_ROLE", "admin")
    ui.run()
    ui.text_input(key=f"person_last_name_{person.id}").set_value("Renamed")
    next(c for c in ui.checkbox if c.label == "Активна особа").uncheck()
    next(b for b in ui.button if b.label == "Зберегти особу").click().run()
    assert not ui.exception
    person = list_people(engine)[0]
    assert person.last_name == "Renamed" and not person.is_active and person.version == 2


def test_pending_navigation_waits_for_dated_confirmation(ui, engine, minimum, actor):
    from datetime import date

    vehicle = save_vehicle(engine, minimum, actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    ui.text_input(key="card_military_number").set_value("DATED").run()
    ui.date_input(key="card_event_date").set_value(date(2025, 1, 1)).run()
    ui.button(key="back_registry").click().run()
    ui.button(key="pending_save").click().run()
    assert get_vehicle(engine, vehicle.id).military_number is None
    assert ui.session_state["page"] == "card"
    ui.checkbox(key="card_confirmed").check().run()
    ui.button(key="card_commit").click().run()
    assert not ui.exception
    assert ui.session_state["page"] == "registry"
    assert get_vehicle(engine, vehicle.id).military_number == "DATED"


def test_admin_deletes_erroneous_card_ui(ui, engine, minimum, actor, monkeypatch):
    vehicle = save_vehicle(engine, minimum, actor)
    monkeypatch.setenv("AUTO_ROLE", "admin")
    ui.run()
    ui.button(key="open_vehicle").click().run()
    ui.text_input(key="delete_reason").set_value("Erroneous test entry").run()
    ui.checkbox(key="delete_confirmed").check().run()
    ui.button(key="delete_card").click().run()
    assert not ui.exception
    assert ui.session_state["page"] == "registry"
    assert get_vehicle(engine, vehicle.id) is None


def test_history_preview_navigation_requires_explicit_confirmation(ui, engine, minimum, actor):
    from datetime import date

    from auto.history import list_history

    vehicle = save_vehicle(engine, minimum, actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    ui.selectbox(key="history_kind").set_value("military_number").run()
    event = next(r for r in list_history(engine, vehicle.id) if r["kind"] == "military_number")
    ui.date_input(key=f"history_date_{event['id']}_1").set_value(date(2025, 1, 1))
    ui.text_input(key=f"history_value_military_number_{event['id']}_1").set_value("HISTORY")
    ui.button(key="history_preview_button").click().run()
    ui.button(key="back_registry").click().run()
    ui.button(key="pending_save").click().run()
    assert ui.session_state["page"] == "card"
    assert get_vehicle(engine, vehicle.id).military_number is None
    ui.checkbox(key="history_confirmed").check().run()
    ui.button(key="history_commit").click().run()
    assert not ui.exception
    assert ui.session_state["page"] == "registry"
    assert get_vehicle(engine, vehicle.id).military_number == "HISTORY"


def test_notes_equipment_and_document_download_ui(ui, engine, minimum, actor):
    from auto.records import list_equipment, list_notes, save_document

    vehicle = save_vehicle(engine, minimum, actor)
    save_document(engine, vehicle.id, {"kind": "Акт"}, [("test.pdf", b"%PDF-1.4\n%%EOF")], actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    assert not ui.exception
    next(t for t in ui.text_area if t.label == "Текст примітки").set_value("UI note")
    next(b for b in ui.button if b.label == "Зберегти примітку").click().run()
    assert not ui.exception
    assert list_notes(engine, vehicle.id, actor)[0].text == "UI note"
    assert next(t for t in ui.text_area if t.label == "Текст примітки").value == ""
    next(t for t in ui.text_input if t.label == "Назва обладнання").set_value("UI Radio")
    next(b for b in ui.button if b.label == "Зберегти обладнання").click().run()
    assert not ui.exception
    assert list_equipment(engine, vehicle.id, actor)[0].name == "UI Radio"


def test_accounting_norm_and_clone_ui(ui, engine, minimum, actor):
    from datetime import date

    from auto.accounting import list_norms

    vehicle = save_vehicle(engine, minimum | {"military_number": "ORIGINAL"}, actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    next(x for x in ui.date_input if x.label == "Початок дії норми").set_value(date(2025, 1, 1))
    next(x for x in ui.number_input if x.label == "Базова норма").set_value(10.0)
    next(x for x in ui.text_input if x.label == "Номер наказу").set_value("1")
    next(x for x in ui.date_input if x.label == "Дата наказу").set_value(date(2025, 1, 1))
    next(b for b in ui.button if b.label == "Переглянути норму").click().run()
    assert not ui.exception
    assert list_norms(engine, vehicle.id) == []
    ui.checkbox(key="norm_confirm_" + vehicle.id).check().run()
    next(b for b in ui.button if b.label == "Підтвердити норму").click().run()
    assert not ui.exception
    assert list_norms(engine, vehicle.id)[0].rate == 10
    ui.button(key="clone_vehicle").click().run()
    assert not ui.exception
    assert ui.text_input(key="card_military_number").value == ""
    assert ui.session_state["vehicle_id"] is None


def test_personal_filter_ui(ui, engine, minimum, actor):
    from auto.registry import list_presets

    save_vehicle(engine, minimum, actor)
    ui.run()
    ui.text_input(key="registry_search").set_value("test").run()
    ui.text_input(key="preset_name").set_value("My view").run()
    next(b for b in ui.button if b.label == "Зберегти поточний набір").click().run()
    assert not ui.exception
    saved = list_presets(engine, Actor("ui-test", Role.OPERATOR))[0]
    ui.text_input(key="registry_search").set_value("").run()
    ui.selectbox(key="preset_selected").set_value(saved.id).run()
    next(b for b in ui.button if b.label == "Застосувати набір").click().run()
    assert not ui.exception
    assert ui.text_input(key="registry_search").value == "test"


def test_excel_page_and_explicit_full_export_ui(ui, engine, minimum, actor):
    save_vehicle(engine, minimum, actor)
    ui.run()
    next(b for b in ui.button if b.label == "Підготувати повний експорт").click().run()
    assert not ui.exception and ui.session_state["export_bytes"]
    ui.button(key="nav_excel").click().run()
    assert not ui.exception
    assert any("Excel" in h.value for h in ui.header)


def test_registry_explicit_columns_and_sort_ui(ui, engine, minimum, actor):
    save_vehicle(engine, minimum | {"military_number": "A"}, actor)
    save_vehicle(engine, minimum | {"military_number": "Z"}, actor)
    ui.run()
    ui.multiselect(key="registry_columns").set_value(["Тип", "Військовий номер"]).run()
    ui.selectbox(key="registry_sort").set_value("Військовий номер").run()
    ui.checkbox(key="registry_reverse").check().run()
    frame = ui.table[0].value
    assert list(frame.columns) == ["Тип", "Військовий номер"]
    assert frame["Військовий номер"].tolist() == ["Z", "A"]


def test_excel_import_preview_confirm_and_mapping_change_ui(ui, engine, minimum, monkeypatch):
    from types import SimpleNamespace

    from tests.test_excel import book, source

    content, _ = book([source(engine, minimum) | {"military_number": "UI-XLSX"}])
    monkeypatch.setattr(
        "streamlit.file_uploader",
        lambda *a, **kw: SimpleNamespace(name="test.xlsx", getvalue=lambda: content),
    )
    ui.run()
    ui.button(key="nav_excel").click().run()
    next(b for b in ui.button if b.label == "Побудувати попередній перегляд").click().run()
    assert not ui.exception and not list_vehicles(engine)
    number_mapping = next(s for s in ui.selectbox if (s.key or "").endswith("_military_number"))
    original_column = number_mapping.value
    number_mapping.set_value("").run()
    assert not any(b.label == "Виконати підтверджений імпорт" for b in ui.button)
    ui.selectbox(key=number_mapping.key).set_value(original_column).run()
    next(b for b in ui.button if b.label == "Виконати підтверджений імпорт").click().run()
    assert not list_vehicles(engine) and any("підтвердження" in e.value for e in ui.error)
    ui.checkbox(key="import_confirmed").check().run()
    next(b for b in ui.button if b.label == "Виконати підтверджений імпорт").click().run()
    assert not ui.exception and list_vehicles(engine)[0].military_number == "UI-XLSX"


def test_independent_draft_navigation_and_cancel_ui(ui, engine, minimum, actor):
    vehicle = save_vehicle(engine, minimum, actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    next(t for t in ui.text_area if t.label == "Текст примітки").set_value("unsaved").run()
    ui.button(key="back_registry").click().run()
    assert ui.session_state["page"] == "card" and ui.session_state["pending"]
    ui.button(key="pending_save").click().run()
    assert ui.session_state["page"] == "card"
    ui.button(key="cancel_aux").click().run()
    ui.button(key="pending_save").click().run()
    assert not ui.exception and ui.session_state["page"] == "registry"
    assert get_vehicle(engine, vehicle.id)


def test_note_ui_uses_opened_version_and_preserves_conflict_draft(ui, engine, minimum, actor):
    from auto.records import list_notes, save_note

    vehicle = save_vehicle(engine, minimum, actor)
    note = save_note(engine, vehicle.id, "original", actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    ui.selectbox(key="note_edit").set_value(note.id).run()
    next(t for t in ui.text_area if t.label == "Текст примітки").set_value("my draft").run()
    save_note(engine, vehicle.id, "other editor", actor, note_id=note.id, expected_version=1)
    next(b for b in ui.button if b.label == "Зберегти примітку").click().run()
    assert not ui.exception and ui.error
    assert list_notes(engine, vehicle.id, actor)[0].text == "other editor"
    assert next(t for t in ui.text_area if t.label == "Текст примітки").value == "my draft"


def test_editor_reload_preserves_legacy_draft(ui, engine, minimum, actor):
    save_vehicle(engine, minimum, actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    ui.session_state["aux_editors"] = {
        "legacy": {
            "version": None,
            "initial": {"text": ""},
            "draft": {"text": "unsaved"},
        }
    }
    ui.run()
    assert not ui.exception
    assert any("Є незбережені" in warning.value for warning in ui.warning)


def test_whole_card_confirmed_save_includes_note_and_equipment(ui, engine, minimum, actor):
    from auto.records import list_equipment, list_notes

    v = save_vehicle(engine, minimum, actor)
    ui.run()
    ui.button(key="open_vehicle").click().run()
    next(t for t in ui.text_area if t.label == "Текст примітки").set_value("atomic note").run()
    next(t for t in ui.text_input if t.label == "Назва обладнання").set_value("atomic radio").run()
    ui.text_input(key="card_color").set_value("Green").run()
    ui.button(key="save_card").click().run()
    assert not ui.exception and not list_notes(engine, v.id, actor)
    assert get_vehicle(engine, v.id).color is None
    ui.checkbox(key="card_confirmed").check().run()
    ui.button(key="card_commit").click().run()
    assert not ui.exception
    assert list_notes(engine, v.id, actor)[0].text == "atomic note"
    assert list_equipment(engine, v.id, actor)[0].name == "atomic radio"
    assert get_vehicle(engine, v.id).color == "Green"
