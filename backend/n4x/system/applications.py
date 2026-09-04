"""Application identity owned by the System, not the host."""

from __future__ import annotations

import uuid

from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import (
    Action,
    ActionRevision,
    Application,
    ApplicationRevision,
    DataSpace,
    ObjectType,
    ObjectTypeRevision,
    RelationType,
    RelationTypeRevision,
    RuntimeDependency,
    Trigger,
    TriggerRevision,
    UiProfile,
    now_utc,
)
from n4x.graph.service_base import transactional
from n4x.source_store.service import SourceStore


class Applications:
    def __init__(self, uow: GraphUnitOfWork, source: SourceStore) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.source = source

    def list(self) -> list[Application]:
        return self.uow.applications.list()

    @transactional
    def create(
        self, application_id: str, name: str, description: str = ""
    ) -> Application:
        application = Application(
            id=application_id, name=name, description=description
        )
        self.uow.applications.save(application)
        self.uow.applications.attach_to_root(application.id)
        production = DataSpace(
            id="production",
            application_id=application.id,
            kind="production",
        )
        self.records.data_spaces.save(production)
        self.store.create_edge(
            node_ref("Application", id=application.id),
            "HAS_DATA_SPACE",
            node_ref(
                "DataSpace",
                application_id=application.id,
                id=production.id,
            ),
        )
        return application

    @transactional
    def set_status(
        self,
        application_id: str,
        status: str,
        *,
        expected_status: str | set[str] | None = None,
    ) -> Application:
        if status not in {
            "active",
            "disabled",
            "triggers_paused",
            "importing",
        }:
            raise ValueError(f"unsupported Application status: {status}")
        self.store.acquire_write_lock(node_ref("N4XRoot", id="n4x"))
        application = self.records.applications[application_id]
        expected = (
            expected_status
            if isinstance(expected_status, set)
            else {expected_status}
            if expected_status is not None
            else None
        )
        if expected is not None and application.status not in expected:
            raise ValueError(
                f"Application {application_id} status changed concurrently: "
                f"expected {sorted(expected)}, got {application.status}"
            )
        updated = application.model_copy(update={"status": status})
        self.uow.applications.save(updated)
        return updated

    @transactional
    def create_revision(
        self,
        application_id: str,
        created_by: str = "system",
        ui_profile: UiProfile | None = None,
        parent_revision_id: str | None = None,
    ) -> ApplicationRevision:
        application = self.records.applications[application_id]
        revision_id = (
            f"{application_id}@"
            f"{len(self.uow.applications.list_revisions(application_id)) + 1}"
        )
        parent = self._resolve_parent(application, parent_revision_id)
        tree = (
            self.source.clone_tree_to_draft(
                parent.source_tree_id,
                application_id,
                revision_id,
            )
            if parent is not None
            else self.source.create_tree(application_id, revision_id)
        )
        revision = ApplicationRevision(
            id=revision_id,
            application_id=application_id,
            source_tree_id=tree.id,
            parent_revision_id=parent.id if parent is not None else None,
            ui_profile=(
                ui_profile
                if ui_profile is not None
                else parent.ui_profile
                if parent is not None
                else "n4x-default"
            ),
            created_by=created_by,
        )
        self.uow.applications.save_revision(revision)
        if parent is not None:
            self._clone_active_definitions(parent, revision)
        self.uow.applications.attach_revision(application_id, revision.id)
        if revision.parent_revision_id is not None:
            self.store.create_edge(
                node_ref("ApplicationRevision", id=revision.id),
                "PARENT_REVISION",
                node_ref("ApplicationRevision", id=revision.parent_revision_id),
            )
        self.store.create_edge(
            node_ref("ApplicationRevision", id=revision.id),
            "HAS_SOURCE_TREE",
            node_ref("SourceTree", id=tree.id),
        )
        return revision

    @transactional
    def create_runtime_dependency(
        self,
        application_revision_id: str,
        ecosystem: str,
        package: str,
        spec: str,
    ) -> RuntimeDependency:
        if ecosystem != "python":
            raise ValueError(
                "ApplicationRevision dependencies must use the python ecosystem; "
                "declare JavaScript dependencies on an ExperienceRevision"
            )
        dependency = RuntimeDependency(
            id=str(uuid.uuid4()),
            owner_kind="ApplicationRevision",
            owner_id=application_revision_id,
            ecosystem=ecosystem,  # type: ignore[arg-type]
            package=package,
            spec=spec,
        )
        self.records.runtime_dependencies.save(dependency)
        self._link_runtime_dependency(dependency)
        return dependency

    def _clone_active_definitions(
        self,
        parent: ApplicationRevision,
        draft: ApplicationRevision,
    ) -> None:
        dependency_ids: dict[str, str] = {}
        for dependency in self.records.runtime_dependencies.values():
            if (
                dependency.owner_kind != "ApplicationRevision"
                or dependency.owner_id != parent.id
                or dependency.ecosystem != "python"
            ):
                continue
            clone = dependency.model_copy(
                update={
                    "id": str(uuid.uuid4()),
                    "owner_kind": "ApplicationRevision",
                    "owner_id": draft.id,
                    "created_at": now_utc(),
                }
            )
            self.records.runtime_dependencies.save(clone)
            dependency_ids[dependency.id] = clone.id
            self._link_runtime_dependency(clone)

        action_revision_ids: dict[str, str] = {}
        for action in self.records.actions.values():
            parent_revision = self._owned_revision_for_parent(
                action.id,
                self.records.action_revisions,
                parent.id,
                "action_id",
            )
            if parent_revision is None:
                continue
            clone = parent_revision.model_copy(
                update={
                    "id": self._next_revision_id(
                        action.id,
                        self.records.action_revisions.values(),
                        "action_id",
                    ),
                    "application_revision_id": draft.id,
                    "source_tree_id": draft.source_tree_id,
                    "runtime_dependency_ids": [
                        dependency_ids.get(item, item)
                        for item in parent_revision.runtime_dependency_ids
                    ],
                    "created_at": now_utc(),
                    "created_by": "revision_clone",
                }
            )
            self.records.action_revisions.save(clone)
            action_revision_ids[parent_revision.id] = clone.id
            self._link_action_revision(draft.application_id, action, clone)

        for object_type in self.records.object_types.values():
            parent_revision = self._owned_revision_for_parent(
                object_type.id,
                self.records.object_type_revisions,
                parent.id,
                "object_type_id",
            )
            if parent_revision is None:
                continue
            clone = parent_revision.model_copy(
                update={
                    "id": self._next_revision_id(
                        object_type.id,
                        self.records.object_type_revisions.values(),
                        "object_type_id",
                    ),
                    "application_revision_id": draft.id,
                    "created_at": now_utc(),
                }
            )
            self.records.object_type_revisions.save(clone)
            self._link_object_type_revision(
                draft.application_id, object_type, clone
            )

        for relation_type in self.records.relation_types.values():
            parent_revision = self._owned_revision_for_parent(
                relation_type.id,
                self.records.relation_type_revisions,
                parent.id,
                "relation_type_id",
            )
            if parent_revision is None:
                continue
            clone = parent_revision.model_copy(
                update={
                    "id": self._next_revision_id(
                        relation_type.id,
                        self.records.relation_type_revisions.values(),
                        "relation_type_id",
                    ),
                    "application_revision_id": draft.id,
                    "created_at": now_utc(),
                }
            )
            self.records.relation_type_revisions.save(clone)
            self._link_relation_type_revision(
                draft.application_id, relation_type, clone
            )

        for trigger in self.records.triggers.values():
            parent_revision = self._owned_revision_for_parent(
                trigger.id,
                self.records.trigger_revisions,
                parent.id,
                "trigger_id",
            )
            if parent_revision is None:
                continue
            clone = parent_revision.model_copy(
                update={
                    "id": self._next_revision_id(
                        trigger.id,
                        self.records.trigger_revisions.values(),
                        "trigger_id",
                    ),
                    "application_revision_id": draft.id,
                    "action_revision_id": action_revision_ids.get(
                        parent_revision.action_revision_id,
                        parent_revision.action_revision_id,
                    ),
                    "created_at": now_utc(),
                    "created_by": "revision_clone",
                }
            )
            self.records.trigger_revisions.save(clone)
            self._link_trigger_revision(draft.application_id, trigger, clone)

    def _resolve_parent(self, application, parent_revision_id: str | None):
        if parent_revision_id is not None:
            parent = self.records.revisions.get(parent_revision_id)
            if parent is None or parent.application_id != application.id:
                raise ValidationFailure(
                    f"unknown parent revision: {parent_revision_id}"
                )
            return parent
        drafts = [
            revision
            for revision in self.uow.applications.list_revisions(application.id)
            if revision.status == "draft"
        ]
        if drafts:
            return max(drafts, key=lambda revision: revision.created_at)
        if application.active_revision_id:
            return self.records.revisions.get(application.active_revision_id)
        return None

    @staticmethod
    def _owned_revision_for_parent(
        owner_id: str, revisions, parent_id: str, owner_field: str
    ):
        matches = [
            revision
            for revision in revisions.values()
            if getattr(revision, owner_field) == owner_id
            and revision.application_revision_id == parent_id
        ]
        if not matches:
            return None
        return max(matches, key=lambda revision: revision.created_at)

    @staticmethod
    def _next_revision_id(
        stable_id: str, revisions: list, owner_field: str
    ) -> str:
        count = len(
            [
                revision
                for revision in revisions
                if getattr(revision, owner_field) == stable_id
            ]
        )
        return f"{stable_id}@{count + 1}"

    def _link_runtime_dependency(self, dependency: RuntimeDependency) -> None:
        self.store.create_edge(
            node_ref(dependency.owner_kind, id=dependency.owner_id),
            "DECLARES_DEPENDENCY",
            node_ref("RuntimeDependency", id=dependency.id),
        )

    def _link_object_type_revision(
        self,
        application_id: str,
        object_type: ObjectType,
        revision: ObjectTypeRevision,
    ) -> None:
        self._link_revision(
            application_id,
            "DEFINES_OBJECT_TYPE",
            "ObjectType",
            object_type.id,
            "ObjectTypeRevision",
            revision.id,
        )

    def _link_relation_type_revision(
        self,
        application_id: str,
        relation_type: RelationType,
        revision: RelationTypeRevision,
    ) -> None:
        self._link_revision(
            application_id,
            "DEFINES_RELATION_TYPE",
            "RelationType",
            relation_type.id,
            "RelationTypeRevision",
            revision.id,
        )
        revision_ref = node_ref("RelationTypeRevision", id=revision.id)
        self.store.replace_single_edge(
            revision_ref,
            "FROM_TYPE",
            node_ref("ObjectType", id=revision.from_object_type_id),
        )
        self.store.replace_single_edge(
            revision_ref,
            "TO_TYPE",
            node_ref("ObjectType", id=revision.to_object_type_id),
        )

    def _link_action_revision(
        self,
        application_id: str,
        action: Action,
        revision: ActionRevision,
    ) -> None:
        self._link_revision(
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

    def _link_trigger_revision(
        self,
        application_id: str,
        trigger: Trigger,
        revision: TriggerRevision,
    ) -> None:
        self._link_revision(
            application_id,
            "DEFINES_TRIGGER",
            "Trigger",
            trigger.id,
            "TriggerRevision",
            revision.id,
        )
        self.store.replace_single_edge(
            node_ref("TriggerRevision", id=revision.id),
            "INVOKES",
            node_ref("ActionRevision", id=revision.action_revision_id),
        )

    def _link_revision(
        self,
        application_id: str,
        ownership_edge: str,
        stable_label: str,
        stable_id: str,
        revision_label: str,
        revision_id: str,
    ) -> None:
        self.store.create_edge(
            node_ref("Application", id=application_id),
            ownership_edge,
            node_ref(stable_label, id=stable_id),
        )
        self.store.create_edge(
            node_ref(stable_label, id=stable_id),
            "HAS_REVISION",
            node_ref(revision_label, id=revision_id),
        )
