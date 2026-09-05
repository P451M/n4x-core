"""Application identity owned by the System, not the host."""

from __future__ import annotations

from n4x.graph.bindings import RevisionBindings
from n4x.graph.intern_gc import delete_interned_orphans
from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.intern import runtime_dependency_id
from n4x.kernel.models import (
    Application,
    ApplicationRevision,
    DataSpace,
    RuntimeDependency,
    UiProfile,
)
from n4x.graph.service_base import transactional
from n4x.source_store.service import SourceStore
from n4x.system.drafts import Drafts

APPLICATION_COPY_EDGES = (
    "HAS_SOURCE_TREE",
    "HAS_ACTION_REVISION",
    "HAS_OBJECT_TYPE_REVISION",
    "HAS_RELATION_TYPE_REVISION",
    "HAS_TRIGGER_REVISION",
    "HAS_TEST",
    "DECLARES_DEPENDENCY",
)


class Applications:
    def __init__(self, uow: GraphUnitOfWork, source: SourceStore) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.source = source
        self.drafts = Drafts(self.records)
        self.bindings = RevisionBindings(uow)

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
        draft = self._draft(application.id)
        if draft is not None:
            if parent_revision_id is None or parent_revision_id in {
                draft.id,
                draft.parent_revision_id,
            }:
                return draft
            raise ValidationFailure(
                f"Application {application_id} already has draft {draft.id}; "
                "call discard_application_revision to start from a different parent"
            )
        parent = self._resolve_parent(application, parent_revision_id)
        revision_id = (
            f"{application_id}@"
            f"{len(self.uow.applications.list_revisions(application_id)) + 1}"
        )
        revision = ApplicationRevision(
            id=revision_id,
            application_id=application_id,
            parent_revision_id=None if parent is None else parent.id,
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
        self.uow.applications.attach_revision(application_id, revision.id)
        if parent is not None:
            self.store.create_edge(
                node_ref("ApplicationRevision", id=revision.id),
                "PARENT_REVISION",
                node_ref("ApplicationRevision", id=parent.id),
            )
            self.bindings.copy_edges(parent.id, revision.id, APPLICATION_COPY_EDGES)
        else:
            self.bindings.set_tree(revision.id, self.source.interned_empty_tree().id)
        return revision

    @transactional
    def discard_revision(self, application_revision_id: str) -> None:
        revision = self.drafts.require_application(application_revision_id)
        tree = self.bindings.tree(revision.id)
        revision_ref = node_ref("ApplicationRevision", id=revision.id)
        seen: set[str] = set()
        for edge in list(self.store.list_edges(revision_ref)):
            if edge.type not in seen:
                self.store.delete_edge(revision_ref, edge.type)
                seen.add(edge.type)
        self.store.delete_edge(
            node_ref("Application", id=revision.application_id),
            "HAS_REVISION",
            revision_ref,
        )
        if tree.status == "draft" and tree.owner_id == revision.id:
            self.source.delete_working_tree(tree.id)
        self.records.revisions.delete(revision.id)
        delete_interned_orphans(self.uow)

    @transactional
    def create_runtime_dependency(
        self,
        application_revision_id: str,
        ecosystem: str,
        package: str,
        spec: str,
    ) -> RuntimeDependency:
        self.drafts.require_application(application_revision_id)
        if ecosystem != "python":
            raise ValueError(
                "ApplicationRevision dependencies must use the python ecosystem; "
                "declare JavaScript dependencies on an ExperienceRevision"
            )
        return upsert_runtime_dependency(
            self.uow,
            self.bindings,
            application_revision_id,
            ecosystem="python",
            package=package,
            spec=spec,
        )

    def _draft(self, application_id: str) -> ApplicationRevision | None:
        drafts = [
            revision
            for revision in self.uow.applications.list_revisions(application_id)
            if revision.status == "draft"
        ]
        if not drafts:
            return None
        return max(drafts, key=lambda revision: revision.created_at)

    def _resolve_parent(
        self, application: Application, parent_revision_id: str | None
    ) -> ApplicationRevision | None:
        if parent_revision_id is not None:
            parent = self.records.revisions.get(parent_revision_id)
            if parent is None or parent.application_id != application.id:
                raise ValidationFailure(
                    f"unknown parent revision: {parent_revision_id}"
                )
            return parent
        if application.active_revision_id:
            return self.records.revisions.get(application.active_revision_id)
        return None


def upsert_runtime_dependency(
    uow: GraphUnitOfWork,
    bindings: RevisionBindings,
    revision_id: str,
    *,
    ecosystem: str,
    package: str,
    spec: str,
) -> RuntimeDependency:
    dependency_id = runtime_dependency_id(
        ecosystem=ecosystem, package=package, spec=spec
    )
    dependency = uow.records.runtime_dependencies.get(dependency_id)
    if dependency is None:
        dependency = RuntimeDependency(
            id=dependency_id,
            ecosystem=ecosystem,  # type: ignore[arg-type]
            package=package,
            spec=spec,
        )
        uow.records.runtime_dependencies.save(dependency)
    if dependency.id not in {item.id for item in bindings.dependencies(revision_id)}:
        label, _ = bindings.resolve(revision_id)
        uow.store.create_edge(
            node_ref(label, id=revision_id),
            "DECLARES_DEPENDENCY",
            node_ref("RuntimeDependency", id=dependency.id),
        )
    return dependency
