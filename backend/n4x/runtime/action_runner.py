from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
import socket
import sys
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class SecretContext:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, uri: str) -> str:
        if uri not in self.values:
            raise KeyError(uri)
        return self.values[uri]


class GraphContext:
    def __init__(self) -> None:
        self._tx_connection: socket.socket | None = None

    def run_app_cypher_read(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return self.run_cypher(query, params, mode="read")

    def run_app_cypher_write(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return self.run_cypher(query, params, mode="write")

    def run_cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        mode: str = "write",
    ) -> list[dict[str, Any]]:
        request = {
            "id": str(uuid.uuid4()),
            "op": "query",
            "mode": mode,
            "query": query,
            "params": params or {},
        }
        if self._tx_connection is not None:
            return self._rpc(self._tx_connection, request)
        socket_path, token = self._endpoint()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(socket_path)
            return self._rpc(connection, {**request, "token": token})

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._tx_connection is not None:
            raise RuntimeError("cypher transaction is already open")
        socket_path, token = self._endpoint()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(socket_path)
        self._tx_connection = connection
        try:
            self._rpc(connection, {"id": str(uuid.uuid4()), "op": "begin", "token": token})
            yield
            self._rpc(
                connection, {"id": str(uuid.uuid4()), "op": "commit", "token": token}
            )
        except BaseException:
            try:
                self._rpc(
                    connection,
                    {"id": str(uuid.uuid4()), "op": "rollback", "token": token},
                )
            except Exception:
                pass
            raise
        finally:
            self._tx_connection = None
            connection.close()

    def raw_driver(self) -> None:
        return None

    def _endpoint(self) -> tuple[str, str]:
        socket_path = os.environ.get("N4X_CYPHER_SOCKET")
        token = os.environ.get("N4X_CYPHER_TOKEN")
        if not socket_path or not token:
            raise RuntimeError("Cypher gateway is unavailable")
        return socket_path, token

    def _rpc(
        self, connection: socket.socket, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        payload = {
            "token": os.environ.get("N4X_CYPHER_TOKEN", ""),
            "contract_version": os.environ.get(
                "N4X_ACTION_CONTEXT_VERSION", "n4x.action.context.v2"
            ),
            **request,
        }
        data = json.dumps(payload).encode("utf-8")
        connection.sendall(len(data).to_bytes(4, "big") + data)
        header = _read_exact(connection, 4)
        body = _read_exact(connection, int.from_bytes(header, "big"))
        response = json.loads(body.decode("utf-8"))
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "cypher gateway request failed")
        return list(response.get("rows") or [])


class ActionContext:
    def __init__(self, secrets: dict[str, str]) -> None:
        self.secrets = SecretContext(secrets)
        self.graph = GraphContext()
        self.application_id = os.environ.get("N4X_APPLICATION_ID", "")
        self.data_space_id = os.environ.get("N4X_DATA_SPACE_ID", "production")


def main() -> int:
    loaded: dict[str, tuple[Any, int, int]] = {}
    while True:
        line = sys.stdin.readline()
        if line == "":
            return 0
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        result_path = Path(request["result_path"])
        try:
            _run_one(request, loaded)
        except Exception as exc:  # noqa: BLE001 - trusted app failures must be reported.
            _write_result(
                result_path,
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                },
            )


def _run_one(request: dict[str, Any], loaded: dict[str, tuple[Any, int, int]]) -> None:
    result_path = Path(request["result_path"])
    module_path = Path(request["module"])
    function_name = str(request["function"])
    input_path = request.get("input_path")
    if input_path:
        input_value = json.loads(Path(input_path).read_text(encoding="utf-8"))
    else:
        input_value = {}
    secrets_path = request.get("secrets_path")
    secrets = (
        json.loads(Path(secrets_path).read_text(encoding="utf-8"))
        if secrets_path
        else {}
    )
    parent = str(module_path.parent.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    fn = _load_callable(module_path, function_name, loaded)
    if request.get("check_only"):
        _write_result(result_path, {"ok": True, "output": None})
        return
    ctx = ActionContext(secrets)
    result = fn(ctx, input_value)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    _write_result(result_path, {"ok": True, "output": result})


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = connection.recv(size - len(chunks))
        if not piece:
            raise ConnectionError("cypher gateway connection closed")
        chunks.extend(piece)
    return bytes(chunks)


def _load_callable(
    path: Path,
    function_name: str,
    loaded: dict[str, tuple[Any, int, int]],
) -> Any:
    if not path.exists():
        raise FileNotFoundError(path)
    stat = path.stat()
    cache_key = str(path)
    cached = loaded.get(cache_key)
    if (
        cached is not None
        and cached[1] == stat.st_mtime_ns
        and cached[2] == stat.st_size
    ):
        module = cached[0]
    else:
        module_name = f"n4x_graph_action_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        loaded[cache_key] = (module, stat.st_mtime_ns, stat.st_size)
    fn = getattr(module, function_name)
    if not callable(fn):
        raise TypeError(f"{function_name} is not callable")
    return fn


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "pid": os.getpid()}
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
