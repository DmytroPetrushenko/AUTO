from pathlib import Path

from auto.services import BusinessError


class RunningLock:
    """OS lock shared by the UI and restore; released when the process exits."""

    def __init__(self, database):
        path = Path(database).resolve().parent / ".auto-running.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        self.stream.seek(0)
        self.stream.write(b"0")
        self.stream.flush()
        self.stream.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise BusinessError(
                "AUTO вже використовує цю БД. Закрийте застосунок перед відновленням."
            ) from exc

    def close(self):
        self.stream.close()

    def __del__(self):
        if hasattr(self, "stream"):
            self.stream.close()
