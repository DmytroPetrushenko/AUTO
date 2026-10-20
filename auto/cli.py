import argparse

from auto.db import db_path, migrate


def main():
    parser = argparse.ArgumentParser(description="AUTO: локальне обслуговування БД")
    parser.add_argument("command", choices=["migrate"])
    parser.parse_args()
    backup = migrate(db_path())
    print(f"SQLite готова: {db_path()}")
    if backup:
        print(f"Перевірена резервна копія: {backup}")


if __name__ == "__main__":
    main()
