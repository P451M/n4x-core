from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import (
    ActionRevision,
    ExecutionContext,
    Invocation,
    now_utc,
)
from n4x.runtime.actions import ActionRuntime


DEFAULT_ACTION_WORKERS = int(os.getenv("N4X_ACTION_WORKERS", "4"))
DEFAULT_ACTION_QUEUE_CAPACITY = int(os.getenv("N4X_ACTION_QUEUE_CAPACITY", "32"))
DEFAULT_ACTION_HEARTBEAT_SECONDS = float(
    os.getenv("N4X_ACTION_HEARTBEAT_SECONDS", "5")
)


class ActionSupervisor:
    """Bounded in-process admission and lifecycle for Action subprocess work."""

    def __init__(
        self,
        runtime: ActionRuntime,
        uow: GraphUnitOfWork,
        *,
        max_workers: int = DEFAULT_ACTION_WORKERS,
        queue_capacity: int = DEFAULT_ACTION_QUEUE_CAPACITY,
        heartbeat_interval_seconds: float = DEFAULT_ACTION_HEARTBEAT_SECONDS,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if queue_capacity < 0:
            raise ValueError("queue_capacity must not be negative")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        self.runtime = runtime
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.python_environments = runtime.python_environments
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="n4x-action",
        )
        self._capacity = threading.BoundedSemaphore(max_workers + queue_capacity)
        self._futures: dict[str, Future[Invocation]] = {}
        self._cancellations: dict[str, threading.Event] = {}
        self._action_ids: dict[str, str] = {}
        self._lock = threading.Lock()
        self._closed = False

    def run(
        self,
        action_revision: ActionRevision,
        input_value: dict[str, Any],
        invocation_kind: str = "draft",
        *,
        invocation_id: str | None = None,
        execution_context: ExecutionContext | None = None,
        heartbeat_callback: Callable[[], None] | None = None,
    ) -> Invocation:
        queued = self.submit(
            action_revision,
            input_value,
            invocation_kind=invocation_kind,
            invocation_id=invocation_id,
            execution_context=execution_context,
            heartbeat_callback=heartbeat_callback,
        )
        return self.await_invocation(queued.id)

    def submit(
        self,
        action_revision: ActionRevision,
        input_value: dict[str, Any],
        invocation_kind: str = "draft",
        *,
        invocation_id: str | None = None,
        execution_context: ExecutionContext | None = None,
        heartbeat_callback: Callable[[], None] | None = None,
    ) -> Invocation:
        self.uow.require_inactive("submit action")
        with self._lock:
            if self._closed:
                raise ValidationFailure("action supervisor is shut down")
        if not self._capacity.acquire(blocking=False):
            raise ValidationFailure("action supervisor queue is full")

        try:
            resolved_invocation_id = invocation_id or str(uuid.uuid4())
            execution_context = self.runtime.resolve_execution_context(
                action_revision,
                resolved_invocation_id,
                execution_context,
            )
            queued = Invocation(
                id=resolved_invocation_id,
                action_revision_id=action_revision.id,
                data_space_id=execution_context.data_space_id,
                deployment_id=execution_context.deployment_id,
                correlation_id=execution_context.correlation_id,
                invocation_kind=invocation_kind,  # type: ignore[arg-type]
                status="queued",
                input={},
                metadata={"input_redaction_pending": True},
                started_at=None,
                completed_at=None,
                last_heartbeat_at=now_utc(),
            )
            with self._lock:
                if self._closed:
                    raise ValidationFailure(
                        "action supervisor is shut down"
                    )
                if (
                    action_revision.concurrency_policy
                    == "reject_if_running"
                    and action_revision.action_id
                    in self._action_ids.values()
                ):
                    raise ValidationFailure(
                        "Action concurrency policy rejects overlapping "
                        f"invocation: {action_revision.action_id}"
                    )
                self._action_ids[queued.id] = action_revision.action_id
            self._persist(queued)
            cancellation = threading.Event()
            future = self._executor.submit(
                self._execute,
                queued,
                action_revision,
                input_value,
                cancellation,
                execution_context,
                heartbeat_callback,
            )
        except BaseException:
            with self._lock:
                if "queued" in locals():
                    self._action_ids.pop(queued.id, None)
            self._capacity.release()
            raise
        with self._lock:
            self._futures[queued.id] = future
            self._cancellations[queued.id] = cancellation
        future.add_done_callback(
            lambda _: self._complete_submission(queued.id)
        )
        return queued

    def inspect(self, invocation_id: str) -> Invocation:
        with self.uow:
            return self.records.invocations[invocation_id]

    def await_invocation(
        self, invocation_id: str, timeout: float | None = None
    ) -> Invocation:
        with self._lock:
            future = self._futures.get(invocation_id)
        if future is None:
            return self.inspect(invocation_id)
        return future.result(timeout=timeout)

    def cancel(self, invocation_id: str) -> Invocation:
        with self._lock:
            future = self._futures.get(invocation_id)
            cancellation = self._cancellations.get(invocation_id)
        if future is None:
            return self.inspect(invocation_id)
        if not future.cancel():
            if cancellation is None:
                raise ValidationFailure("running Action cannot be cancelled")
            cancellation.set()
            return future.result()
        current = self.inspect(invocation_id)
        cancelled = current.model_copy(
            update={
                "status": "cancelled",
                "completed_at": now_utc(),
                "last_heartbeat_at": now_utc(),
                "metadata": {
                    **current.metadata,
                    "input_redaction_pending": False,
                },
            }
        )
        self._persist(cancelled)
        return cancelled

    def cancel_deployment(self, deployment_id: str) -> list[Invocation]:
        invocation_ids = [
            invocation.id
            for invocation in self.records.invocations.values()
            if invocation.deployment_id == deployment_id
            and invocation.status in {"queued", "running"}
        ]
        return [
            self.cancel(invocation_id)
            for invocation_id in invocation_ids
        ]

    def import_check(
        self, action_revision: ActionRevision, *, application_revision_id: str
    ) -> None:
        self.runtime.import_check(
            action_revision, application_revision_id=application_revision_id
        )

    def materialize(
        self, action_revision: ActionRevision, *, application_revision_id: str
    ):
        return self.runtime.materialize(
            action_revision, application_revision_id=application_revision_id
        )

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _execute(
        self,
        queued: Invocation,
        action_revision: ActionRevision,
        input_value: dict[str, Any],
        cancellation_event: threading.Event,
        execution_context: ExecutionContext,
        heartbeat_callback: Callable[[], None] | None,
    ) -> Invocation:
        running = queued.model_copy(
            update={
                "status": "running",
                "started_at": now_utc(),
                "last_heartbeat_at": now_utc(),
            }
        )
        self._persist(running)
        live_logs = {"stdout": "", "stderr": ""}
        last_heartbeat = time.monotonic()

        def capture_logs(stdout: str, stderr: str) -> None:
            nonlocal last_heartbeat
            live_logs["stdout"] = stdout
            live_logs["stderr"] = stderr
            self._update_logs(queued.id, stdout, stderr)
            now = time.monotonic()
            if (
                heartbeat_callback is not None
                and now - last_heartbeat >= self.heartbeat_interval_seconds
            ):
                heartbeat_callback()
                last_heartbeat = now

        try:
            completed = self.runtime.run(
                action_revision,
                input_value,
                invocation_kind=queued.invocation_kind,
                invocation_id=queued.id,
                execution_context=execution_context,
                cancellation_event=cancellation_event,
                log_callback=capture_logs,
            )
            if live_logs["stdout"] or live_logs["stderr"]:
                completed = completed.model_copy(
                    update={
                        "stdout": completed.stdout or live_logs["stdout"],
                        "stderr": completed.stderr or live_logs["stderr"],
                    }
                )
                self._persist(completed)
            return completed
        except BaseException as exc:
            failed = running.model_copy(
                update={
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "stderr": traceback.format_exc(),
                    "completed_at": now_utc(),
                    "last_heartbeat_at": now_utc(),
                }
            )
            self._persist(failed)
            return failed

    def _update_logs(
        self, invocation_id: str, stdout: str, stderr: str
    ) -> None:
        current = self.inspect(invocation_id)
        if current.status != "running":
            return
        self._persist(
            current.model_copy(
                update={
                    "stdout": stdout,
                    "stderr": stderr,
                    "last_heartbeat_at": now_utc(),
                }
            )
        )

    def _complete_submission(self, invocation_id: str) -> None:
        self._capacity.release()
        with self._lock:
            self._futures.pop(invocation_id, None)
            self._cancellations.pop(invocation_id, None)
            self._action_ids.pop(invocation_id, None)

    def _persist(self, invocation: Invocation) -> None:
        with self.uow:
            self.records.invocations.save(invocation)
            self.store.create_edge(
                node_ref("ActionRevision", id=invocation.action_revision_id),
                "HAS_INVOCATION",
                node_ref("Invocation", id=invocation.id),
            )
            self.store.create_edge(
                node_ref("Invocation", id=invocation.id),
                "RAN",
                node_ref("ActionRevision", id=invocation.action_revision_id),
            )
