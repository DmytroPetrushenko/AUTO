import sqlite3

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from auto.constants import VEHICLE_TYPES
from auto.db import alembic_config, make_engine, migrate, verified_backup
from auto.models import Vehicle, VehicleBrand
from auto.services import list_lookup, save_vehicle


def test_migration_idempotent_and_schema_matches(engine):
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
        assert compare_metadata(MigrationContext.configure(connection), SQLModel.metadata) == []
    assert set(x.name for x in list_lookup(engine, "vehicle_types")) == set(VEHICLE_TYPES)
    assert set(x.name for x in list_lookup(engine, "support_services")) == {"СВТ", "ЕТС"}
    assert list_lookup(engine, "vehicle_brands") == []
    from pathlib import Path

    assert migrate(Path(engine.url.database)) is None
    assert len(list_lookup(engine, "vehicle_types")) == 13


def test_foreign_keys_reject_missing_brand(engine):
    with Session(engine) as session:
        session.add(
            Vehicle(
                brand_id=999,
                model_id=999,
                vehicle_type_id=1,
                support_service_id=1,
                work_unit="км",
                created_by="test",
                updated_by="test",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_used_lookup_cannot_be_deleted(engine, minimum, actor):
    save_vehicle(engine, minimum, actor)
    with Session(engine) as session:
        session.delete(session.get(VehicleBrand, minimum["brand_id"]))
        with pytest.raises(IntegrityError):
            session.commit()


def test_unknown_database_is_refused(tmp_path):
    path = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE important_data (value TEXT)")
        connection.execute("INSERT INTO important_data VALUES ('preserve me')")
    with pytest.raises(RuntimeError, match="без версії"):
        migrate(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM important_data").fetchone()[0] == "preserve me"


def test_upgrade_existing_database_keeps_rows_and_backups(tmp_path):
    path = tmp_path / "old.sqlite3"
    command.upgrade(alembic_config(path), "0001")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO vehicle_brands (id,name,normalized_name,is_active) VALUES (1,'A','a',1)",
        )
        connection.execute(
            "INSERT INTO vehicle_models (id,name,normalized_name,is_active,brand_id) "
            "VALUES (1,'M','m',1,1)",
        )
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    (attachments / "test.txt").write_text("synthetic attachment", encoding="utf-8")
    backup = migrate(path)
    assert backup.exists()
    assert (backup.parent / "attachments" / "test.txt").read_text() == "synthetic attachment"
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001"
        assert connection.execute("SELECT brand_id FROM vehicle_models").fetchone()[0] == 1
    engine = make_engine(path)
    try:
        assert list_lookup(engine, "vehicle_brands")[0].name == "A"
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()


def test_backup_preserves_vehicle_and_relationships(engine, minimum, actor, tmp_path):
    from pathlib import Path

    vehicle = save_vehicle(engine, minimum, actor)
    copy = verified_backup(Path(engine.url.database), tmp_path / "copy")
    restored = make_engine(copy)
    try:
        with Session(restored) as session:
            assert session.get(Vehicle, vehicle.id).brand_id == minimum["brand_id"]
            assert len(session.exec(select(Vehicle)).all()) == 1
    finally:
        restored.dispose()
