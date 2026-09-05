"""Experience identity owned by the System, not the host."""

from __future__ import annotations

from typing import Any

from n4x.graph.bindings import RevisionBindings
from n4x.graph.intern_gc import delete_interned_orphans
from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ConcurrentGraphUpdateError, ValidationFailure
from n4x.kernel.models import (
    ApplicationAccessDeclaration,
    Experience,
    ExperienceRevision,
    RuntimeDependency,
    SourceFileSummary,
    UiProfile,
)
from n4x.graph.service_base import transactional
from n4x.source_store.service import SourceStore
from n4x.system.applications import upsert_runtime_dependency
from n4x.system.drafts import Drafts
from n4x.system.surfaces import Surfaces

EXPERIENCE_COPY_EDGES = (
    "HAS_SOURCE_TREE",
    "DECLARES_DEPENDENCY",
    "DECLARES_SURFACE",
)


class Experiences:
    def __init__(self, uow: GraphUnitOfWork, source: SourceStore) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.source = source
        self.drafts = Drafts(self.records)
        self.surfaces = Surfaces(uow, source)
        self.bindings = RevisionBindings(uow)

    @transactional
    def create(
        self, experience_id: str, name: str, description: str = ""
    ) -> Experience:
        experience = Experience(
            id=experience_id, name=name, description=description
        )
        self.uow.experiences.save(experience)
        self.uow.experiences.attach_to_root(experience.id)
        return experience

    def list(self) -> list[Experience]:
        return sorted(
            (
                item
                for item in self.records.experiences.values()
                if item.status != "disabled"
            ),
            key=lambda item: item.id,
        )

    def inspect(self, experience_id: str) -> dict[str, Any]:
        experience = self.records.experiences.get(experience_id)
        if experience is None:
            raise ValidationFailure(f"Experience {experience_id!r} not found")
        revisions = sorted(
            (
                item
                for item in self.records.experience_revisions.values()
                if item.experience_id == experience_id
            ),
            key=lambda item: item.created_at,
        )
        return {
            "experience": experience.model_dump(mode="json"),
            "revisions": [
                self.bindings.payload(revision.id) for revision in revisions
            ],
        }

    def inspect_revision(self, experience_revision_id: str) -> dict[str, Any]:
        revision = self.records.experience_revisions[experience_revision_id]
        tree_id = self.bindings.tree_id(revision.id)
        current = {
            item.path: item.content_hash
            for item in self.source.list_source_tree(tree_id)
        }
        previous: dict[str, str] = {}
        if revision.parent_revision_id is not None:
            parent_tree_id = self.bindings.tree_id(revision.parent_revision_id)
            previous = {
                item.path: item.content_hash
                for item in self.source.list_source_tree(parent_tree_id)
            }
        dirty_paths = sorted(
            {
                path
                for path, digest in current.items()
                if previous.get(path) != digest
            }
            | {path for path in previous if path not in current}
        )
        revision_payload = self.bindings.payload(revision.id)
        for row in revision_payload["application_access"]:
            application = self.records.applications.get(row["application_id"])
            row["active_revision_id"] = (
                None if application is None else application.active_revision_id
            )
        return {
            "revision": revision_payload,
            "source_files": [
                SourceFileSummary.from_source_file(item).model_dump(mode="json")
                for item in self.source.list_source_tree(tree_id)
            ],
            "dirty_paths": dirty_paths,
            "dependencies": [
                item.model_dump(mode="json")
                for item in self.bindings.dependencies(revision.id)
            ],
            "surfaces": [
                item.model_dump(mode="json")
                for item in self.surfaces.list(revision.id)
            ],
            "build_artifacts": [
                item.model_dump(mode="json")
                for item in self.records.build_artifacts.values()
                if item.owner_kind == "ExperienceRevision"
                and item.owner_id == revision.id
            ],
        }

    @transactional
    def retire(
        self,
        experience_id: str,
        *,
        expected_active_revision_id: str | None = None,
    ) -> Experience:
        experience = self.records.experiences[experience_id]
        if experience.status == "disabled" and experience.active_revision_id is None:
            return experience
        if (
            expected_active_revision_id is not None
            and experience.active_revision_id != expected_active_revision_id
        ):
            raise ConcurrentGraphUpdateError(
                f"Experience {experience_id} active revision changed before retirement"
            )
        self.uow.experiences.clear_active_revision(
            experience_id,
            expected_revision_id=experience.active_revision_id,
        )
        retired = experience.model_copy(
            update={"status": "disabled", "active_revision_id": None}
        )
        self.uow.experiences.save(retired)
        return retired

    @transactional
    def create_revision(
        self,
        experience_id: str,
        *,
        created_by: str = "system",
        ui_profile: UiProfile | None = None,
        parent_revision_id: str | None = None,
        application_access: list[ApplicationAccessDeclaration | dict[str, Any]]
        | None = None,
    ) -> ExperienceRevision:
        experience = self.records.experiences[experience_id]
        draft = self._draft(experience.id)
        if draft is not None:
            if parent_revision_id is None or parent_revision_id in {
                draft.id,
                draft.parent_revision_id,
            }:
                return draft
            raise ValidationFailure(
                f"Experience {experience_id} already has draft {draft.id}; "
                "call discard_experience_revision to start from a different parent"
            )
        parent = self._resolve_parent(experience, parent_revision_id)
        access = [
            item
            if isinstance(item, ApplicationAccessDeclaration)
            else ApplicationAccessDeclaration.model_validate(item)
            for item in (
                application_access
                if application_access is not None
                else parent.application_access
                if parent is not None
                else []
            )
        ]
        self._validate_access_declarations(access)
        revision_id = (
            f"{experience_id}.experience@"
            f"{len(self.uow.experiences.list_revisions(experience_id)) + 1}"
        )
        revision = ExperienceRevision(
            id=revision_id,
            experience_id=experience_id,
            parent_revision_id=None if parent is None else parent.id,
            ui_profile=(
                ui_profile
                if ui_profile is not None
                else parent.ui_profile
                if parent is not None
                else "n4x-default"
            ),
            created_by=created_by,
            application_access=access,
        )
        self.uow.experiences.save_revision(revision)
        self.uow.experiences.attach_revision(experience_id, revision.id)
        if parent is not None:
            self.store.create_edge(
                node_ref("ExperienceRevision", id=revision.id),
                "PARENT_REVISION",
                node_ref("ExperienceRevision", id=parent.id),
            )
            self.bindings.copy_edges(parent.id, revision.id, EXPERIENCE_COPY_EDGES)
        else:
            self.bindings.set_tree(revision.id, self.source.interned_empty_tree().id)
        self._link_access(revision)
        return revision

    @transactional
    def discard_revision(self, experience_revision_id: str) -> None:
        revision = self.drafts.require_experience(experience_revision_id)
        tree = self.bindings.tree(revision.id)
        revision_ref = node_ref("ExperienceRevision", id=revision.id)
        seen: set[str] = set()
        for edge in list(self.store.list_edges(revision_ref)):
            if edge.type not in seen:
                self.store.delete_edge(revision_ref, edge.type)
                seen.add(edge.type)
        self.store.delete_edge(
            node_ref("Experience", id=revision.experience_id),
            "HAS_REVISION",
            revision_ref,
        )
        if tree.status == "draft" and tree.owner_id == revision.id:
            self.source.delete_working_tree(tree.id)
        self.records.experience_revisions.delete(revision.id)
        delete_interned_orphans(self.uow)

    @transactional
    def set_application_access(
        self,
        experience_revision_id: str,
        application_access: list[ApplicationAccessDeclaration | dict[str, Any]],
    ) -> ExperienceRevision:
        revision = self.drafts.require_experience(experience_revision_id)
        declarations = [
            item
            if isinstance(item, ApplicationAccessDeclaration)
            else ApplicationAccessDeclaration.model_validate(item)
            for item in application_access
        ]
        self._validate_access_declarations(declarations)
        updated = revision.model_copy(update={"application_access": declarations})
        updated = ExperienceRevision.model_validate(updated.model_dump())
        self.records.experience_revisions.save(updated)
        self._link_access(updated)
        return updated

    @transactional
    def create_runtime_dependency(
        self,
        experience_revision_id: str,
        ecosystem: str,
        package: str,
        spec: str,
    ) -> RuntimeDependency:
        self.drafts.require_experience(experience_revision_id)
        if ecosystem != "javascript":
            raise ValueError(
                "ExperienceRevision dependencies must use the javascript "
                "ecosystem; declare Python dependencies on an ApplicationRevision"
            )
        return upsert_runtime_dependency(
            self.uow,
            self.bindings,
            experience_revision_id,
            ecosystem="javascript",
            package=package,
            spec=spec,
        )

    def _validate_access_declarations(
        self, declarations: list[ApplicationAccessDeclaration]
    ) -> None:
        for declaration in declarations:
            application = self.records.applications.get(declaration.application_id)
            if application is None:
                raise ValueError(
                    f"unknown application access target: {declaration.application_id}"
                )
            for identifiers, records, kind in (
                (
                    declaration.object_type_ids,
                    self.records.object_types,
                    "object type",
                ),
                (
                    declaration.relation_type_ids,
                    self.records.relation_types,
                    "relation type",
                ),
                (declaration.action_ids, self.records.actions, "action"),
                (
                    declaration.secret_reference_ids,
                    self.records.secret_references,
                    "secret reference",
                ),
            ):
                for identifier in identifiers or []:
                    target = records.get(identifier)
                    if (
                        target is None
                        or target.application_id != declaration.application_id
                    ):
                        raise ValueError(
                            f"invalid {kind} access declaration: {identifier}"
                        )

    def _draft(self, experience_id: str) -> ExperienceRevision | None:
        drafts = [
            revision
            for revision in self.uow.experiences.list_revisions(experience_id)
            if revision.status == "draft"
        ]
        if not drafts:
            return None
        return max(drafts, key=lambda revision: revision.created_at)

    def _resolve_parent(
        self, experience: Experience, parent_revision_id: str | None
    ) -> ExperienceRevision | None:
        if parent_revision_id is not None:
            parent = self.records.experience_revisions.get(parent_revision_id)
            if parent is None or parent.experience_id != experience.id:
                raise ValidationFailure(
                    f"unknown parent revision: {parent_revision_id}"
                )
            return parent
        if experience.active_revision_id:
            return self.records.experience_revisions.get(experience.active_revision_id)
        return None

    def _link_access(self, revision: ExperienceRevision) -> None:
        revision_ref = node_ref("ExperienceRevision", id=revision.id)
        self.store.delete_edge(revision_ref, "USES_APPLICATION")
        for access in revision.application_access:
            self.store.create_edge(
                revision_ref,
                "USES_APPLICATION",
                node_ref("Application", id=access.application_id),
            )
