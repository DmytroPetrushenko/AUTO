from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from auto.services import get_vehicle, list_vehicles, save_vehicle


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
    assert len(ui.dataframe[0].value) == 1
    ui.checkbox(key="include_archived").check().run()
    assert len(ui.dataframe[0].value) == 2
    ui.text_input(key="registry_search").set_value("test 001").run()
    assert len(ui.dataframe[0].value) == 1
    assert "Військовий номер" in ui.dataframe[0].value.columns


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
