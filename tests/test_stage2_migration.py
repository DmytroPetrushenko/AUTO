import sqlite3

from alembic import command
from sqlmodel import Session, select

from auto.db import alembic_config, make_engine, migrate, verified_backup
from auto.models import VehicleHistory


def populated_stage1(path):
    command.upgrade(alembic_config(path), "0003")
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO vehicle_brands VALUES (1,'Test','test',1)")
        c.execute("INSERT INTO vehicle_models VALUES (1,'Model','model',1,1)")
        c.execute(
            "INSERT INTO personnel (id,first_name,last_name,personal_number,is_active) "
            "VALUES (1,'Test','Person',' AA  1 ',1)"
        )
        # Reuse the stage-1 mapper columns without new stage-2 columns.
        c.execute("""INSERT INTO vehicles
            (id,brand_id,model_id,vehicle_type_id,support_service_id,work_unit,
             accounting_status,technical_condition,technical_category,is_operated,
             manufacture_precision,engine_not_applicable,created_at,created_by,
             updated_at,updated_by,version,military_number,military_number_normalized)
            VALUES ('test-id',1,1,6,1,'км','На обліку','Не визначено','III',0,
                    'невідомо',0,'2026-01-01','test','2026-01-01','test',1,'AA 1','aa 1')""")


def test_populated_copy_upgrade_preserves_unknown_dates(tmp_path):
    source = tmp_path / "stage1.sqlite3"
    populated_stage1(source)
    copy = verified_backup(source, tmp_path / "copy")
    backup = migrate(copy)
    assert backup.exists()
    with sqlite3.connect(source) as c:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0003"
    with sqlite3.connect(copy) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert c.execute("SELECT personal_number_normalized,version FROM personnel").fetchone() == (
            "aa 1",
            1,
        )
        assert c.execute(
            "SELECT id,military_number,technical_category FROM vehicles"
        ).fetchone() == ("test-id", "AA 1", "III")
    engine = make_engine(copy)
    try:
        with Session(engine) as session:
            rows = session.exec(select(VehicleHistory)).all()
            assert len(rows) == 8
            assert all(r.valid_from is None and r.valid_to is None and r.is_initial for r in rows)
            assert next(r for r in rows if r.kind == "military_number").value == {
                "military_number": "AA 1"
            }
    finally:
        engine.dispose()


def test_migration_rejects_existing_duplicate_personal_numbers(tmp_path):
    import pytest

    path = tmp_path / "duplicate.sqlite3"
    populated_stage1(path)
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO personnel (id,first_name,last_name,personal_number,is_active) "
            "VALUES (2,'Other','Person','aa 1',1)"
        )
    with pytest.raises(RuntimeError, match="Duplicate personnel"):
        migrate(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT COUNT(*) FROM personnel").fetchone()[0] == 2
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0003"
