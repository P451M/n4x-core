from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from n4x.kernel.errors import ValidationFailure
from n4x.runtime.cypher_gateway import CypherGatewaySession

_REAP_INTERVAL_SECONDS = 30.0


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait()


class PooledChild:
    def __init__(
        self,
        key: tuple[str, str, str],
        process: subprocess.Popen[str],
        session: CypherGatewaySession,
        close_session: Callable[[], None],
    ) -> None:
        self.key = key
        self.process = process
        self.session = session
        self._close_session = close_session
        self.busy = True
        self.released_at = 0.0
        self._log_lock = threading.Lock()
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._drains = [
            threading.Thread(
                target=self._drain,
                args=(process.stdout, self._stdout),
                name="n4x-action-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=self._drain,
                args=(process.stderr, self._stderr),
                name="n4x-action-stderr",
                daemon=True,
            ),
        ]
        for thread in self._drains:
            thread.start()

    def log_marks(self) -> tuple[int, int]:
        with self._log_lock:
            return len(self._stdout), len(self._stderr)

    def logs_since(self, marks: tuple[int, int]) -> tuple[str, str]:
        with self._log_lock:
            return (
                "".join(self._stdout[marks[0] :]),
                "".join(self._stderr[marks[1] :]),
            )

    def alive(self) -> bool:
        return self.process.poll() is None

    def kill(self) -> None:
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except Exception:
                pass
        terminate_process_group(self.process)
        try:
            self._close_session()
        except Exception:
            pass
        for thread in self._drains:
            thread.join(timeout=0.2)

    def _drain(self, stream: Any, chunks: list[str]) -> None:
        if stream is None:
            return
        try:
            while True:
                line = stream.readline()
                if line == "":
                    break
                with self._log_lock:
                    chunks.append(line)
        except Exception:
            pass


class ActionPool:
    """Retain child interpreters after an invoke. Size is total children."""

    def __init__(
        self,
        *,
        max_size: int | None = None,
        idle_seconds: float | None = None,
    ) -> None:
        self.max_size = (
            int(os.getenv("N4X_ACTION_POOL_SIZE", "10"))
            if max_size is None
            else max_size
        )
        self.idle_seconds = (
            float(os.getenv("N4X_ACTION_POOL_IDLE_SECONDS", "28800"))
            if idle_seconds is None
            else idle_seconds
        )
        if self.max_size < 0:
            raise ValueError("max_size must not be negative")
        if self.idle_seconds < 0:
            raise ValueError("idle_seconds must not be negative")
        self._lock = threading.Lock()
        self._children: list[PooledChild] = []
        self._reserved = 0
        self._closed = False
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        if self.idle_seconds > 0:
            self._reaper = threading.Thread(
                target=self._reap_loop,
                name="n4x-action-pool-reaper",
                daemon=True,
            )
            self._reaper.start()

    def checkout(
        self,
        key: tuple[str, str, str],
        spawn: Callable[[], PooledChild],
    ) -> PooledChild:
        with self._lock:
            if self._closed:
                raise ValidationFailure("action interpreter pool is shut down")
            self._reap_locked()
            self._evict_stale_interpreters_locked(key)
            idle = next(
                (
                    child
                    for child in self._children
                    if child.key == key and not child.busy and child.alive()
                ),
                None,
            )
            if idle is not None:
                idle.busy = True
                return idle
            if self.max_size > 0 and self._occupancy() >= self.max_size:
                victim = self._lru_idle_locked()
                if victim is None:
                    raise ValidationFailure("action interpreter pool is full")
                self._kill_locked(victim)
            self._reserved += 1
        try:
            child = spawn()
        except BaseException:
            with self._lock:
                self._reserved -= 1
            raise
        child.key = key
        child.busy = True
        with self._lock:
            self._reserved -= 1
            self._children.append(child)
        return child

    def release(self, child: PooledChild, *, retain: bool) -> None:
        with self._lock:
            if (
                retain
                and self.max_size > 0
                and not self._closed
                and child.alive()
            ):
                child.busy = False
                child.released_at = time.monotonic()
                return
            self._kill_locked(child)

    def evict_revision(self, revision_id: str) -> None:
        with self._lock:
            victims = [
                child for child in list(self._children) if child.key[0] == revision_id
            ]
            for child in victims:
                self._kill_locked(child)

    def evict_data_space(self, data_space_id: str) -> None:
        with self._lock:
            victims = [
                child
                for child in list(self._children)
                if child.key[1] == data_space_id
            ]
            for child in victims:
                self._kill_locked(child)

    def contains_pid(self, pid: int) -> bool:
        with self._lock:
            return any(child.process.pid == pid for child in self._children)

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            self._closed = True
            for child in list(self._children):
                self._kill_locked(child)
        if self._reaper is not None:
            self._reaper.join(timeout=1)

    def _occupancy(self) -> int:
        return len(self._children) + self._reserved

    def _evict_stale_interpreters_locked(
        self, key: tuple[str, str, str]
    ) -> None:
        revision_id, data_space_id, python_executable = key
        for child in list(self._children):
            if (
                child.key[0] == revision_id
                and child.key[1] == data_space_id
                and child.key[2] != python_executable
                and not child.busy
            ):
                self._kill_locked(child)

    def _lru_idle_locked(self) -> PooledChild | None:
        idle = [
            child
            for child in self._children
            if not child.busy
        ]
        if not idle:
            return None
        return min(idle, key=lambda child: child.released_at)

    def _reap_loop(self) -> None:
        while not self._stop.wait(_REAP_INTERVAL_SECONDS):
            with self._lock:
                self._reap_locked()

    def _reap_locked(self) -> None:
        if self.idle_seconds <= 0:
            return
        now = time.monotonic()
        for child in list(self._children):
            if (
                not child.busy
                and now - child.released_at >= self.idle_seconds
            ):
                self._kill_locked(child)

    def _kill_locked(self, child: PooledChild) -> None:
        if child in self._children:
            self._children.remove(child)
        child.kill()
