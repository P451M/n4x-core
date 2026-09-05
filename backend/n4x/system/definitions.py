"""Action and trigger definitions owned by the System."""

from __future__ import annotations

from typing import Any

from n4x.graph.bindings import RevisionBindings
from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.intern import (
    action_declaration,
    action_revision_id,
    test_case_id,
    trigger_revision_id,
)
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
        self.bindings = RevisionBindings(uow)

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
        callback_refs: list[str] | None = None,
    ) -> ActionRevision:
        app_revision = self.drafts.require_application(application_revision_id)
        if kind not in ("normal", "migration", "test_helper"):
            raise ValidationFailure(f"invalid action kind: {kind}", field="kind")
        for secret_id in secret_ref_ids or []:
            if self.records.secret_references.get(secret_id) is None:
                raise ValidationFailure(f"unknown secret reference: {secret_id}")
        declared_deps = {item.id for item in self.bindings.dependencies(app_revision.id)}
        for dependency_id in dependency_ids or []:
            dependency = self.records.runtime_dependencies[dependency_id]
            if dependency.id not in declared_deps or dependency.ecosystem != "python":
                raise ValueError(
                    "Action dependencies must be Python dependencies declared "
                    "by the same ApplicationRevision"
                )
        tree_id = self.bindings.tree_id(app_revision.id)
        for path in source_paths:
            self.source.read_source_file(tree_id, path)
        payload = action_declaration(
            action_id=action_id,
            kind=kind,
            entrypoint=entrypoint,
            source_paths=source_paths,
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            runtime_dependency_ids=dependency_ids or [],
            secret_refs=secret_ref_ids or [],
            callback_refs=callback_refs or [],
            declared_capabilities=declared_capabilities or [],
            timeout_seconds=timeout_seconds,
            concurrency_policy=concurrency_policy,
            retry_policy=retry_policy or {},
            idempotency_key_policy=idempotency_key_policy,
            migration_metadata=migration_metadata or {},
        )
        interned_id = action_revision_id(payload)
        revision = self.records.action_revisions.get(interned_id)
        if revision is None:
            revision = ActionRevision(
                id=interned_id,
                action_id=action_id,
                kind=kind,  # type: ignore[arg-type]
                entrypoint=entrypoint,
                source_paths=source_paths,
                input_schema=input_schema or {},
                output_schema=output_schema or {},
                runtime_dependency_ids=dependency_ids or [],
                secret_refs=secret_ref_ids or [],
                callback_refs=callback_refs or [],
                declared_capabilities=declared_capabilities or [],
                timeout_seconds=timeout_seconds,
                concurrency_policy=concurrency_policy,  # type: ignore[arg-type]
                retry_policy=retry_policy or {},
                idempotency_key_policy=idempotency_key_policy,
                migration_metadata=migration_metadata or {},
                created_by=created_by,
                content_hash=interned_id,
            )
            self.records.action_revisions.save(revision)
        stable = self.records.actions.get(action_id) or Action(
            id=action_id, application_id=app_revision.application_id
        )
        self.records.actions.save(stable)
        self._ensure_action_edges(app_revision.application_id, stable, revision)
        current = self.bindings.named(
            app_revision.id,
            "HAS_ACTION_REVISION",
            self.records.action_revisions,
            "action_id",
            action_id,
        )
        self.bindings.replace_named(
            app_revision.id,
            "HAS_ACTION_REVISION",
            None if current is None else current.id,
            revision.id,
            "ActionRevision",
        )
        return revision

    @transactional
    def create_trigger(
        self,
        application_revision_id: str,
        trigger_id: str,
        *,
        trigger_type: str,
        action_id: str,
        config: dict[str, Any] | None = None,
        input_template: dict[str, Any] | None = None,
        overlap_policy: str | None = None,
        misfire_policy: str = "run_once",
        max_attempts: int = 3,
        retry_policy: dict[str, Any] | None = None,
        enabled: bool = True,
        created_by: str = "system",
    ) -> TriggerRevision:
        app_revision = self.drafts.require_application(application_revision_id)
        action_revision = self.bindings.action_revision(app_revision.id, action_id)
        if action_revision.kind == "migration":
            raise ValueError("migration ActionRevisions cannot be trigger targets")
        overlap_policy = overlap_policy or (
            "skip_if_running"
            if trigger_type == "schedule"
            else "queue"
            if trigger_type == "event"
            else "run_concurrently"
        )
        values = {
            "trigger_id": trigger_id,
            "action_id": action_id,
            "trigger_type": trigger_type,
            "config": config or {},
            "input_template": input_template or {},
            "overlap_policy": overlap_policy,
            "misfire_policy": misfire_policy,
            "max_attempts": max_attempts,
            "retry_policy": retry_policy or {},
            "enabled": enabled,
        }
        interned_id = trigger_revision_id(values)
        revision = self.records.trigger_revisions.get(interned_id)
        if revision is None:
            revision = TriggerRevision(
                id=interned_id,
                created_by=created_by,
                content_hash=interned_id,
                **values,  # type: ignore[arg-type]
            )
            self.records.trigger_revisions.save(revision)
        self.validate_trigger(revision)
        stable = self.records.triggers.get(trigger_id) or Trigger(
            id=trigger_id, application_id=app_revision.application_id
        )
        self.records.triggers.save(stable)
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
            node_ref("Action", id=action_id),
        )
        current = self.bindings.named(
            app_revision.id,
            "HAS_TRIGGER_REVISION",
            self.records.trigger_revisions,
            "trigger_id",
            trigger_id,
        )
        self.bindings.replace_named(
            app_revision.id,
            "HAS_TRIGGER_REVISION",
            None if current is None else current.id,
            revision.id,
            "TriggerRevision",
        )
        return revision

    @transactional
    def create_test_case(
        self,
        application_revision_id: str,
        action_id: str,
        input_value: dict[str, Any],
        expected_output: Any,
    ) -> TestCase:
        app_revision = self.drafts.require_application(application_revision_id)
        action_revision = self.bindings.action_revision(app_revision.id, action_id)
        if action_revision.kind == "migration":
            raise ValidationFailure(
                "migration revisions use activation dry-runs, not TestCase"
            )
        payload = {
            "action_id": action_id,
            "input": input_value,
            "expected_output": expected_output,
        }
        interned_id = test_case_id(payload)
        test = self.records.test_cases.get(interned_id)
        if test is None:
            test = TestCase(
                id=interned_id,
                action_id=action_id,
                input=input_value,
                expected_output=expected_output,
            )
            self.records.test_cases.save(test)
        if test.id not in {item.id for item in self.bindings.test_cases(app_revision.id)}:
            self.store.create_edge(
                node_ref("ApplicationRevision", id=app_revision.id),
                "HAS_TEST",
                node_ref("TestCase", id=test.id),
            )
        self.store.replace_single_edge(
            node_ref("TestCase", id=test.id),
            "TESTS",
            node_ref("Action", id=action_id),
        )
        return test

    def validate_trigger(self, revision: TriggerRevision) -> None:
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
        self._ensure_action_edges(application_id, action, revision)

    def _ensure_action_edges(
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
        existing_deps = {
            edge.to_ref.identity["id"]
            for edge in self.store.list_edges(revision_ref, "DEPENDS_ON")
        }
        for dependency_id in revision.runtime_dependency_ids:
            if dependency_id not in existing_deps:
                self.store.create_edge(
                    revision_ref,
                    "DEPENDS_ON",
                    node_ref("RuntimeDependency", id=dependency_id),
                )
        existing_secrets = {
            edge.to_ref.identity["id"]
            for edge in self.store.list_edges(revision_ref, "USES_SECRET")
        }
        for secret_id in revision.secret_refs:
            if secret_id not in existing_secrets:
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
