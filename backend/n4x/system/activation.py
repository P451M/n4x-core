"""Application activation owned by the System, not the host."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from n4x.graph.bindings import RevisionBindings
from n4x.graph.service_base import transactional
from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ImmutableRevisionError, ValidationFailure
from n4x.kernel.models import (
    Action,
    ActionRevision,
    ApplicationRevision,
    ValidationReport,
)
from n4x.system.checkpoints import CheckpointService


class ActivationSourcePort(Protocol):
    def intern_tree(self, revision_id: str): ...

    def read_source_file(self, source_tree_id: str, path: str): ...


class ActivationInvocationPort(Protocol):
    def resolve_dependencies(self, application_revision_id: str) -> dict[str, Any]: ...

    def build_revision_artifacts(
        self, application_revision_id: str
    ) -> dict[str, Any]: ...

    def run_migration(
        self, application_revision_id: str, action_revision_id: str
    ) -> Any: ...


class ActivationDefinitionPort(Protocol):
    def relink_action_revision(
        self,
        application_id: str,
        action: Action,
        revision: ActionRevision,
    ) -> None: ...


class ActivationSchedulerPort(Protocol):
    def remount(self) -> None: ...


class ActivationCypherGatewayPort(Protocol):
    def available(self) -> bool: ...


class ActivationProcessPort(Protocol):
    def evict_revision(self, revision_id: str) -> None: ...


class ActivationService:
    """Single-pass application activation. No durable attempt rows."""

    def __init__(
        self,
        uow: GraphUnitOfWork,
        source: ActivationSourcePort,
        invocations: ActivationInvocationPort,
        definitions: ActivationDefinitionPort,
        scheduler: ActivationSchedulerPort,
        checkpoints: CheckpointService | None = None,
        cypher_gateway: ActivationCypherGatewayPort | None = None,
        process_pool: ActivationProcessPort | None = None,
    ) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.source = source
        self.invocations = invocations
        self.definitions = definitions
        self.scheduler = scheduler
        self.checkpoints = checkpoints or CheckpointService(uow)
        self.cypher_gateway = cypher_gateway
        self.process_pool = process_pool
        self.bindings = RevisionBindings(uow)

    @transactional
    def validate(self, application_revision_id: str) -> ValidationReport:
        errors: list[str] = []
        app_revision = self.records.revisions[application_revision_id]
        bound = {
            "ActionRevision": (
                self.bindings.action_revisions(app_revision.id),
                "action_id",
            ),
            "TriggerRevision": (
                self.bindings.trigger_revisions(app_revision.id),
                "trigger_id",
            ),
            "ObjectTypeRevision": (
                self.bindings.object_type_revisions(app_revision.id),
                "object_type_id",
            ),
            "RelationTypeRevision": (
                self.bindings.relation_type_revisions(app_revision.id),
                "relation_type_id",
            ),
        }
        for label, (revisions, owner_field) in bound.items():
            counts: dict[str, int] = {}
            for revision in revisions:
                stable_id = getattr(revision, owner_field)
                counts[stable_id] = counts.get(stable_id, 0) + 1
            for stable_id, count in counts.items():
                if count > 1:
                    errors.append(
                        f"{label}: duplicate {owner_field} {stable_id} "
                        f"on {app_revision.id}"
                    )
        tree_id = self.bindings.tree_id(app_revision.id)
        for revision in self._action_revisions(app_revision.id):
            for path in revision.source_paths:
                try:
                    self.source.read_source_file(tree_id, path)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{revision.id}:{path}: {type(exc).__name__}: {exc}")
            if revision.kind == "migration":
                metadata = revision.migration_metadata
                affected = metadata.get("affected_schema_revision_ids", [])
                if not isinstance(affected, list):
                    errors.append(
                        f"{revision.id}: affected_schema_revision_ids must be a list"
                    )
        object_type_ids = {
            item.object_type_id
            for item in self.bindings.object_type_revisions(app_revision.id)
        }
        for revision in self.bindings.relation_type_revisions(app_revision.id):
            if revision.from_object_type_id not in object_type_ids:
                errors.append(f"{revision.id}: missing from object type revision")
            if revision.to_object_type_id not in object_type_ids:
                errors.append(f"{revision.id}: missing to object type revision")
        if self._action_revisions(app_revision.id):
            if self.cypher_gateway is None or not self.cypher_gateway.available():
                errors.append("Cypher gateway is unavailable")
        report = ValidationReport(
            id=str(uuid.uuid4()),
            application_revision_id=app_revision.id,
            status="failed" if errors else "passed",
            errors=errors,
        )
        self.records.validation_reports.save(report)
        self.store.create_edge(
            node_ref("ApplicationRevision", id=app_revision.id),
            "HAS_VALIDATION_REPORT",
            node_ref("ValidationReport", id=report.id),
        )
        return report

    def activate(
        self,
        application_revision_id: str,
        *,
        policy: str = "normal",
    ) -> ApplicationRevision:
        if policy not in {"normal", "package_install"}:
            raise ValueError(f"unsupported activation policy: {policy}")
        checkpoint_id: str | None = None
        switched = False
        try:
            with self.uow:
                self._freeze(application_revision_id)
            self.uow.require_inactive("resolve application dependencies")
            self.invocations.resolve_dependencies(application_revision_id)
            self.uow.require_inactive("build application artifacts")
            self.invocations.build_revision_artifacts(application_revision_id)
            if policy != "package_install":
                checkpoint_id = self._checkpoint_if_mutating(
                    application_revision_id
                )
                self._run_migrations(application_revision_id)
            with self.uow:
                activated, superseded_id = self._activate_edges(
                    application_revision_id
                )
            if superseded_id is not None and self.process_pool is not None:
                self.process_pool.evict_revision(superseded_id)
            switched = True
            if policy != "package_install":
                self.scheduler.remount()
            return activated
        except Exception as exc:
            if switched:
                raise
            restore_error = self._restore_checkpoint(checkpoint_id)
            self._reject(application_revision_id)
            error = f"{type(exc).__name__}: {exc}"
            if restore_error is not None:
                error += f"; checkpoint restore failed: {restore_error}"
            if isinstance(exc, ValidationFailure):
                raise
            raise ValidationFailure(error) from exc

    def _freeze(self, application_revision_id: str) -> None:
        revision = self.records.revisions[application_revision_id]
        if revision.status not in {"draft", "validating"}:
            raise ImmutableRevisionError(
                f"activation requires draft state, got {revision.status}"
            )
        if revision.status == "draft":
            revision = revision.model_copy(update={"status": "validating"})
            self.records.revisions.save(revision)
        self.source.intern_tree(revision.id)

    def _checkpoint_if_mutating(self, application_revision_id: str) -> str | None:
        mutations = any(
            bool(item.migration_metadata.get("mutates_application_data"))
            for item in self._migrations(application_revision_id)
        )
        if not mutations:
            return None
        revision = self.records.revisions[application_revision_id]
        return self.checkpoints.create(
            revision.application_id,
            level="application_data",
            reason=f"activation before {application_revision_id}",
        ).id

    def _run_migrations(self, application_revision_id: str) -> None:
        for revision in self._migrations(application_revision_id):
            self.invocations.run_migration(application_revision_id, revision.id)

    def _restore_checkpoint(self, checkpoint_id: str | None) -> str | None:
        if checkpoint_id is None:
            return None
        try:
            self.checkpoints.restore(checkpoint_id)
            try:
                self.scheduler.remount()
            except Exception:
                pass
            return None
        except Exception as exc:  # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"

    def _reject(self, application_revision_id: str) -> None:
        with self.uow:
            revision = self.records.revisions.get(application_revision_id)
            if revision is None:
                return
            if revision.status == "validating":
                self.records.revisions.save(
                    revision.model_copy(update={"status": "rejected"})
                )

    def rollback(
        self, application_id: str, target_revision_id: str
    ) -> ApplicationRevision:
        with self.uow:
            target = self.records.revisions.get(target_revision_id)
            if target is None:
                raise KeyError(target_revision_id)
            if target.application_id != application_id:
                raise ValueError("target revision belongs to a different application")
            if target.status not in {"active", "superseded"}:
                raise ValidationFailure(
                    "rollback target must be a previously active revision"
                )
            activated, superseded_id = self._activate_edges(target_revision_id)
        if superseded_id is not None and self.process_pool is not None:
            self.process_pool.evict_revision(superseded_id)
        return activated

    def _activate_edges(
        self, application_revision_id: str
    ) -> tuple[ApplicationRevision, str | None]:
        target = self.records.revisions[application_revision_id]
        application = self.records.applications[target.application_id]
        superseded_id: str | None = None
        if application.active_revision_id is not None:
            current = self.records.revisions[application.active_revision_id]
            if current.id != target.id:
                superseded_id = current.id
                self.records.revisions.save(
                    current.model_copy(update={"status": "superseded"})
                )
        activated = target.model_copy(update={"status": "active"})
        self.records.revisions.save(activated)
        status = application.status
        if status == "disabled":
            status = "triggers_paused"
        self.records.applications.save(
            application.model_copy(
                update={
                    "active_revision_id": activated.id,
                    "status": status,
                }
            )
        )
        self.uow.applications.replace_active_revision(
            application.id,
            activated.id,
            expected_revision_id=application.active_revision_id,
        )
        self._activate_revision_set(
            self.bindings.action_revisions(activated.id),
            "action_id",
            "Action",
            self.records.actions,
            "ActionRevision",
        )
        self._activate_revision_set(
            self.bindings.trigger_revisions(activated.id),
            "trigger_id",
            "Trigger",
            self.records.triggers,
            "TriggerRevision",
        )
        self._activate_revision_set(
            self.bindings.object_type_revisions(activated.id),
            "object_type_id",
            "ObjectType",
            self.records.object_types,
            "ObjectTypeRevision",
        )
        self._activate_revision_set(
            self.bindings.relation_type_revisions(activated.id),
            "relation_type_id",
            "RelationType",
            self.records.relation_types,
            "RelationTypeRevision",
        )
        return activated, superseded_id

    def _activate_revision_set(
        self,
        revisions: list,
        owner_field: str,
        stable_label: str,
        stable_records,
        revision_label: str,
    ) -> None:
        for revision in revisions:
            stable = stable_records[getattr(revision, owner_field)]
            stable_records.save(
                stable.model_copy(update={"active_revision_id": revision.id})
            )
            self.store.replace_single_edge(
                node_ref(stable_label, id=stable.id),
                "ACTIVE_REVISION",
                node_ref(revision_label, id=revision.id),
            )

    def _action_revisions(self, application_revision_id: str) -> list[ActionRevision]:
        return sorted(
            self.bindings.action_revisions(application_revision_id),
            key=lambda item: item.id,
        )

    def _migrations(self, application_revision_id: str) -> list[ActionRevision]:
        current = {
            item.action_id: item
            for item in self._action_revisions(application_revision_id)
            if item.kind == "migration"
        }
        target = self.records.revisions[application_revision_id]
        parent_ids: dict[str, str] = {}
        if target.parent_revision_id is not None:
            parent_ids = {
                item.action_id: item.id
                for item in self._action_revisions(target.parent_revision_id)
                if item.kind == "migration"
            }
        return [
            item
            for action_id, item in current.items()
            if parent_ids.get(action_id) != item.id
        ]
