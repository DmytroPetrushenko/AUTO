import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from auto.backup import backup_bundle, restore_bundle, verify_bundle
from auto.db import make_engine, migrate
from auto.records import list_documents, list_notes, read_attachment, save_document, save_note
from auto.services import get_vehicle, save_vehicle


def cli(path, *args):
    env = {**os.environ, "AUTO_DB_PATH": str(path), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", "auto.cli", *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )


def test_cli_migrate_check_backup_restore_and_confirmation(tmp_path):
    source = tmp_path / "source" / "auto.sqlite3"
    assert cli(source, "migrate").returncode == 0
    assert cli(source, "check").returncode == 0
    bundle = tmp_path / "bundle"
    assert cli(source, "backup", "--destination", bundle).returncode == 0
    assert cli(source, "verify-backup", bundle).returncode == 0
    target = tmp_path / "target" / "auto.sqlite3"
    assert cli(target, "restore", bundle).returncode == 1
    assert not target.exists()
    assert cli(target, "restore", bundle, "--confirm").returncode == 0
    assert cli(target, "check").returncode == 0
    assert cli(target, "backup", "--destination", bundle).returncode == 1


def test_migration_preserves_populated_documents_and_files(engine, minimum, actor, tmp_path):
    v = save_vehicle(engine, minimum, actor)
    save_document(engine, v.id, {"kind": "Act"}, [("a.pdf", b"%PDF-1.4\n%%EOF")], actor)
    n = save_note(engine, v.id, "original", actor)
    save_note(engine, v.id, "edited", actor, note_id=n.id, expected_version=1)
    bundle = backup_bundle(Path(engine.url.database), tmp_path / "filled-stage5")
    copy = bundle / Path(engine.url.database).name
    # Stage 6/7 contain only newly added empty tables, so reconstruct a genuine 0005 fixture.
    with sqlite3.connect(copy) as c:
        for table in (
            "import_receipts",
            "import_batches",
            "vehicle_fuel_norms",
            "vehicle_readings",
            "filter_presets",
        ):
            c.execute(f"DROP TABLE {table}")
        c.execute("UPDATE alembic_version SET version_num='0005'")
    with sqlite3.connect(copy) as c:
        original = list(c.iterdump())
    backup = migrate(copy)
    with sqlite3.connect(backup) as c:
        assert list(c.iterdump()) == original
    verify_bundle(backup.parent)
    restored = make_engine(copy)
    try:
        assert get_vehicle(restored, v.id).brand_id == minimum["brand_id"]
        assert list_notes(restored, v.id, actor)[0].text == "edited"
        file = list_documents(restored, v.id, actor)[0]["files"][0]
        assert read_attachment(restored, v.id, file.id, actor)[1] == b"%PDF-1.4\n%%EOF"
        with restored.connect() as c:
            assert not c.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    finally:
        restored.dispose()


def test_restore_rolls_back_if_attachment_install_fails(
    engine, minimum, actor, tmp_path, monkeypatch
):
    v = save_vehicle(engine, minimum, actor)
    save_note(engine, v.id, "backup", actor)
    source = Path(engine.url.database)
    bundle = backup_bundle(source, tmp_path / "bundle")
    save_vehicle(
        engine, minimum | {"color": "after-backup"}, actor, vehicle_id=v.id, expected_version=1
    )
    engine.dispose()
    original = os.replace

    def failing_replace(src, dest):
        if Path(src).name == "new-attachments":
            raise OSError("synthetic replace failure")
        return original(src, dest)

    monkeypatch.setattr("auto.backup.os.replace", failing_replace)
    import pytest

    with pytest.raises(OSError, match="synthetic"):
        restore_bundle(bundle, source, confirmed=True)
    assert get_vehicle(engine, v.id).color == "after-backup"
    assert list_notes(engine, v.id, actor)[0].text == "backup"


def test_windows_cmd_backup_wrapper(tmp_path):
    import pytest

    if sys.platform != "win32":
        pytest.skip("Windows command wrapper")
    path = tmp_path / "auto.sqlite3"
    migrate(path)
    target = tmp_path / "cmd-backup"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "backup.cmd", "--destination", str(target)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "AUTO_DB_PATH": str(path)},
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    verify_bundle(target)


def test_restore_replaces_existing_database_and_attachment_tree(engine, minimum, actor, tmp_path):
    v = save_vehicle(engine, minimum, actor)
    doc = save_document(engine, v.id, {"kind": "Act"}, [("old.pdf", b"%PDF-1.4\nold")], actor)
    path = Path(engine.url.database)
    bundle = backup_bundle(path, tmp_path / "original")
    save_document(
        engine,
        v.id,
        {"kind": "Act"},
        [("new.pdf", b"%PDF-1.4\nnew")],
        actor,
        document_id=doc.id,
        expected_version=1,
    )
    engine.dispose()
    restore_bundle(bundle, path, confirmed=True)
    docs = list_documents(engine, v.id, actor)
    assert len(docs) == 1 and docs[0]["revision"].version == 1
    assert read_attachment(engine, v.id, docs[0]["files"][0].id, actor)[1].endswith(b"old")
    assert len(list((path.parent / "attachments").iterdir())) == 1
    saved = list((path.parent / "backups").glob("before-restore-*"))
    assert len(saved) == 1 and len(verify_bundle(saved[0])["files"]) == 3


def test_document_commit_failure_cleans_new_files(engine, minimum, actor, monkeypatch):
    import pytest
    from sqlmodel import Session

    v = save_vehicle(engine, minimum, actor)

    def fail(self):
        raise OSError("synthetic commit failure")

    monkeypatch.setattr(Session, "commit", fail)
    with pytest.raises(OSError, match="synthetic"):
        save_document(engine, v.id, {"kind": "Act"}, [("a.pdf", b"%PDF-1.4\n%%EOF")], actor)
    assert list_documents(engine, v.id, actor) == []
    assert not list((Path(engine.url.database).parent / "attachments").iterdir())
