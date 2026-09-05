from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from n4x.contracts import ACTION_CONTEXT_VERSION, SUBPROCESS_PROTOCOL_VERSION
from n4x.contracts.file_delivery import FILE_DELIVERY_SIGNING_KEY_ENV
from n4x.graph.bindings import RevisionBindings
from n4x.graph.store import GraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ActionExecutionError
from n4x.kernel.hash import sha256_json
from n4x.kernel.paths import resolve_path_within
from n4x.kernel.models import (
    ActionRevision,
    BuildArtifact,
    BuildInvocation,
    ExecutionContext,
    Invocation,
    now_utc,
)
from n4x.runtime.action_pool import ActionPool, PooledChild
from n4x.runtime.cypher_gateway import (
    CypherGateway,
    CypherInvocationContext,
)
from n4x.runtime.environments import PythonEnvironmentManager
from n4x.secrets.service import SecretService
from n4x.source_store.service import SourceStore

ACTION_LOG_LIMIT_CHARS = int(os.getenv("N4X_ACTION_LOG_LIMIT_CHARS", "1000000"))


@dataclass
class RuntimePaths:
    root: Path

    @classmethod
    def default(cls) -> "RuntimePaths":
        from n4x.secrets.paths import default_application_runtime_root

        root = default_application_runtime_root()
        root.mkdir(parents=True, exist_ok=True)
        return cls(root)

    @classmethod
    def temporary(cls) -> "RuntimePaths":
        import tempfile

        return cls(Path(tempfile.mkdtemp(prefix="n4x-runtime-")))

    @staticmethod
    def application_data_scope_id(data_scope_key: str) -> str:
        if not isinstance(data_scope_key, str) or not data_scope_key:
            raise ValueError("data_scope_key must be a non-empty string")
        return hashlib.sha256(data_scope_key.encode("utf-8")).hexdigest()

    @staticmethod
    def application_data_scope_key(
        application_id: str, data_space_id: str
    ) -> str:
        return (
            application_id
            if data_space_id == "production"
            else f"{application_id}\x1f{data_space_id}"
        )

    def application_data_root(self, data_scope_key: str) -> Path:
        """Return the opaque persistent mount for one Application data scope."""
        data_root = self.root / "application-data"
        data_root.mkdir(parents=True, exist_ok=True)
        opaque_segment = self.application_data_scope_id(data_scope_key)
        scope_root = resolve_path_within(data_root, opaque_segment)
        scope_root.mkdir(parents=True, exist_ok=True)
        return scope_root

    def purge_application_data_scope(self, data_scope_key: str) -> None:
        data_root = self.root / "application-data"
        opaque_segment = self.application_data_scope_id(data_scope_key)
        scope_root = resolve_path_within(data_root, opaque_segment)
        if scope_root.exists():
            shutil.rmtree(scope_root)


class ActionRuntime:
    def __init__(
        self,
        source: SourceStore,
        secrets: SecretService,
        graph_store: GraphStore,
        paths: RuntimePaths | None = None,
        uow: GraphUnitOfWork | None = None,
        cypher_gateway: CypherGateway | None = None,
    ) -> None:
        self.store = graph_store
        self.uow = uow or GraphUnitOfWork(graph_store)
        self.graph = self.uow.records
        self.source = source
        self.secrets = secrets
        self.paths = paths or RuntimePaths.default()
        self.cypher_gateway = cypher_gateway or CypherGateway(graph_store)
        self.python_environments = PythonEnvironmentManager(
            self.paths.root, self.store, self.uow
        )
        self.pool = ActionPool()
        self._materialization_locks_guard = threading.Lock()
        self._materialization_locks: dict[str, threading.Lock] = {}
        self.bindings = RevisionBindings(self.uow)

    def shutdown(self) -> None:
        self.pool.shutdown()

    def import_check(
        self, action_revision: ActionRevision, *, application_revision_id: str
    ) -> None:
        self.uow.require_inactive("check action import")
        module_path, function_name = self._split_entrypoint(action_revision.entrypoint)
        materialized = self.materialize(
            action_revision, application_revision_id=application_revision_id
        )
        environment = self.python_environments.prepare(application_revision_id)
        revision = self.graph.revisions[application_revision_id]
        self._run_child(
            action_revision,
            materialized / module_path,
            function_name,
            {},
            environment.python_executable,
            timeout_seconds=action_revision.timeout_seconds,
            check_only=True,
            execution_context=ExecutionContext(
                correlation_id=str(uuid.uuid4()),
                application_revision_id=revision.id,
                application_id=revision.application_id,
            ),
        )

    def run(
        self,
        action_revision: ActionRevision,
        input_value: dict[str, Any],
        invocation_kind: str = "draft",
        *,
        invocation_id: str | None = None,
        execution_context: ExecutionContext | None = None,
        cancellation_event: threading.Event | None = None,
        log_callback: Callable[[str, str], None] | None = None,
    ) -> Invocation:
        self.uow.require_inactive("run action")
        invocation_id = invocation_id or str(uuid.uuid4())
        execution_context = self.resolve_execution_context(
            action_revision,
            invocation_id,
            execution_context,
        )
        redaction_values = self._redaction_values(action_revision)
        try:
            self._validate_input(action_revision.input_schema, input_value)
            module_path, function_name = self._split_entrypoint(
                action_revision.entrypoint
            )
            materialized = self.materialize(
                action_revision,
                application_revision_id=execution_context.application_revision_id,
            )
            environment = self.python_environments.prepare(
                execution_context.application_revision_id
            )
            process_result = self._run_child(
                action_revision,
                materialized / module_path,
                function_name,
                input_value,
                environment.python_executable,
                timeout_seconds=action_revision.timeout_seconds,
                invocation_id=invocation_id,
                execution_context=execution_context,
                cancellation_event=cancellation_event,
                log_callback=(
                    None
                    if log_callback is None
                    else lambda stdout, stderr: log_callback(
                        self._bounded_log(
                            self._redact(
                                action_revision,
                                stdout,
                                redaction_values,
                            )
                        ),
                        self._bounded_log(
                            self._redact(
                                action_revision,
                                stderr,
                                redaction_values,
                            )
                        ),
                    )
                ),
            )
            output = process_result["output"]
            metadata = {}
            pid = process_result.get("pid")
            if pid is not None:
                metadata["pid"] = pid
            invocation = Invocation(
                id=invocation_id,
                action_revision_id=action_revision.id,
                data_space_id=execution_context.data_space_id,
                deployment_id=execution_context.deployment_id,
                correlation_id=execution_context.correlation_id,
                invocation_kind=invocation_kind,  # type: ignore[arg-type]
                status="succeeded",
                metadata=metadata,
                input=self._redact_data(action_revision, input_value, redaction_values),
                output=self._redact_data(action_revision, output, redaction_values),
                stdout=self._bounded_log(
                    self._redact(
                        action_revision,
                        process_result["stdout"],
                        redaction_values,
                    )
                ),
                stderr=self._bounded_log(
                    self._redact(
                        action_revision,
                        process_result["stderr"],
                        redaction_values,
                    )
                ),
                completed_at=now_utc(),
                last_heartbeat_at=now_utc(),
            )
            with self.uow:
                self.graph.invocations[invocation.id] = invocation
                self._link_invocation(action_revision, invocation.id)
                self._link_cypher_audits(invocation.id)
        except Exception as exc:  # noqa: BLE001 - invocation must capture trusted app failures.
            cancelled = (
                cancellation_event is not None
                and cancellation_event.is_set()
            )
            error_code = (
                "action_cancelled"
                if cancelled
                else getattr(exc, "code", None)
            )
            metadata = {}
            if error_code:
                metadata["error_code"] = error_code
            invocation = Invocation(
                id=invocation_id,
                action_revision_id=action_revision.id,
                data_space_id=execution_context.data_space_id,
                deployment_id=execution_context.deployment_id,
                correlation_id=execution_context.correlation_id,
                invocation_kind=invocation_kind,  # type: ignore[arg-type]
                status="cancelled" if cancelled else "failed",
                input=self._redact_data(action_revision, input_value, redaction_values),
                error=self._redact(
                    action_revision,
                    f"{type(exc).__name__}: {exc}",
                    redaction_values,
                ),
                metadata=metadata,
                stdout=self._bounded_log(
                    self._redact(action_revision, "", redaction_values)
                ),
                stderr=self._bounded_log(
                    self._redact(
                        action_revision,
                        traceback.format_exc(),
                        redaction_values,
                    )
                ),
                completed_at=now_utc(),
                last_heartbeat_at=now_utc(),
            )
            with self.uow:
                self.graph.invocations[invocation.id] = invocation
                self._link_invocation(action_revision, invocation.id)
                self._link_cypher_audits(invocation.id)
        return invocation

    def resolve_execution_context(
        self,
        action_revision: ActionRevision,
        invocation_id: str,
        context: ExecutionContext | None,
    ) -> ExecutionContext:
        if context is None:
            raise ActionExecutionError("ExecutionContext is required")
        revision = self.graph.revisions[context.application_revision_id]
        if context.application_id != revision.application_id:
            raise ActionExecutionError(
                "ExecutionContext does not match the ApplicationRevision"
            )
        data_space = self.graph.data_spaces.get(
            (context.application_id, context.data_space_id)
        )
        if data_space is None:
            raise ActionExecutionError(
                "ExecutionContext references an unknown DataSpace"
            )
        if context.mode == "production" and (
            data_space.kind != "production"
            or context.deployment_id is not None
        ):
            raise ActionExecutionError(
                "production ExecutionContext requires the production DataSpace"
            )
        if context.mode == "development" and (
            data_space.kind != "development"
            or context.deployment_id is None
        ):
            raise ActionExecutionError(
                "development ExecutionContext requires a deployment and "
                "development DataSpace"
            )
        return context

    def materialize(
        self, action_revision: ActionRevision, *, application_revision_id: str
    ) -> Path:
        self.uow.require_inactive("materialize action source")
        tree = self.bindings.tree(application_revision_id)
        lock_key = f"{action_revision.id}:{tree.tree_hash}"
        with self._materialization_locks_guard:
            lock = self._materialization_locks.setdefault(lock_key, threading.Lock())
        with lock:
            materialized_files: list[dict[str, str]] = []
            for source_path in action_revision.source_paths:
                file = self.source.read_source_file(tree.id, source_path)
                materialized_files.append(
                    {"path": file.path, "hash": file.content_hash}
                )
            input_hash = sha256_json(
                {
                    "action_revision_id": action_revision.id,
                    "files": materialized_files,
                }
            )
            target = (
                self.paths.root / "actions" / input_hash.replace(":", "-")
            )
            target.mkdir(parents=True, exist_ok=True)
            for source_path in action_revision.source_paths:
                file = self.source.read_source_file(tree.id, source_path)
                destination = target / file.path
                destination.parent.mkdir(parents=True, exist_ok=True)
                if (
                    not destination.exists()
                    or destination.read_text(encoding="utf-8")
                    != file.content
                ):
                    destination.write_text(file.content, encoding="utf-8")
            self._record_materialization(
                action_revision,
                application_revision_id,
                target,
                materialized_files,
                input_hash,
            )
            return target

    def _record_materialization(
        self,
        action_revision: ActionRevision,
        application_revision_id: str,
        target: Path,
        materialized_files: list[dict[str, str]],
        input_hash: str,
    ) -> None:
        with self.uow:
            existing = [
                artifact
                for artifact in self.graph.build_artifacts.values()
                if artifact.artifact_type == "materialized_source"
                and artifact.owner_id == application_revision_id
                and artifact.content_hash == input_hash
            ]
            if existing:
                return
            invocation_id = str(uuid.uuid4())
            invocation = BuildInvocation(
                id=invocation_id,
                owner_kind="ApplicationRevision",
                owner_id=application_revision_id,
                kind="source_materialization",
                status="succeeded",
                input_hash=input_hash,
                command=[],
                cwd=str(target),
                completed_at=now_utc(),
            )
            artifact_id = str(uuid.uuid4())
            artifact = BuildArtifact(
                id=artifact_id,
                owner_kind="ApplicationRevision",
                owner_id=application_revision_id,
                build_invocation_id=invocation.id,
                artifact_type="materialized_source",
                path=str(target),
                content_hash=input_hash,
                metadata={
                    "action_revision_id": action_revision.id,
                    "files": materialized_files,
                },
            )
            self.graph.build_invocations[invocation.id] = invocation
            self.graph.build_artifacts[artifact.id] = artifact
            self.store.create_edge(
                node_ref(artifact.owner_kind, id=artifact.owner_id),
                "HAS_BUILD_ARTIFACT",
                node_ref("BuildArtifact", id=artifact.id),
            )
            self.store.create_edge(
                node_ref("ApplicationRevision", id=application_revision_id),
                "HAS_BUILD_INVOCATION",
                node_ref("BuildInvocation", id=invocation.id),
            )
            self.store.create_edge(
                node_ref("BuildInvocation", id=invocation.id),
                "PRODUCED",
                node_ref("BuildArtifact", id=artifact.id),
            )

    def _run_child(
        self,
        action_revision: ActionRevision,
        module_path: Path,
        function_name: str,
        input_value: dict[str, Any],
        python_executable: Path,
        *,
        timeout_seconds: int,
        check_only: bool = False,
        invocation_id: str | None = None,
        execution_context: ExecutionContext | None = None,
        cancellation_event: threading.Event | None = None,
        log_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        self.uow.require_inactive("wait for action subprocess")
        result_path = self.paths.root / "action-results" / f"{uuid.uuid4()}.json"
        invocation_id = invocation_id or str(uuid.uuid4())
        execution_context = self.resolve_execution_context(
            action_revision,
            invocation_id,
            execution_context,
        )
        revision = self.graph.revisions[execution_context.application_revision_id]
        secrets_path = self._secrets_path_for(
            action_revision,
            execution_context,
            check_only=check_only,
        )
        data_scope_key = self.paths.application_data_scope_key(
            revision.application_id,
            execution_context.data_space_id,
        )
        application_data_root = self.paths.application_data_root(
            data_scope_key
        )
        input_path = self._write_json_payload("action-inputs", input_value)
        context = CypherInvocationContext(
            invocation_id=invocation_id,
            action_revision_id=action_revision.id,
            application_id=revision.application_id,
            data_space_id=execution_context.data_space_id,
            deployment_id=execution_context.deployment_id,
            correlation_id=execution_context.correlation_id,
            checkpoint_id=(
                self._latest_checkpoint_id(revision.application_id)
                if execution_context.mode == "production"
                else None
            ),
        )
        key = (
            revision.id,
            execution_context.data_space_id,
            str(python_executable),
        )
        child = self.pool.checkout(
            key,
            lambda: self._spawn_child(
                key,
                python_executable,
                application_data_root,
                context,
            ),
        )
        retain = False
        finished = False
        try:
            child.session.set_context(context)
            request: dict[str, Any] = {
                "module": str(module_path),
                "function": function_name,
                "input_path": str(input_path),
                "result_path": str(result_path),
                "check_only": check_only,
            }
            if secrets_path is not None:
                request["secrets_path"] = str(secrets_path)
            marks = child.log_marks()
            if child.process.stdin is None:
                raise ActionExecutionError("action process stdin is closed")
            child.process.stdin.write(json.dumps(request) + "\n")
            child.process.stdin.flush()
            stdout, stderr = self._wait_rpc(
                child,
                result_path,
                timeout_seconds=timeout_seconds,
                cancellation_event=cancellation_event,
                log_callback=log_callback,
                marks=marks,
            )
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            payload["stdout"] = stdout
            payload["stderr"] = stderr + payload.get("traceback", "")
            leaked = child.session.has_open_transaction()
            retain = child.alive() and not leaked
            finished = True
            if not payload.get("ok"):
                raise ActionExecutionError(
                    payload.get("error", "action failed")
                )
            return payload
        except BaseException:
            if not finished:
                retain = False
            raise
        finally:
            self.pool.release(child, retain=retain)
            if secrets_path is not None and secrets_path.exists():
                secrets_path.unlink()
            if input_path.exists():
                input_path.unlink()
            if result_path.exists():
                result_path.unlink()

    def _spawn_child(
        self,
        key: tuple[str, str, str],
        python_executable: Path,
        application_data_root: Path,
        context: CypherInvocationContext,
    ) -> PooledChild:
        if not self.cypher_gateway.available():
            raise ActionExecutionError(
                "Cypher gateway is unavailable",
                code="cypher_gateway_unavailable",
            )
        inherited_env = dict(os.environ)
        inherited_env.pop(FILE_DELIVERY_SIGNING_KEY_ENV, None)
        materialized = inherited_env.get("N4X_SYSTEM_MATERIALIZED_ROOT", "").strip()
        if materialized:
            existing = inherited_env.get("PYTHONPATH", "")
            inherited_env["PYTHONPATH"] = (
                materialized
                if not existing
                else f"{materialized}{os.pathsep}{existing}"
            )
        session_cm = self.cypher_gateway.serve(context)
        session = session_cm.__enter__()
        env = {
            **inherited_env,
            "PYTHONUNBUFFERED": "1",
            "N4X_APPLICATION_DATA_ROOT": str(application_data_root),
            "N4X_ACTION_CONTEXT_VERSION": ACTION_CONTEXT_VERSION,
            "N4X_SUBPROCESS_PROTOCOL_VERSION": SUBPROCESS_PROTOCOL_VERSION,
            "N4X_APPLICATION_ID": context.application_id,
            "N4X_DATA_SPACE_ID": context.data_space_id,
            **self.cypher_gateway.subprocess_env(session.endpoint, context),
        }
        runner = Path(__file__).with_name("action_runner.py")
        try:
            process = subprocess.Popen(
                [str(python_executable), str(runner)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            session_cm.__exit__(None, None, None)
            if exc.errno == errno.E2BIG:
                raise ActionExecutionError(
                    "action argument list is too long",
                    code="exec_argument_list_too_long",
                ) from exc
            raise
        return PooledChild(
            key,
            process,
            session,
            lambda: session_cm.__exit__(None, None, None),
        )

    def _wait_rpc(
        self,
        child: PooledChild,
        result_path: Path,
        *,
        timeout_seconds: int,
        cancellation_event: threading.Event | None,
        log_callback: Callable[[str, str], None] | None,
        marks: tuple[int, int],
    ) -> tuple[str, str]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancellation_event is not None and cancellation_event.is_set():
                raise ActionExecutionError(
                    "action cancelled", code="action_cancelled"
                )
            stdout, stderr = child.logs_since(marks)
            if log_callback is not None:
                log_callback(stdout, stderr)
            if result_path.exists():
                return child.logs_since(marks)
            if child.process.poll() is not None:
                raise ActionExecutionError(
                    f"action process produced no result; "
                    f"exit={child.process.returncode}; stderr={stderr}"
                )
            if time.monotonic() >= deadline:
                raise ActionExecutionError(
                    f"action timed out after {timeout_seconds} seconds",
                    code="action_timed_out",
                )
            time.sleep(0.05)

    @staticmethod
    def _bounded_log(value: str) -> str:
        if len(value) <= ACTION_LOG_LIMIT_CHARS:
            return value
        omitted = len(value) - ACTION_LOG_LIMIT_CHARS
        return f"[... {omitted} earlier characters omitted ...]\n" + value[
            -ACTION_LOG_LIMIT_CHARS:
        ]

    def _secrets_path_for(
        self,
        action_revision: ActionRevision,
        execution_context: ExecutionContext,
        *,
        check_only: bool,
    ) -> Path | None:
        if check_only:
            return None
        if execution_context.mode == "development":
            deployment = self.graph.development_deployments.get(
                execution_context.deployment_id or ""
            )
            if deployment is None:
                return None
            allowed = set(deployment.secret_reference_ids)
            refs = [
                reference_id
                for reference_id in action_revision.secret_refs
                if reference_id in allowed
            ]
            return self._write_secret_payload(action_revision, secret_refs=refs)
        return self._write_secret_payload(action_revision)

    def _write_secret_payload(
        self,
        action_revision: ActionRevision,
        *,
        secret_refs: list[str] | None = None,
    ) -> Path | None:
        refs = action_revision.secret_refs if secret_refs is None else secret_refs
        if not refs:
            return None
        values = {
            secret.uri: secret.value
            for secret in self.secrets.allowed_secret_values(
                refs,
                require_values=False,
            )
        }
        if not values:
            return None
        return self._write_json_payload("action-secrets", values)

    def _write_json_payload(self, directory: str, payload: Any) -> Path:
        path = self.paths.root / directory / f"{uuid.uuid4()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file)
        return path

    def _link_invocation(
        self, action_revision: ActionRevision, invocation_id: str
    ) -> None:
        self.store.create_edge(
            node_ref("ActionRevision", id=action_revision.id),
            "HAS_INVOCATION",
            node_ref("Invocation", id=invocation_id),
        )
        self.store.create_edge(
            node_ref("Invocation", id=invocation_id),
            "RAN",
            node_ref("ActionRevision", id=action_revision.id),
        )

    def _link_cypher_audits(self, invocation_id: str) -> None:
        for audit in self.graph.cypher_audits.values():
            if audit.invocation_id != invocation_id:
                continue
            self.store.create_edge(
                node_ref("Invocation", id=invocation_id),
                "HAS_CYPHER_AUDIT",
                node_ref("CypherAuditRecord", id=audit.id),
            )

    def _latest_checkpoint_id(self, application_id: str) -> str | None:
        checkpoints = [
            checkpoint
            for checkpoint in self.graph.checkpoints.values()
            if checkpoint.application_id == application_id
        ]
        if not checkpoints:
            return None
        return sorted(checkpoints, key=lambda item: item.created_at)[-1].id

    def _split_entrypoint(self, entrypoint: str) -> tuple[str, str]:
        if ":" not in entrypoint:
            raise ValueError("entrypoint must be formatted as path.py:function")
        module_path, function_name = entrypoint.split(":", 1)
        return module_path, function_name

    def _validate_input(self, schema: dict[str, Any], value: dict[str, Any]) -> None:
        if not schema:
            return
        if schema.get("type") == "object" and not isinstance(value, dict):
            raise ValueError("input must be an object")
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise ValueError(f"missing required input key: {key}")

    def _redaction_values(self, action_revision: ActionRevision) -> list[str]:
        try:
            return [
                secret.value
                for secret in self.secrets.allowed_secret_values(
                    action_revision.secret_refs,
                    require_values=False,
                )
                if secret.value
            ]
        except Exception:  # noqa: BLE001 - redaction must not hide runtime errors.
            return []

    def _redact(
        self,
        action_revision: ActionRevision,
        value: str,
        secret_values: list[str] | None = None,
    ) -> str:
        redacted = value
        values = (
            self._redaction_values(action_revision)
            if secret_values is None
            else secret_values
        )
        for secret_value in values:
            redacted = redacted.replace(secret_value, "[REDACTED]")
        return redacted

    def _redact_data(
        self,
        action_revision: ActionRevision,
        value: Any,
        secret_values: list[str] | None = None,
    ) -> Any:
        if isinstance(value, str):
            return self._redact(action_revision, value, secret_values)
        if isinstance(value, list):
            return [
                self._redact_data(action_revision, item, secret_values)
                for item in value
            ]
        if isinstance(value, dict):
            return {
                key: self._redact_data(action_revision, item, secret_values)
                for key, item in value.items()
            }
        return value
