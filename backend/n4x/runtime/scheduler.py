from __future__ import annotations

import os
import uuid
from datetime import timedelta
from functools import wraps
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from n4x.graph.bindings import RevisionBindings
from n4x.graph.store import GraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.models import ExecutionContext
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.hash import sha256_json
from n4x.kernel.models import JobAttempt, JobRecord, TriggerRevision, now_utc
from n4x.runtime.actions import ActionRuntime

LEASE_SECONDS = int(os.getenv("N4X_JOB_LEASE_SECONDS", "30"))
DUE_WORK_INTERVAL_SECONDS = int(os.getenv("N4X_JOB_DUE_WORK_SECONDS", "1"))


def _transactional(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.uow:
            return method(self, *args, **kwargs)

    return wrapper


class TriggerRuntime:
    def __init__(
        self,
        actions: ActionRuntime,
        graph_store: GraphStore,
        uow: GraphUnitOfWork | None = None,
    ) -> None:
        self.actions = actions
        self.store = graph_store
        self.uow = uow or GraphUnitOfWork(graph_store)
        self.graph = self.uow.records
        self.bindings = RevisionBindings(self.uow)
        self._scheduler: BackgroundScheduler | None = None
        self.lease_owner = f"n4x-local:{os.getpid()}"

    def start(self) -> None:
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler()
            self._scheduler.start(paused=True)
        self.remount()
        self.recover_expired_leases()
        self.process_due_work()
        self._scheduler.resume()

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def remount(self) -> None:
        if self._scheduler is None:
            return
        self._scheduler.remove_all_jobs()
        self._scheduler.add_job(
            self.process_due_work,
            IntervalTrigger(seconds=DUE_WORK_INTERVAL_SECONDS),
            id="n4x-job-due-work",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        for trigger_revision in self.active_trigger_revisions():
            if (
                not trigger_revision.enabled
                or trigger_revision.trigger_type != "schedule"
            ):
                continue
            cron = trigger_revision.config.get("cron")
            if not cron:
                continue
            self._scheduler.add_job(
                self.run_trigger_revision,
                CronTrigger.from_crontab(cron),
                args=[trigger_revision.id],
                id=trigger_revision.id,
                replace_existing=True,
                max_instances=1,
                coalesce=trigger_revision.misfire_policy == "run_once",
            )

    def active_trigger_revisions(self) -> list[TriggerRevision]:
        revisions = []
        for trigger in self.graph.triggers.values():
            if trigger.active_revision_id is None:
                continue
            revision = self.graph.trigger_revisions.get(trigger.active_revision_id)
            if revision is not None and self._application_allows_triggers(
                revision
            ):
                revisions.append(revision)
        return sorted(revisions, key=lambda revision: revision.id)

    def run_trigger(
        self,
        trigger_id: str,
        input_value: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> JobRecord:
        trigger = self.graph.triggers[trigger_id]
        if trigger.active_revision_id is None:
            raise KeyError(f"trigger is not active: {trigger_id}")
        return self.run_trigger_revision(
            trigger.active_revision_id, input_value, idempotency_key=idempotency_key
        )

    def run_trigger_revision(
        self,
        trigger_revision_id: str,
        input_value: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> JobRecord:
        with self.uow:
            trigger_revision = self.graph.trigger_revisions[trigger_revision_id]
            if not self._application_allows_triggers(trigger_revision):
                raise ValidationFailure(
                    "application triggers are paused or unavailable"
                )
            application = self._application_for_trigger(trigger_revision)
            if application is None:
                raise ValidationFailure("trigger application is unavailable")
            revision = self.graph.revisions[application.active_revision_id]
            now = now_utc()
            job_input = {**trigger_revision.input_template, **(input_value or {})}
            key = idempotency_key or sha256_json(
                {
                    "trigger_revision_id": trigger_revision.id,
                    "input": job_input,
                    "scheduled_at": now.isoformat(),
                }
            )
            existing = self._job_by_idempotency_key(key)
            if existing is not None:
                return existing
            if (
                trigger_revision.overlap_policy == "skip_if_running"
                and self._has_running_job(trigger_revision.id)
            ):
                return self._record_job(
                    revision.application_id,
                    trigger_revision,
                    job_input,
                    key,
                    status="missed",
                    scheduled_at=now,
                    queued_at=now,
                    completed_at=now,
                    error="skipped because trigger is already running",
                    application_revision_id=revision.id,
                )
            job = self._record_job(
                revision.application_id,
                trigger_revision,
                job_input,
                key,
                status="scheduled",
                scheduled_at=now,
                queued_at=now,
                application_revision_id=revision.id,
            )
        return self._execute_job(job, trigger_revision)

    def process_due_work(self) -> list[JobRecord]:
        self.recover_expired_leases()
        with self.uow:
            now = now_utc()
            due = list(self.uow.jobs.due_retries(now))
            due = [
                job
                for job in due
                if self._application_allows_triggers(
                    self.graph.trigger_revisions[job.trigger_revision_id]
                )
            ]
            trigger_revisions = {
                job.id: self.graph.trigger_revisions[job.trigger_revision_id]
                for job in due
            }
        return [
            self._execute_job(job, trigger_revisions[job.id], retry=True) for job in due
        ]

    @_transactional
    def recover_expired_leases(self) -> list[JobRecord]:
        recovered = []
        now = now_utc()
        for job in list(self.uow.jobs.expired_leases(now)):
            trigger_revision = self.graph.trigger_revisions[job.trigger_revision_id]
            recovered.append(
                self._fail_or_retry(
                    job,
                    trigger_revision,
                    error="lease expired before heartbeat",
                    attempt_status="expired",
                )
            )
        return recovered

    def dispatch_event(
        self,
        application_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> list[JobRecord]:
        jobs = []
        for trigger_revision in self.active_trigger_revisions():
            application = self._application_for_trigger(trigger_revision)
            if application is None or application.id != application_id:
                continue
            if trigger_revision.trigger_type != "event":
                continue
            if trigger_revision.config.get("event_type") != event_type:
                continue
            jobs.append(self.run_trigger_revision(trigger_revision.id, payload))
        return jobs

    @_transactional
    def mark_missed_jobs(self) -> list[JobRecord]:
        missed = []
        now = now_utc()
        for trigger_revision in self.active_trigger_revisions():
            if (
                trigger_revision.trigger_type != "schedule"
                or trigger_revision.misfire_policy == "skip"
            ):
                continue
            application = self._application_for_trigger(trigger_revision)
            if application is None:
                continue
            if application.active_revision_id is None:
                continue
            job = self._record_job(
                application.id,
                trigger_revision,
                trigger_revision.input_template,
                sha256_json(
                    {
                        "trigger_revision_id": trigger_revision.id,
                        "missed_at": now.isoformat(),
                    }
                ),
                status="missed",
                scheduled_at=now,
                completed_at=now,
                error="scheduled trigger may have fired while runtime was stopped",
                application_revision_id=application.active_revision_id,
            )
            missed.append(job)
        return missed

    def inspect(self) -> dict[str, Any]:
        scheduled_jobs = []
        if self._scheduler is not None:
            scheduled_jobs = [
                {
                    "id": job.id,
                    "next_run_time": None
                    if job.next_run_time is None
                    else job.next_run_time.isoformat(),
                }
                for job in self._scheduler.get_jobs()
            ]
        return {
            "running": self._scheduler is not None and self._scheduler.running,
            "lease_owner": self.lease_owner,
            "active_triggers": [
                revision.model_dump(mode="json")
                for revision in self.active_trigger_revisions()
            ],
            "scheduled_jobs": scheduled_jobs,
            "job_records": [
                job.model_dump(mode="json") for job in self.graph.job_records.values()
            ],
            "job_attempts": [
                attempt.model_dump(mode="json")
                for attempt in self.graph.job_attempts.values()
            ],
        }

    def _execute_job(
        self,
        job: JobRecord,
        trigger_revision: TriggerRevision,
        *,
        retry: bool = False,
    ) -> JobRecord:
        with self.uow:
            current = self.graph.job_records[job.id]
            if not self._application_allows_triggers(trigger_revision):
                return current
            now = now_utc()
            attempt_number = current.attempt + 1 if retry else current.attempt
            expires = now + timedelta(seconds=LEASE_SECONDS)
            attempt = JobAttempt(
                id=str(uuid.uuid4()),
                job_id=current.id,
                attempt=attempt_number,
                status="leased",
                lease_owner=self.lease_owner,
                lease_expires_at=expires,
                heartbeat_at=now,
            )
            leased = current.model_copy(
                update={
                    "status": "leased",
                    "attempt": attempt_number,
                    "lease_owner": self.lease_owner,
                    "lease_expires_at": expires,
                    "heartbeat_at": now,
                    "current_attempt_id": attempt.id,
                    "started_at": now,
                    "error": None,
                    "next_retry_at": None,
                }
            )
            self.graph.job_attempts.save(attempt)
            self.graph.job_records[leased.id] = leased
            self.store.create_edge(
                node_ref("JobRecord", id=leased.id),
                "HAS_ATTEMPT",
                node_ref("JobAttempt", id=attempt.id),
            )
            self.store.create_edge(
                node_ref("JobRecord", id=leased.id),
                "CURRENT_ATTEMPT",
                node_ref("JobAttempt", id=attempt.id),
            )
            running_attempt = attempt.model_copy(
                update={
                    "status": "running",
                    "started_at": now_utc(),
                    "heartbeat_at": now_utc(),
                }
            )
            running = leased.model_copy(
                update={
                    "status": "running",
                    "heartbeat_at": running_attempt.heartbeat_at,
                    "lease_expires_at": now_utc() + timedelta(seconds=LEASE_SECONDS),
                }
            )
            self.graph.job_attempts.save(running_attempt)
            self.graph.job_records[running.id] = running
            action_revision = self.graph.action_revisions[current.action_revision_id]
            application = self.graph.applications[current.application_id]
            pinned_revision_id = current.application_revision_id
            if self.graph.revisions.get(pinned_revision_id) is None:
                return self._fail_or_retry(
                    running,
                    trigger_revision,
                    error=(
                        "job application revision is gone: "
                        f"{pinned_revision_id}"
                    ),
                    attempt_status="failed",
                    attempt=running_attempt,
                )
            execution_context = ExecutionContext(
                correlation_id=str(uuid.uuid4()),
                application_revision_id=pinned_revision_id,
                application_id=application.id,
            )

        with self.uow:
            if not self._application_allows_triggers(trigger_revision):
                return self._defer_paused_job(running, running_attempt)
        self.uow.require_inactive("execute scheduled action")
        job_id = running.id
        attempt_id = running_attempt.id
        invocation = self.actions.run(
            action_revision,
            running.input,
            invocation_kind="active",
            execution_context=execution_context,
            heartbeat_callback=lambda: self._heartbeat_job(job_id, attempt_id),
        )
        with self.uow:
            self.store.create_edge(
                node_ref("JobRecord", id=running.id),
                "INVOKED",
                node_ref("Invocation", id=invocation.id),
            )
            self.store.create_edge(
                node_ref("JobAttempt", id=running_attempt.id),
                "INVOKED",
                node_ref("Invocation", id=invocation.id),
            )
            if invocation.status == "succeeded":
                completed_attempt = running_attempt.model_copy(
                    update={
                        "status": "succeeded",
                        "invocation_id": invocation.id,
                        "completed_at": invocation.completed_at,
                        "heartbeat_at": invocation.last_heartbeat_at,
                    }
                )
                completed = running.model_copy(
                    update={
                        "status": "succeeded",
                        "invocation_id": invocation.id,
                        "completed_at": invocation.completed_at,
                        "heartbeat_at": invocation.last_heartbeat_at,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "error": None,
                    }
                )
                self.graph.job_attempts.save(completed_attempt)
                self.graph.job_records[completed.id] = completed
                return completed
            return self._fail_or_retry(
                running.model_copy(update={"invocation_id": invocation.id}),
                trigger_revision,
                error=invocation.error or "action failed",
                attempt_status="failed",
                attempt=running_attempt.model_copy(
                    update={
                        "invocation_id": invocation.id,
                        "heartbeat_at": invocation.last_heartbeat_at,
                    }
                ),
            )

    def _heartbeat_job(self, job_id: str, attempt_id: str) -> None:
        with self.uow:
            job = self.graph.job_records.get(job_id)
            attempt = self.graph.job_attempts.get(attempt_id)
            if (
                job is None
                or attempt is None
                or job.status != "running"
                or attempt.status != "running"
                or job.lease_owner != self.lease_owner
                or attempt.lease_owner != self.lease_owner
            ):
                return
            now = now_utc()
            expires = now + timedelta(seconds=LEASE_SECONDS)
            self.graph.job_records[job.id] = job.model_copy(
                update={
                    "heartbeat_at": now,
                    "lease_expires_at": expires,
                }
            )
            self.graph.job_attempts.save(
                attempt.model_copy(
                    update={
                        "heartbeat_at": now,
                        "lease_expires_at": expires,
                    }
                )
            )

    def _fail_or_retry(
        self,
        job: JobRecord,
        trigger_revision: TriggerRevision,
        *,
        error: str,
        attempt_status: str,
        attempt: JobAttempt | None = None,
    ) -> JobRecord:
        current_attempt = attempt
        if current_attempt is None and job.current_attempt_id is not None:
            current_attempt = self.graph.job_attempts.get(job.current_attempt_id)
        if current_attempt is not None:
            self.graph.job_attempts.save(
                current_attempt.model_copy(
                    update={
                        "status": attempt_status,  # type: ignore[arg-type]
                        "completed_at": now_utc(),
                        "error": error,
                    }
                )
            )
        if job.attempt < job.max_attempts:
            completed = job.model_copy(
                update={
                    "status": "retry_wait",
                    "error": error,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "next_retry_at": now_utc()
                    + timedelta(
                        seconds=self._retry_delay_seconds(trigger_revision, job.attempt)
                    ),
                }
            )
        else:
            completed = job.model_copy(
                update={
                    "status": "failed",
                    "error": error,
                    "completed_at": now_utc(),
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "next_retry_at": None,
                }
            )
        self.graph.job_records[completed.id] = completed
        return completed

    def _record_job(
        self,
        application_id: str,
        trigger_revision: TriggerRevision,
        input_value: dict[str, Any],
        idempotency_key: str,
        *,
        status: str,
        scheduled_at=None,
        queued_at=None,
        started_at=None,
        completed_at=None,
        heartbeat_at=None,
        error: str | None = None,
        application_revision_id: str,
    ) -> JobRecord:
        job = JobRecord(
            id=str(uuid.uuid4()),
            application_id=application_id,
            application_revision_id=application_revision_id,
            trigger_revision_id=trigger_revision.id,
            action_revision_id=self._action_revision_id(
                application_revision_id, trigger_revision
            ),
            status=status,  # type: ignore[arg-type]
            input=input_value,
            scheduled_at=scheduled_at,
            queued_at=queued_at,
            started_at=started_at,
            completed_at=completed_at,
            heartbeat_at=heartbeat_at,
            attempt=1,
            max_attempts=trigger_revision.max_attempts,
            idempotency_key=idempotency_key,
            error=error,
        )
        self.graph.job_records[job.id] = job
        self.store.create_edge(
            node_ref("Application", id=application_id),
            "HAS_JOB",
            node_ref("JobRecord", id=job.id),
        )
        self.store.create_edge(
            node_ref("TriggerRevision", id=trigger_revision.id),
            "HAS_JOB",
            node_ref("JobRecord", id=job.id),
        )
        return job

    def _job_by_idempotency_key(self, idempotency_key: str) -> JobRecord | None:
        for job in self.graph.job_records.values():
            if job.idempotency_key == idempotency_key:
                return job
        return None

    def _has_running_job(self, trigger_revision_id: str) -> bool:
        return any(
            job.trigger_revision_id == trigger_revision_id
            and job.status in {"leased", "running"}
            for job in self.graph.job_records.values()
        )

    def _application_for_trigger(self, trigger_revision: TriggerRevision):
        trigger = self.graph.triggers.get(trigger_revision.trigger_id)
        if trigger is None:
            return None
        return self.graph.applications.get(trigger.application_id)

    def _action_revision_id(
        self, application_revision_id: str, trigger_revision: TriggerRevision
    ) -> str:
        return self.bindings.action_revision(
            application_revision_id, trigger_revision.action_id
        ).id

    def _application_allows_triggers(
        self, trigger_revision: TriggerRevision
    ) -> bool:
        application = self._application_for_trigger(trigger_revision)
        return (
            application is not None
            and application.status == "active"
            and application.active_revision_id is not None
        )

    def _defer_paused_job(
        self, job: JobRecord, attempt: JobAttempt
    ) -> JobRecord:
        now = now_utc()
        self.graph.job_attempts.save(
            attempt.model_copy(
                update={
                    "status": "expired",
                    "completed_at": now,
                    "error": "application triggers paused before execution",
                }
            )
        )
        deferred = job.model_copy(
            update={
                "status": "retry_wait",
                "lease_owner": None,
                "lease_expires_at": None,
                "next_retry_at": now,
                "error": "application triggers paused before execution",
            }
        )
        self.graph.job_records[deferred.id] = deferred
        return deferred

    def _retry_delay_seconds(
        self, trigger_revision: TriggerRevision, attempt: int
    ) -> int:
        base = int(trigger_revision.retry_policy.get("base_seconds", 30))
        maximum = int(trigger_revision.retry_policy.get("max_seconds", 300))
        return min(maximum, base * (2 ** max(0, attempt - 1)))
