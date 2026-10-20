import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlmodel import create_engine

ROOT = Path(__file__).resolve().parent.parent


def db_path() -> Path:
    return Path(os.environ.get("AUTO_DB_PATH", ROOT / "data" / "auto.sqlite3")).resolve()


def make_engine(path: Path):
    engine = create_engine(
        "sqlite:///" + path.resolve().as_posix(),
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")

    return engine


def alembic_config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option(
        "sqlalchemy.url", "sqlite:///" + path.resolve().as_posix().replace("%", "%%")
    )
    return config


def require_current_schema(engine) -> None:
    config = alembic_config(db_path())
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current != ScriptDirectory.from_config(config).get_current_head():
        raise RuntimeError("БД не підготовлена. Виконайте: python -m auto.cli migrate")


def verified_backup(path: Path, target_dir: Path, attachments: Path | None = None) -> Path:
    target_dir.mkdir(parents=True, exist_ok=False)
    target = target_dir / path.name
    with sqlite3.connect(path) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Резервна копія не пройшла integrity_check")
        if destination.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Резервна копія містить порушені зовнішні ключі")
    if attachments and attachments.exists():
        if attachments.is_symlink() or any(p.is_symlink() for p in attachments.rglob("*")):
            raise RuntimeError("Символічні посилання у вкладеннях заборонені")
        shutil.copytree(attachments, target_dir / "attachments")
    return target


def migrate(path: Path, backup_root: Path | None = None) -> Path | None:
    """Explicit migration command. Never adopt an unknown populated database."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(path)
    config = alembic_config(path)
    backup = None
    try:
        with engine.connect() as connection:
            tables = (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
                .scalars()
                .all()
            )
            current = MigrationContext.configure(connection).get_current_revision()
        head = ScriptDirectory.from_config(config).get_current_head()
        if current == head:
            return None
        if tables and current is None:
            raise RuntimeError("Невідома непорожня БД без версії Alembic: міграцію зупинено")
        if tables:
            root = backup_root or path.parent / "backups"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            from auto.backup import backup_bundle

            backup = backup_bundle(path, root / stamp) / path.name
        with engine.begin() as connection:
            # Existing connection ensures foreign_keys=ON for Alembic too.
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Міграція порушила зовнішні ключі")
    finally:
        engine.dispose()
    return backup
