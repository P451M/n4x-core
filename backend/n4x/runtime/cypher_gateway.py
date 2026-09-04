from __future__ import annotations

import json
import os
import re
import socket
import socketserver
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from n4x.contracts import (
    ACTION_CONTEXT_VERSION,
    SUBPROCESS_PROTOCOL_VERSION,
)
from n4x.graph.store import GraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import CypherGatewayError
from n4x.kernel.hash import sha256_json, sha256_text
from n4x.kernel.models import CypherAuditRecord, now_utc

WRITE_QUERY = re.compile(
    r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b",
    re.IGNORECASE,
)
AVAILABILITY_QUERY = "RETURN 1 AS ok"
DEFAULT_TX_TIMEOUT_SECONDS = float(os.getenv("N4X_CYPHER_TX_TIMEOUT_SECONDS", "30"))


def _send_message(connection: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    try:
        connection.sendall(len(data).to_bytes(4, "big") + data)
    except BrokenPipeError:
        return


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = connection.recv(size - len(chunks))
        if not piece:
            raise ConnectionError("cypher gateway connection closed")
        chunks.extend(piece)
    return bytes(chunks)


def _recv_message(connection: socket.socket) -> dict[str, Any]:
    length = int.from_bytes(_read_exact(connection, 4), "big")
    return json.loads(_read_exact(connection, length).decode("utf-8"))


@dataclass(frozen=True)
class CypherGatewayEndpoint:
    socket_path: str
    token: str


class CypherGatewaySession:
    def __init__(self, endpoint: CypherGatewayEndpoint, server: _UnixGatewayServer) -> None:
        self.endpoint = endpoint
        self._server = server

    def set_context(self, context: CypherInvocationContext) -> None:
        self._server.context = context  # type: ignore[attr-defined]

    def has_open_transaction(self) -> bool:
        with self._server.open_transactions_lock:  # type: ignore[attr-defined]
            return bool(self._server.open_transactions)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class CypherInvocationContext:
    invocation_id: str
    action_revision_id: str
    application_id: str
    data_space_id: str = "production"
    deployment_id: str | None = None
    correlation_id: str | None = None
    actor: str = "action-runtime"
    checkpoint_id: str | None = None


@dataclass
class _ConnectionTransaction:
    uow: GraphUnitOfWork
    started_at: float = field(default_factory=time.monotonic)


class _CypherRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        gateway: CypherGateway = self.server.gateway  # type: ignore[attr-defined]
        token: str = self.server.token  # type: ignore[attr-defined]
        tx_timeout: float = self.server.tx_timeout_seconds  # type: ignore[attr-defined]
        connection_tx: _ConnectionTransaction | None = None
        previous_tx: _ConnectionTransaction | None = None
        try:
            while True:
                try:
                    request = _recv_message(self.connection)
                except Exception:  # noqa: BLE001 - closed child sockets end the handler.
                    break
                context: CypherInvocationContext = self.server.context  # type: ignore[attr-defined]
                response, connection_tx, done = gateway.handle_request(
                    context,
                    request,
                    expected_token=token,
                    connection_tx=connection_tx,
                    tx_timeout_seconds=tx_timeout,
                )
                self.server.track_transaction(previous_tx, connection_tx)  # type: ignore[attr-defined]
                previous_tx = connection_tx
                _send_message(self.connection, response)
                if done:
                    break
        finally:
            if connection_tx is not None:
                gateway.abort_transaction(connection_tx)
                self.server.track_transaction(connection_tx, None)  # type: ignore[attr-defined]


class _UnixGatewayServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def track_transaction(
        self,
        previous: _ConnectionTransaction | None,
        current: _ConnectionTransaction | None,
    ) -> None:
        with self.open_transactions_lock:  # type: ignore[attr-defined]
            if previous is None and current is not None:
                self.open_transactions.add(id(current))  # type: ignore[attr-defined]
            elif previous is not None and current is None:
                self.open_transactions.discard(id(previous))  # type: ignore[attr-defined]


class CypherGateway:
    """Invocation-scoped Cypher access over authenticated local IPC."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def available(self) -> bool:
        try:
            rows = self.store.run_cypher(AVAILABILITY_QUERY)
        except Exception:  # noqa: BLE001 - availability is a probe, not a query path.
            return False
        return bool(rows) and rows[0].get("ok") == 1

    @contextmanager
    def serve(
        self,
        context: CypherInvocationContext,
        *,
        tx_timeout_seconds: float = DEFAULT_TX_TIMEOUT_SECONDS,
    ) -> Iterator[CypherGatewaySession]:
        token = uuid.uuid4().hex
        with TemporaryDirectory(prefix="n4x-cypher-") as directory:
            socket_path = str(Path(directory) / "gateway.sock")
            server = _UnixGatewayServer(socket_path, _CypherRequestHandler)
            server.gateway = self  # type: ignore[attr-defined]
            server.context = context  # type: ignore[attr-defined]
            server.token = token  # type: ignore[attr-defined]
            server.tx_timeout_seconds = tx_timeout_seconds  # type: ignore[attr-defined]
            server.open_transactions = set()  # type: ignore[attr-defined]
            server.open_transactions_lock = threading.Lock()  # type: ignore[attr-defined]
            thread = threading.Thread(
                target=server.serve_forever,
                name="n4x-cypher-gateway",
                daemon=True,
            )
            thread.start()
            try:
                yield CypherGatewaySession(
                    CypherGatewayEndpoint(socket_path=socket_path, token=token),
                    server,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def handle_request(
        self,
        context: CypherInvocationContext,
        request: dict[str, Any],
        *,
        expected_token: str,
        connection_tx: _ConnectionTransaction | None,
        tx_timeout_seconds: float,
    ) -> tuple[dict[str, Any], _ConnectionTransaction | None, bool]:
        request_id = request.get("id") or str(uuid.uuid4())
        try:
            op = str(request.get("op") or "query")
            token = str(request.get("token") or "")
            if token != expected_token or not expected_token:
                raise CypherGatewayError("cypher gateway authentication failed")
            if connection_tx is not None:
                self._require_open_tx(connection_tx, tx_timeout_seconds)
            if op == "begin":
                if connection_tx is not None:
                    raise CypherGatewayError("cypher transaction is already open")
                connection_tx = self._begin_transaction()
                return {"id": request_id, "ok": True, "rows": []}, connection_tx, False
            if op == "commit":
                if connection_tx is None:
                    raise CypherGatewayError("cypher transaction is not open")
                self._commit_transaction(connection_tx)
                return {"id": request_id, "ok": True, "rows": []}, None, True
            if op == "rollback":
                if connection_tx is None:
                    raise CypherGatewayError("cypher transaction is not open")
                self.abort_transaction(connection_tx)
                return {"id": request_id, "ok": True, "rows": []}, None, True
            if op != "query":
                raise CypherGatewayError(f"unsupported cypher op: {op}")
            rows = self._execute_query(
                context,
                mode=str(request.get("mode") or "write"),
                query=str(request.get("query") or ""),
                params=dict(request.get("params") or {}),
                connection_tx=connection_tx,
            )
            return (
                {"id": request_id, "ok": True, "rows": rows},
                connection_tx,
                connection_tx is None,
            )
        except Exception as exc:  # noqa: BLE001 - child process receives structured errors.
            return (
                {
                    "id": request_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                connection_tx,
                connection_tx is None,
            )

    def abort_transaction(self, connection_tx: _ConnectionTransaction) -> None:
        try:
            connection_tx.uow.rollback()
        except Exception:  # noqa: BLE001 - disconnect rollback is best-effort.
            try:
                connection_tx.uow.__exit__(
                    CypherGatewayError,
                    CypherGatewayError("cypher transaction aborted"),
                    None,
                )
            except Exception:
                pass

    def _begin_transaction(self) -> _ConnectionTransaction:
        uow = GraphUnitOfWork(self.store)
        uow.begin()
        return _ConnectionTransaction(uow=uow)

    def _commit_transaction(self, connection_tx: _ConnectionTransaction) -> None:
        connection_tx.uow.commit()

    def _require_open_tx(
        self, connection_tx: _ConnectionTransaction, tx_timeout_seconds: float
    ) -> None:
        if time.monotonic() - connection_tx.started_at > tx_timeout_seconds:
            self.abort_transaction(connection_tx)
            raise CypherGatewayError(
                f"cypher transaction exceeded {tx_timeout_seconds} seconds"
            )

    def _execute_query(
        self,
        context: CypherInvocationContext,
        *,
        mode: str,
        query: str,
        params: dict[str, Any],
        connection_tx: _ConnectionTransaction | None,
    ) -> list[dict[str, Any]]:
        if mode not in {"read", "write"}:
            raise CypherGatewayError(f"unsupported cypher mode: {mode}")
        if not query.strip():
            raise CypherGatewayError("cypher query is required")
        if mode == "read" and WRITE_QUERY.search(query):
            raise CypherGatewayError("write clauses are not allowed in read mode")
        if connection_tx is not None:
            return self._run_and_audit(connection_tx.uow, context, mode, query, params)
        uow = GraphUnitOfWork(self.store)
        with uow:
            return self._run_and_audit(uow, context, mode, query, params)

    def _run_and_audit(
        self,
        uow: GraphUnitOfWork,
        context: CypherInvocationContext,
        mode: str,
        query: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = self.store.run_cypher(query, params)
        audit = CypherAuditRecord(
            id=str(uuid.uuid4()),
            invocation_id=context.invocation_id,
            action_revision_id=context.action_revision_id,
            application_id=context.application_id,
            data_space_id=context.data_space_id,
            deployment_id=context.deployment_id,
            correlation_id=context.correlation_id,
            actor=context.actor,
            mode=mode,  # type: ignore[arg-type]
            query=query,
            query_hash=sha256_text(query),
            params_hash=sha256_json(params),
            checkpoint_id=context.checkpoint_id,
            row_count=len(rows),
            created_at=now_utc(),
        )
        uow.records.cypher_audits.save(audit)
        if uow.records.invocations.get(context.invocation_id) is not None:
            self.store.create_edge(
                node_ref("Invocation", id=context.invocation_id),
                "HAS_CYPHER_AUDIT",
                node_ref("CypherAuditRecord", id=audit.id),
            )
        self.store.create_edge(
            node_ref("Application", id=context.application_id),
            "HAS_CYPHER_AUDIT",
            node_ref("CypherAuditRecord", id=audit.id),
        )
        if context.checkpoint_id is not None:
            self.store.create_edge(
                node_ref("CypherAuditRecord", id=audit.id),
                "AT_CHECKPOINT",
                node_ref("GraphCheckpoint", id=context.checkpoint_id),
            )
        return rows

    def subprocess_env(
        self,
        endpoint: CypherGatewayEndpoint,
        context: CypherInvocationContext,
    ) -> dict[str, str]:
        env = {
            "N4X_CYPHER_SOCKET": endpoint.socket_path,
            "N4X_CYPHER_TOKEN": endpoint.token,
            "N4X_ACTION_CONTEXT_VERSION": ACTION_CONTEXT_VERSION,
            "N4X_SUBPROCESS_PROTOCOL_VERSION": SUBPROCESS_PROTOCOL_VERSION,
            "N4X_INVOCATION_ID": context.invocation_id,
            "N4X_APPLICATION_ID": context.application_id,
            "N4X_DATA_SPACE_ID": context.data_space_id,
            "N4X_ACTION_REVISION_ID": context.action_revision_id,
            "N4X_ACTOR": context.actor,
        }
        if context.checkpoint_id is not None:
            env["N4X_CHECKPOINT_ID"] = context.checkpoint_id
        if context.deployment_id is not None:
            env["N4X_DEPLOYMENT_ID"] = context.deployment_id
        if context.correlation_id is not None:
            env["N4X_CORRELATION_ID"] = context.correlation_id
        if os.getenv("N4X_CYPHER_GATEWAY_REQUIRED"):
            env["N4X_CYPHER_GATEWAY_REQUIRED"] = os.environ[
                "N4X_CYPHER_GATEWAY_REQUIRED"
            ]
        return env
