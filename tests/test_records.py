from datetime import date
from pathlib import Path

import pytest

from auto.backup import backup_bundle, restore_bundle, verify_bundle
from auto.db import make_engine
from auto.history import delete_vehicle
from auto.records import (
    list_documents,
    list_equipment,
    list_notes,
    note_versions,
    read_attachment,
    save_document,
    save_equipment,
    save_note,
    validate_upload,
)
from auto.runtime import RunningLock
from auto.services import Actor, BusinessError, ConflictError, Role, save_vehicle

VIEWER = Actor("reader", Role.VIEWER)
ADMIN = Actor("admin", Role.ADMIN)
PDF = b"%PDF-1.4\nSynthetic attachment\n%%EOF"


def test_multiple_files_versions_and_access(engine, minimum, actor):
    v = save_vehicle(engine, minimum, actor)
    doc = save_document(
        engine, v.id, {"kind": "Акт"}, [("a.pdf", PDF), ("b.pdf", PDF + b"2")], actor
    )
    save_document(
        engine,
        v.id,
        {"kind": "Акт", "number": "2"},
        [("new.pdf", PDF)],
        actor,
        document_id=doc.id,
        expected_version=1,
    )
    entries = list_documents(engine, v.id, VIEWER)
    assert [r["revision"].version for r in entries] == [2, 1]
    assert len(entries[1]["files"]) == 2
    f = entries[1]["files"][0]
    assert read_attachment(engine, v.id, f.id, VIEWER)[1] == PDF
    with pytest.raises(BusinessError):
        read_attachment(engine, "other", f.id, VIEWER)
    with pytest.raises(PermissionError):
        read_attachment(engine, v.id, f.id, Actor("", Role.VIEWER))
    with pytest.raises(ConflictError):
        save_document(
            engine,
            v.id,
            {"kind": "Акт"},
            [("a.pdf", PDF)],
            actor,
            document_id=doc.id,
            expected_version=1,
        )


@pytest.mark.parametrize(
    "name,data",
    [
        ("../a.pdf", PDF),
        ("..\\a.pdf", PDF),
        ("C:a.pdf", PDF),
        ("a.exe", PDF),
        ("a.png", PDF),
        ("a.xlsx", PDF),
        ("a.pdf", b"not PDF"),
        ("a.pdf", b""),
        ("a.pdf", b"x" * (20 * 1024 * 1024 + 1)),
    ],
    ids=[
        "parent",
        "backslash",
        "drive",
        "executable",
        "fake-image",
        "fake-office",
        "fake-pdf",
        "empty",
        "oversized",
    ],
)
def test_bad_attachments_rejected(name, data):
    with pytest.raises(BusinessError):
        validate_upload(name, data)


def test_failed_document_leaves_no_db_or_files(engine, minimum, actor):
    v = save_vehicle(engine, minimum, actor)
    with pytest.raises(BusinessError):
        save_document(engine, v.id, {"kind": "Act"}, [], actor)
    with pytest.raises(BusinessError):
        save_document(engine, v.id, {"kind": "Act"}, [("good.pdf", PDF), ("bad.exe", PDF)], actor)
    assert list_documents(engine, v.id, actor) == []
    assert not (Path(engine.url.database).parent / "attachments").exists()


def test_checksum_and_path_traversal_metadata_blocked(engine, minimum, actor, tmp_path):
    v = save_vehicle(engine, minimum, actor)
    save_document(engine, v.id, {"kind": "Act"}, [("a.pdf", PDF)], actor)
    f = list_documents(engine, v.id, actor)[0]["files"][0]
    root = Path(engine.url.database).parent / "attachments"
    (root / f.relative_path).write_bytes(b"changed")
    with pytest.raises(BusinessError, match="сума"):
        read_attachment(engine, v.id, f.id, actor)
    # A corrupt metadata row cannot escape the attachment root even if DB triggers are bypassed.
    with engine.begin() as c:
        c.exec_driver_sql("DROP TRIGGER vehicle_document_files_no_update")
        c.exec_driver_sql("UPDATE vehicle_document_files SET relative_path='../outside.pdf'")
    (root.parent / "outside.pdf").write_bytes(PDF)
    with pytest.raises(BusinessError, match="шлях"):
        read_attachment(engine, v.id, f.id, actor)


def test_equipment_removed_is_retained_with_version(engine, minimum, actor):
    v = save_vehicle(engine, minimum, actor)
    item = save_equipment(engine, v.id, {"name": "Radio", "quantity": 2}, actor)
    saved = save_equipment(
        engine,
        v.id,
        {"name": "Radio", "quantity": 2, "is_removed": True, "removed_on": date(2026, 1, 1)},
        actor,
        equipment_id=item.id,
        expected_version=1,
    )
    assert saved.version == 2
    assert list_equipment(engine, v.id, actor, removed=False) == []
    assert list_equipment(engine, v.id, actor, removed=True)[0].id == item.id
    with pytest.raises(ConflictError):
        save_equipment(
            engine,
            v.id,
            {"name": "Radio", "quantity": 1},
            actor,
            equipment_id=item.id,
            expected_version=1,
        )


def test_notes_versions_hidden_and_order(engine, minimum, actor):
    v = save_vehicle(engine, minimum, actor)
    n = save_note(engine, v.id, "first", actor)
    newer = save_note(engine, v.id, "second", actor)
    assert list_notes(engine, v.id, actor)[0].id == newer.id
    save_note(engine, v.id, "edited", actor, note_id=n.id, expected_version=1, is_hidden=True)
    assert [r.text for r in note_versions(engine, v.id, n.id, actor)] == ["edited", "first"]
    assert len(list_notes(engine, v.id, actor)) == 1
    assert len(list_notes(engine, v.id, actor, include_hidden=True)) == 2
    with pytest.raises(ConflictError):
        save_note(engine, v.id, "lost", actor, note_id=n.id, expected_version=1)


def test_reader_cannot_write_records(engine, minimum, actor):
    v = save_vehicle(engine, minimum, actor)
    for call in (
        lambda: save_note(engine, v.id, "text", VIEWER),
        lambda: save_equipment(engine, v.id, {"name": "x", "quantity": 1}, VIEWER),
        lambda: save_document(engine, v.id, {"kind": "Act"}, [("a.pdf", PDF)], VIEWER),
    ):
        with pytest.raises(PermissionError):
            call()


@pytest.mark.parametrize("kind", ["document", "equipment", "note"])
def test_independent_records_block_deletion(engine, minimum, actor, kind):
    v = save_vehicle(engine, minimum, actor)
    if kind == "document":
        save_document(engine, v.id, {"kind": "Act"}, [("a.pdf", PDF)], actor)
    elif kind == "equipment":
        save_equipment(engine, v.id, {"name": "x", "quantity": 1}, actor)
    else:
        save_note(engine, v.id, "text", actor)
    with pytest.raises(BusinessError, match="блокують"):
        delete_vehicle(engine, v.id, ADMIN, expected_version=1, confirmed=True, reason="test")


def test_versions_are_immutable(engine, minimum, actor):
    from sqlalchemy.exc import IntegrityError

    v = save_vehicle(engine, minimum, actor)
    save_note(engine, v.id, "text", actor)
    save_document(engine, v.id, {"kind": "Act"}, [("a.pdf", PDF)], actor)
    for table in ("vehicle_document_files", "vehicle_document_versions", "vehicle_note_versions"):
        with engine.begin() as c:
            with pytest.raises(IntegrityError, match="immutable"):
                c.exec_driver_sql(f"DELETE FROM {table}")


def test_backup_restore_db_and_all_file_versions(engine, minimum, actor, tmp_path):
    v = save_vehicle(engine, minimum, actor)
    save_document(engine, v.id, {"kind": "Act"}, [("a.pdf", PDF), ("b.pdf", PDF + b"2")], actor)
    save_note(engine, v.id, "text", actor)
    bundle = backup_bundle(Path(engine.url.database), tmp_path / "bundle")
    assert verify_bundle(bundle)["format"] == 1
    target = tmp_path / "restored" / "auto.sqlite3"
    restore_bundle(bundle, target, confirmed=True)
    restored = make_engine(target)
    try:
        entries = list_documents(restored, v.id, VIEWER)
        assert len(entries[0]["files"]) == 2
        assert read_attachment(restored, v.id, entries[0]["files"][1].id, VIEWER)[1] == PDF + b"2"
        assert list_notes(restored, v.id, VIEWER)[0].text == "text"
    finally:
        restored.dispose()
    with pytest.raises(BusinessError):
        restore_bundle(bundle, target)
    guard = RunningLock(target)
    try:
        with pytest.raises(BusinessError, match="Закрийте"):
            restore_bundle(bundle, target, confirmed=True)
    finally:
        guard.close()
    (bundle / "attachments" / entries[0]["files"][0].relative_path).write_bytes(b"corrupt")
    with pytest.raises(BusinessError, match="сума"):
        restore_bundle(bundle, target, confirmed=True)
