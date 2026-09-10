"""Single-writer guards for Import, acceptance, withdrawal, and cleanup."""

from __future__ import annotations

import errno
import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_WORKER_VARS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "TWIN_API_WORKERS")


class SingleWriterError(RuntimeError):
    """More than one API writer process was configured."""


class WriterLockHeld(RuntimeError):
    """Another import or lifecycle writer holds the exclusive lock."""


def assert_single_api_process() -> None:
    for key in _WORKER_VARS:
        raw = os.environ.get(key)
        if raw is None or raw.strip() == "":
            continue
        try:
            workers = int(raw)
        except ValueError as exc:
            raise SingleWriterError(f"{key}={raw!r} is not an integer") from exc
        if workers > 1:
            raise SingleWriterError(
                f"{key}={workers} is unsafe; this release allows one API writer process"
            )


class ImportWriterLock:
    """Exclusive non-blocking file lock under the import root."""

    def __init__(self, import_root: Path | str) -> None:
        root = Path(import_root)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / ".import-writer.lock"
        self._fd: int | None = None

    def __enter__(self) -> ImportWriterLock:
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if os.name == "nt":
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise WriterLockHeld("another import or lifecycle writer is active") from exc
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            try:
                if os.name == "nt":
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
