from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from n4x.host.identity import HOST_ADAPTER

# Enable HTTP waits 300s so a failed enable can finish this wait and the
# rollback start of the previous worker. Empty-graph CI is healthy in well
# under 8s; a live instance must construct SystemRuntime, FastMCP, and the
# scheduler before /health exists. 8s kills that process and returns a bare 503.
WORKER_BOOT_TIMEOUT_SECONDS = 120.0
WORKER_LOG_TAIL_BYTES = 8192


class WorkerBootError(RuntimeError):
    """The System worker did not become healthy."""


def worker_log_tail(log_path: Path, *, limit: int = WORKER_LOG_TAIL_BYTES) -> str:
    if not log_path.is_file():
        return ""
    data = log_path.read_bytes()
    if len(data) > limit:
        data = data[-limit:]
    return data.decode("utf-8", errors="replace").strip()


def unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def worker_pythonpath(materialized_root: Path, existing: str = "") -> str:
    root = str(materialized_root.resolve())
    if not existing:
        return root
    parts = [item for item in existing.split(os.pathsep) if item and item != root]
    return os.pathsep.join([root, *parts])


@dataclass
class WorkerSpec:
    revision_id: str
    content_root: str = ""
    source_tree_id: str = ""
    materialized_root: Path | None = None
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    health_path: str = "/health"
    boot_timeout_seconds: float = WORKER_BOOT_TIMEOUT_SECONDS

    def resolved_command(self) -> list[str]:
        if self.command:
            return list(self.command)
        return [sys.executable, "-P", "-m", "n4x.system.worker"]


@dataclass
class RunningWorker:
    spec: WorkerSpec
    process: subprocess.Popen[bytes]
    port: int
    log_handle: object | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def health_url(self) -> str:
        return f"{self.base_url}{self.spec.health_path}"


class WorkerSupervisor:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.current: RunningWorker | None = None

    def start(self, spec: WorkerSpec) -> RunningWorker:
        if self.current is not None:
            raise RuntimeError("a System worker is already running")
        if spec.materialized_root is None:
            raise WorkerBootError("worker spec is missing materialized_root")
        port = unused_loopback_port()
        env = os.environ.copy()
        env.update(spec.env)
        env["N4X_SYSTEM_REVISION_ID"] = spec.revision_id
        env["N4X_SYSTEM_CONTENT_ROOT"] = spec.content_root
        env["N4X_SYSTEM_SOURCE_TREE_ID"] = spec.source_tree_id
        env["N4X_SYSTEM_MATERIALIZED_ROOT"] = str(spec.materialized_root)
        env["N4X_HOST_ABI"] = HOST_ADAPTER
        env["N4X_WORKER_HOST"] = "127.0.0.1"
        env["N4X_WORKER_PORT"] = str(port)
        if "N4X_RUNTIME_ROOT" not in spec.env:
            env["N4X_RUNTIME_ROOT"] = str(self.runtime_root)
        env["PYTHONPATH"] = worker_pythonpath(
            spec.materialized_root, env.get("PYTHONPATH", "")
        )
        log_path = self.runtime_root / f"{spec.revision_id}.log"
        log_handle = open(log_path, "ab")
        process = subprocess.Popen(
            spec.resolved_command(),
            cwd=spec.cwd,
            env=env,
            start_new_session=True,
            stdout=log_handle,
            stderr=log_handle,
        )
        worker = RunningWorker(
            spec=spec, process=process, port=port, log_handle=log_handle
        )
        try:
            self._wait_healthy(worker)
        except WorkerBootError as error:
            log_handle.flush()
            self.current = worker
            tail = worker_log_tail(log_path)
            self.stop()
            if tail:
                raise WorkerBootError(f"{error}\n--- worker log ---\n{tail}") from error
            raise
        self.current = worker
        return worker

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        worker = self.current
        self.current = None
        if worker is None:
            return
        pid = worker.process.pid
        try:
            if pid is not None:
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                else:
                    deadline = time.monotonic() + timeout_seconds
                    while time.monotonic() < deadline:
                        if worker.process.poll() is not None:
                            break
                        time.sleep(0.05)
                    if worker.process.poll() is None:
                        try:
                            os.killpg(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        else:
                            worker.process.wait(timeout=2)
        finally:
            if worker.log_handle is not None:
                worker.log_handle.close()

    def _wait_healthy(self, worker: RunningWorker) -> None:
        deadline = time.monotonic() + worker.spec.boot_timeout_seconds
        last_error = "worker did not become healthy"
        while time.monotonic() < deadline:
            if worker.process.poll() is not None:
                raise WorkerBootError(
                    f"System worker exited before health ({worker.process.returncode})"
                )
            try:
                with urlopen(worker.health_url(), timeout=0.4) as response:
                    if response.status == 200:
                        return
            except (URLError, TimeoutError, OSError) as error:
                last_error = str(error)
            time.sleep(0.05)
        raise WorkerBootError(last_error)
