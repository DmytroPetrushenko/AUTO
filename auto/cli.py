import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from auto.backup import backup_bundle, restore_bundle, verify_bundle
from auto.db import db_path, make_engine, migrate, require_current_schema
from auto.runtime import RunningLock


def check_database(path):
    if not path.is_file():
        raise ValueError("БД не знайдено. Спочатку виконайте install.cmd")
    engine = make_engine(path)
    try:
        require_current_schema(engine)
        with engine.connect() as c:
            if c.exec_driver_sql("PRAGMA integrity_check").scalar() != "ok":
                raise ValueError("SQLite integrity_check не пройдено")
            if c.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                raise ValueError("SQLite foreign_key_check не пройдено")
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="AUTO: локальне обслуговування БД")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("migrate")
    backup = commands.add_parser("backup")
    backup.add_argument("--destination", type=Path)
    verify = commands.add_parser("verify-backup")
    verify.add_argument("bundle", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("bundle", type=Path)
    restore.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    path = db_path()
    try:
        if args.command == "check":
            check_database(path)
            print(f"SQLite OK: {path}")
        elif args.command == "migrate":
            guard = RunningLock(path)
            try:
                backup_path = migrate(path)
                check_database(path)
                print(f"SQLite готова: {path}")
                if backup_path:
                    print(f"Перевірена резервна копія: {backup_path.parent}")
            finally:
                guard.close()
        elif args.command == "backup":
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            target = args.destination or path.parent / "backups" / stamp
            print(f"Перевірена резервна копія: {backup_bundle(path, target)}")
        elif args.command == "verify-backup":
            verify_bundle(args.bundle)
            print(f"Резервна копія OK: {args.bundle.resolve()}")
        elif args.command == "restore":
            restore_bundle(args.bundle, path, confirmed=args.confirm)
            print(f"Відновлено: {path}. Перевірте версію командою check.")
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"AUTO: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
