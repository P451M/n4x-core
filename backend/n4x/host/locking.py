from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any


class HostLockBusy(RuntimeError):
    """Another host already holds the install lock."""


@contextmanager
def exclusive_file_lock(handle: IO[str], *, blocking: bool = True) -> Iterator[None]:
    flags = fcntl.LOCK_EX
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), flags)
    except BlockingIOError as error:
        raise HostLockBusy("another n4x host holds this install lock") from error
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class HostLock:
    """One host process per install root."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: Any = None
        self._guard: Any = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self.held:
            return
        self._handle = open(self.path, "a+")
        self._guard = exclusive_file_lock(self._handle, blocking=False)
        self._guard.__enter__()

    def release(self) -> None:
        if self._guard is not None:
            self._guard.__exit__(None, None, None)
            self._guard = None
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> HostLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
