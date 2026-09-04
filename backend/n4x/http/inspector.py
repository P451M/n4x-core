from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InspectorSettings:
    mcp_url: str
    client_port: int = 6274
    proxy_port: int = 6277
    ready_timeout_seconds: float = 90.0


class InspectorProcess:
    """Supervises the MCP Inspector UI for local development."""

    def __init__(self, settings: InspectorSettings) -> None:
        self.settings = settings
        self._process: subprocess.Popen[Any] | None = None
        self._config_dir: tempfile.TemporaryDirectory[str] | None = None

    @property
    def ui_url(self) -> str:
        return f"http://127.0.0.1:{self.settings.client_port}"

    def start(self) -> None:
        npx = shutil.which("npx")
        if npx is None:
            raise RuntimeError(
                "development mode requires npx to start MCP Inspector"
            )
        self._config_dir = tempfile.TemporaryDirectory(prefix="n4x-inspector-")
        config_path = Path(self._config_dir.name) / "mcp.json"
        config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "n4x": {
                            "type": "http",
                            "url": self.settings.mcp_url,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        command = [
            npx,
            "--yes",
            "@modelcontextprotocol/inspector",
            "--web",
            "--catalog",
            str(config_path),
        ]
        try:
            self._process = subprocess.Popen(
                command,
                env=_inspector_env(self.settings),
                start_new_session=True,
            )
            self._wait_until_ready()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            _stop_process_group(process)
        if self._config_dir is not None:
            self._config_dir.cleanup()
            self._config_dir = None

    def _wait_until_ready(self) -> None:
        process = self._process
        assert process is not None
        deadline = time.monotonic() + self.settings.ready_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("MCP Inspector exited before becoming ready")
            if _port_is_open("127.0.0.1", self.settings.client_port):
                return
            time.sleep(0.1)
        raise RuntimeError(
            "MCP Inspector did not listen on "
            f"127.0.0.1:{self.settings.client_port} within "
            f"{self.settings.ready_timeout_seconds:.0f}s"
        )


def public_base_url(host: str, port: int) -> str:
    bound = "127.0.0.1" if host in {"0.0.0.0", "::", "[::]"} else host
    return f"http://{bound}:{port}"


def resolve_worker_public_origin(
    host: str,
    port: int,
    configured: str | None = None,
) -> str:
    from n4x.host.http import is_loopback_origin

    origin = (configured or "").strip().rstrip("/")
    if origin and not is_loopback_origin(origin):
        return origin
    return public_base_url(host, port)


def _inspector_env(settings: InspectorSettings) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not _is_secret_env(key)
    }
    env["CLIENT_PORT"] = str(settings.client_port)
    env["SERVER_PORT"] = str(settings.proxy_port)
    return env


def _is_secret_env(name: str) -> bool:
    upper = name.upper()
    return any(
        marker in upper
        for marker in (
            "PASSWORD",
            "SECRET",
            "TOKEN",
            "CREDENTIAL",
            "PRIVATE_KEY",
            "MASTER_KEY",
            "_KEY",
            "GHCR",
            "PAT",
        )
    )


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _stop_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=5)
