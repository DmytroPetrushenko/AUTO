import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

from auto.db import verified_backup
from auto.services import BusinessError


def _hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_bundle(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        path = (directory / name).resolve()
        if directory not in path.parents or not path.is_file() or path.is_symlink():
            raise BusinessError("Небезпечний або відсутній файл резервної копії")
        if _hash(path) != digest:
            raise BusinessError("Контрольна сума резервної копії не збігається")
    db = (directory / manifest["database"]).resolve()
    if db.parent != directory or manifest["database"] not in manifest["files"]:
        raise BusinessError("Неправильний шлях БД резервної копії")
    with sqlite3.connect(db) as c:
        if (
            c.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or c.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise BusinessError("Пошкоджена резервна БД")
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='vehicle_document_files'").fetchone():
            for relative, digest in c.execute(
                "SELECT relative_path,sha256 FROM vehicle_document_files"
            ):
                key = "attachments/" + relative
                if manifest["files"].get(key) != digest:
                    raise BusinessError("Вкладення БД відсутнє у копії")
    return manifest


def backup_bundle(database, directory):
    database = Path(database).resolve()
    directory = Path(directory).resolve()
    if not database.is_file():
        raise BusinessError("БД для резервування не знайдено")
    attachments = database.parent / "attachments"
    if directory == attachments or attachments in directory.parents:
        raise BusinessError("Резервну копію не можна створювати всередині вкладень")
    # SQLite backup obtains a consistent database snapshot. Attachment files are append-only.
    verified_backup(database, directory, attachments)
    files = {}
    for p in directory.rglob("*"):
        if p.is_symlink():
            raise BusinessError("Символічні посилання у вкладеннях заборонені")
        if p.is_file():
            files[p.relative_to(directory).as_posix()] = _hash(p)
    manifest = {"format": 1, "database": database.name, "files": files}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    verify_bundle(directory)
    return directory


def restore_bundle(directory, database, *, confirmed=False):
    if not confirmed:
        raise BusinessError("Відновлення потребує явного підтвердження")
    directory = Path(directory).resolve()
    database = Path(database).resolve()
    manifest = verify_bundle(directory)
    from auto.runtime import RunningLock

    guard = RunningLock(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    token = str(uuid4())
    stage = database.parent / (".restore-" + token)
    stage.mkdir()
    old_files = []
    installed = []
    try:
        # Always preserve the current installation before replacing it.
        if database.exists():
            backup_bundle(database, database.parent / "backups" / ("before-restore-" + token))
        shutil.copyfile(directory / manifest["database"], stage / "new.sqlite3")
        shutil.copytree(directory / "attachments", stage / "new-attachments") if (
            directory / "attachments"
        ).exists() else (stage / "new-attachments").mkdir()
        targets = [
            database,
            database.parent / "attachments",
            Path(str(database) + "-wal"),
            Path(str(database) + "-shm"),
        ]
        for index, target in enumerate(targets):
            if target.exists():
                old = stage / f"old-{index}"
                os.replace(target, old)
                old_files.append((old, target))
        os.replace(stage / "new.sqlite3", database)
        installed.append(database)
        os.replace(stage / "new-attachments", database.parent / "attachments")
        installed.append(database.parent / "attachments")
    except Exception:
        for path in reversed(installed):
            os.replace(path, stage / ("failed-" + path.name))
        for old, target in reversed(old_files):
            os.replace(old, target)
        raise
    finally:
        if stage.parent == database.parent and stage.name.startswith(".restore-"):
            shutil.rmtree(stage)
        guard.close()
    return database
