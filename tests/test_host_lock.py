from __future__ import annotations

from pathlib import Path

import pytest

from n4x.host.locking import HostLock, HostLockBusy


def test_host_lock_excludes_second_process(tmp_path: Path) -> None:
    path = tmp_path / "host.lock"
    first = HostLock(path)
    second = HostLock(path)
    first.acquire()
    try:
        with pytest.raises(HostLockBusy):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()
