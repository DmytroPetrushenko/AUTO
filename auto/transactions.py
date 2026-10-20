"""Compose existing business operations into one SQLite transaction."""

from contextlib import contextmanager
from contextvars import ContextVar

from sqlmodel import Session as SQLSession

_active = ContextVar("auto_transaction", default=None)


def Session(engine):
    active = _active.get()
    if active is None:
        return SQLSession(engine)
    if active[0].engine is not engine:
        raise ValueError("Different database inside one transaction")
    return SQLSession(active[0], join_transaction_mode="create_savepoint")


def begin_write(session):
    if _active.get() is None:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def track_file(path):
    if active := _active.get():
        active[1].append(path)


@contextmanager
def atomic(engine):
    if _active.get() is not None:
        raise ValueError("Nested card transaction")
    files = []
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        token = _active.set((connection, files))
        try:
            yield
            connection.commit()
        except BaseException:
            connection.rollback()
            for path in files:
                path.unlink(missing_ok=True)
            raise
        finally:
            _active.reset(token)
