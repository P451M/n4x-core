"""Action and trigger definitions owned by the System."""

from __future__ import annotations

import uuid
from typing import Any

from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.hash import sha256_json
from n4x.kernel.models import (
    Action,
    ActionKind,
    ActionRevision,
    TestCase,
    Trigger,
    TriggerRevision,
)
from n4x.graph.service_base import transactional
from n4x.source_store.service import SourceStore
from n4x.system.drafts import Drafts


class Definitions:
    def __init__(self, uow: GraphUnitOfWork, source: SourceStore) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.source = source
        self.drafts = Drafts(self.records)

    @transactional
    def create_action(
        self,
        application_revision_id: str,
        action_id: str,
        *,
        kind: ActionKind | str,
        entrypoint: str,
        source_paths: list[str],
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        dependency_ids: list[str] | None = None,
        secret_ref_ids: list[str] | None = None,
        migration_metadata: dict[str, Any] | None = None,
        timeout_seconds: int = 30,
        declared_capabilities: list[str] | None = None,
        concurrency_policy: str = "default",
        retry_policy: dict[str, Any] | None = None,
        idempotency_key_policy: str | None = None,
        created_by: str = "system",
    ) -> ActionRevision:
        app_revision = self.drafts.require_application(application_revision_id)
        if kind not in ("normal", "migration", "test_helper"):
            raise ValidationFailure(f"invalid action kind: {kind}", field="kind")
        for secret_id in secret_ref_ids or []:
            if self.records.secret_references.get(secret_id) is None:
                raise ValidationFailure(f"unknown secret reference: {secret_id}")
        for dependency_id in dependency_ids or []:
            dependency = self.records.runtime_dependencies[dependency_id]
            if (
                dependency.owner_kind != "ApplicationRevision"
                or dependency.owner_id != app_revision.id
                or dependency.ecosystem != "python"
            ):
                raise ValueError(
                    "Action dependencies must be Python dependencies owned "
                    "by the same ApplicationRevision"
                )
        stable = self.records.actions.get(action_id) or Action(
            id=action_id, application_id=app_revision.application_id
        )
        self.records.actions.save(stable)
        existing = self._draft(
            self.records.action_revisions.values(),
            application_revision_id,
            "action_id",
            action_id,
        )
        source_hashes = [
            self.source.read_source_file(
                app_revision.source_tree_id, path
            ).content_hash
            for path in source_paths
        ]
        revision = ActionRevision(
            id=(
                existing.id
                if existing is not None
                else f"{action_id}@{self._next(self.records.action_revisions.values(), 'action_id', action_id)}"
            ),
            action_id=action_id,
            application_revision_id=application_revision_id,
            kind=kind,  # type: ignore[arg-type]
            entrypoint=entrypoint,
            source_tree_id=app_revision.source_tree_id,
            source_paths=source_paths,
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            runtime_dependency_ids=dependency_ids or [],
            secret_refs=secret_ref_ids or [],
            migration_metadata=migration_metadata or {},
            timeout_seconds=timeout_seconds,
            declared_capabilities=declared_capabilities or [],
            concurrency_policy=concurrency_policy,
            retry_policy=retry_policy or {},
            idempotency_key_policy=idempotency_key_policy,
            created_by=created_by,
            content_hash=sha256_json(
                {
                    "kind": kind,
                    "entrypoint": entrypoint,
                    "source_hashes": source_hashes,
                    "input_schema": input_schema or {},
                    "output_schema": output_schema or {},
                    "dependency_ids": dependency_ids or [],
                    "secret_ref_ids": secret_ref_ids or [],
                    "migration_metadata": migration_metadata or {},
                    "timeout_seconds": timeout_seconds,
                    "declared_capabilities": declared_capabilities or [],
                    "concurrency_policy": concurrency_policy,
                    "retry_policy": retry_policy or {},
                    "idempotency_key_policy": idempotency_key_policy,
                }
            ),
        )
        self.records.action_revisions.save(revision)
        self.relink_action_revision(app_revision.application_id, stable, revision)
        return revision

    @transactional
    def create_trigger(
        self,
        application_revision_id: str,
        trigger_id: str,
        *,
        trigger_type: str,
        action_revision_id: str,
        config: dict[str, Any] | None = None,
        input_template: dict[str, Any] | None = None,
        overlap_policy: str | None = None,
        misfire_policy: str = "run_once",
        max_attempts: int = 3,
        retry_policy: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> TriggerRevision:
        app_revision = self.drafts.require_application(application_revision_id)
        action_revision = self.records.action_revisions[action_revision_id]
        if action_revision.kind == "migration":
            raise ValueError("migration ActionRevisions cannot be trigger targets")
        if action_revision.application_revision_id != application_revision_id:
            raise ValueError(
                "trigger action revision must belong to the same application revision"
            )
        stable = self.records.triggers.get(trigger_id) or Trigger(
            id=trigger_id, application_id=app_revision.application_id
        )
        self.records.triggers.save(stable)
        existing = self._draft(
            self.records.trigger_revisions.values(),
            application_revision_id,
            "trigger_id",
            trigger_id,
        )
        overlap_policy = overlap_policy or (
            "skip_if_running"
            if trigger_type == "schedule"
            else "queue"
            if trigger_type == "event"
            else "run_concurrently"
        )
        values = {
            "trigger_type": trigger_type,
            "action_revision_id": action_revision_id,
            "config": config or {},
            "input_template": input_template or {},
            "overlap_policy": overlap_policy,
            "misfire_policy": misfire_policy,
            "max_attempts": max_attempts,
            "retry_policy": retry_policy or {},
            "enabled": enabled,
        }
        revision = TriggerRevision(
            id=(
                existing.id
                if existing is not None
                else f"{trigger_id}@{self._next(self.records.trigger_revisions.values(), 'trigger_id', trigger_id)}"
            ),
            trigger_id=trigger_id,
            application_revision_id=application_revision_id,
            content_hash=sha256_json(values),
            **values,  # type: ignore[arg-type]
        )
        self.records.trigger_revisions.save(revision)
        self._link_definition(
            app_revision.application_id,
            "DEFINES_TRIGGER",
            "Trigger",
            stable.id,
            "TriggerRevision",
            revision.id,
        )
        self.store.replace_single_edge(
            node_ref("TriggerRevision", id=revision.id),
            "INVOKES",
            node_ref("ActionRevision", id=revision.action_revision_id),
        )
        return revision

    @transactional
    def create_test_case(
        self,
        application_revision_id: str,
        action_revision_id: str,
        input_value: dict[str, Any],
        expected_output: Any,
    ) -> TestCase:
        if self.records.action_revisions[action_revision_id].kind == "migration":
            raise ValidationFailure(
                "migration revisions use activation dry-runs, not TestCase"
            )
        test = TestCase(
            id=str(uuid.uuid4()),
            application_revision_id=application_revision_id,
            action_revision_id=action_revision_id,
            input=input_value,
            expected_output=expected_output,
        )
        self.records.test_cases.save(test)
        self.store.create_edge(
            node_ref("ApplicationRevision", id=application_revision_id),
            "HAS_TEST",
            node_ref("TestCase", id=test.id),
        )
        self.store.create_edge(
            node_ref("TestCase", id=test.id),
            "TESTS",
            node_ref("ActionRevision", id=action_revision_id),
        )
        return test

    def validate_trigger(self, revision: TriggerRevision) -> None:
        action_revision = self.records.action_revisions[revision.action_revision_id]
        if action_revision.application_revision_id != revision.application_revision_id:
            raise ValueError(
                "trigger action revision must belong to the same application revision"
            )
        if revision.trigger_type == "schedule":
            cron = revision.config.get("cron")
            if not isinstance(cron, str) or not cron.strip():
                raise ValueError("schedule triggers require config.cron")
            from apscheduler.triggers.cron import CronTrigger

            CronTrigger.from_crontab(cron)
        elif revision.trigger_type == "event":
            event_type = revision.config.get("event_type")
            if not isinstance(event_type, str) or not event_type.strip():
                raise ValueError("event triggers require config.event_type")

    def relink_action_revision(
        self,
        application_id: str,
        action: Action,
        revision: ActionRevision,
    ) -> None:
        self._link_definition(
            application_id,
            "DEFINES_ACTION",
            "Action",
            action.id,
            "ActionRevision",
            revision.id,
        )
        revision_ref = node_ref("ActionRevision", id=revision.id)
        self.store.delete_edge(revision_ref, "USES_SOURCE")
        self.store.delete_edge(revision_ref, "DEPENDS_ON")
        self.store.delete_edge(revision_ref, "USES_SECRET")
        for path in revision.source_paths:
            self.store.create_edge(
                revision_ref,
                "USES_SOURCE",
                node_ref(
                    "SourceFile",
                    source_tree_id=revision.source_tree_id,
                    path=path,
                ),
            )
        for dependency_id in revision.runtime_dependency_ids:
            self.store.create_edge(
                revision_ref,
                "DEPENDS_ON",
                node_ref("RuntimeDependency", id=dependency_id),
            )
        for secret_id in revision.secret_refs:
            self.store.create_edge(
                revision_ref,
                "USES_SECRET",
                node_ref("SecretReference", id=secret_id),
            )

    def _link_definition(
        self,
        owner_id: str,
        ownership_edge: str,
        stable_label: str,
        stable_id: str,
        revision_label: str,
        revision_id: str,
    ) -> None:
        self.store.create_edge(
            node_ref("Application", id=owner_id),
            ownership_edge,
            node_ref(stable_label, id=stable_id),
        )
        self.store.create_edge(
            node_ref(stable_label, id=stable_id),
            "HAS_REVISION",
            node_ref(revision_label, id=revision_id),
        )

    @staticmethod
    def _draft(records, app_revision_id, field, stable_id):
        return next(
            (
                item
                for item in records
                if item.application_revision_id == app_revision_id
                and getattr(item, field) == stable_id
            ),
            None,
        )

    @staticmethod
    def _next(records, field, stable_id):
        return len([item for item in records if getattr(item, field) == stable_id]) + 1
