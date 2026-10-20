from test_excel import book, source

from auto.excel import apply_import, preview_import
from auto.services import list_vehicles, save_vehicle


def test_all_row_errors_include_numeric_identifier_and_unsupported_condition(
    engine, minimum, actor
):
    content, mapping = book(
        [
            source(engine, minimum)
            | {
                "chassis_number": 123,
                "technical_condition": "waiting for repair",
                "seats": -2,
            }
        ]
    )
    row = preview_import(engine, content, mapping, actor)["rows"][0]
    assert any("chassis_number: числовий" in error for error in row["errors"])
    assert any("technical_condition:" in error for error in row["errors"])
    assert any("seats:" in error for error in row["errors"])
    assert row["data"] is None


def test_invalid_rows_still_show_database_and_incoming_identifier_matches(engine, minimum, actor):
    v = save_vehicle(engine, minimum | {"military_number": "SAME"}, actor)
    data = source(engine, minimum)
    content, mapping = book(
        [
            data | {"military_number": "same", "chassis_number": 123},
            data | {"military_number": "SAME", "technical_condition": "unknown source label"},
        ]
    )
    p = preview_import(engine, content, mapping, actor)
    for row in p["rows"]:
        assert row["errors"] and row["duplicates"][0]["matches"][0]["vehicle_id"] == v.id
        assert "military_number" in row["duplicate_checked_fields"]
    assert p["rows"][1]["incoming"] == [{"field": "military_number", "source_row": 2}]
    assert "chassis_number" in p["rows"][0]["duplicate_unchecked_fields"]
    assert all(r["status"] == "skipped" for r in apply_import(engine, p, actor, confirmed=True))
    assert len(list_vehicles(engine)) == 1


def test_explicit_source_policy_blocks_silent_accounting_default_and_keeps_provenance(
    engine, minimum, actor
):
    content, mapping = book([source(engine, minimum)])
    normal = preview_import(engine, content, mapping, actor)
    assert normal["rows"][0]["applied_defaults"]["accounting_status"] == "На обліку"
    strict = preview_import(
        engine,
        content,
        mapping,
        actor,
        required_explicit=["accounting_status"],
        source_name="synthetic.xlsx",
    )
    row = strict["rows"][0]
    assert any("accounting_status: потрібне явне" in error for error in row["errors"])
    assert row["source"] == {"file": "synthetic.xlsx", "sheet": "AUTO", "row": 2}
    assert row["source_cells"]["brand"]["cell"] == "A2"
    assert apply_import(engine, strict, actor, confirmed=True)[0]["status"] == "skipped"
    assert not list_vehicles(engine)


def test_numeric_identifier_never_silently_matches_text_until_reviewed(engine, minimum, actor):
    v = save_vehicle(engine, minimum | {"chassis_number": "00123"}, actor)
    content, mapping = book([source(engine, minimum) | {"chassis_number": 123}])
    p = preview_import(engine, content, mapping, actor)
    assert p["rows"][0]["duplicates"] == []
    assert p["rows"][0]["duplicate_unchecked_fields"]["chassis_number"]
    p = preview_import(
        engine, content, mapping, actor, overrides={"2": {"chassis_number": "00123"}}
    )
    assert p["rows"][0]["duplicates"][0]["matches"][0]["vehicle_id"] == v.id
