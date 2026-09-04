"""Experience identity owned by the System, not the host."""

from __future__ import annotations

import uuid
from typing import Any

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
    now_utc,
)
from n4x.graph.service_base import transactional
from n4x.source_store.service import SourceStore
from n4x.system.drafts import Drafts
from n4x.system.surfaces import Surfaces


class Experiences:
    def __init__(self, uow: GraphUnitOfWork, source: SourceStore) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.source = source
        self.drafts = Drafts(self.records)
        self.surfaces = Surfaces(uow, source)

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
            "revisions": [revision.model_dump(mode="json") for revision in revisions],
        }

    def inspect_revision(self, experience_revision_id: str) -> dict[str, Any]:
        revision = self.records.experience_revisions[experience_revision_id]
        current = {
            item.path: item.content_hash
            for item in self.source.list_source_tree(revision.source_tree_id)
        }
        previous: dict[str, str] = {}
        if revision.parent_revision_id is not None:
            parent = self.records.experience_revisions[revision.parent_revision_id]
            previous = {
                item.path: item.content_hash
                for item in self.source.list_source_tree(parent.source_tree_id)
            }
        dirty_paths = sorted(
            {
                path
                for path, digest in current.items()
                if previous.get(path) != digest
            }
            | {path for path in previous if path not in current}
        )
        revision_payload = revision.model_dump(mode="json")
        for row in revision_payload["application_access"]:
            application = self.records.applications.get(row["application_id"])
            row["active_revision_id"] = (
                None if application is None else application.active_revision_id
            )
        return {
            "revision": revision_payload,
            "source_files": [
                SourceFileSummary.from_source_file(item).model_dump(mode="json")
                for item in self.source.list_source_tree(revision.source_tree_id)
            ],
            "dirty_paths": dirty_paths,
            "dependencies": [
                item.model_dump(mode="json")
                for item in self.records.runtime_dependencies.values()
                if item.owner_kind == "ExperienceRevision"
                and item.owner_id == revision.id
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
        application_access: list[ApplicationAccessDeclaration | dict[str, Any]]
        | None = None,
    ) -> ExperienceRevision:
        experience = self.records.experiences[experience_id]
        revision_id = (
            f"{experience_id}.experience@"
            f"{len(self.uow.experiences.list_revisions(experience_id)) + 1}"
        )
        parent = self._resolve_parent(experience)
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
        tree = (
            self.source.clone_tree_to_draft(
                parent.source_tree_id,
                experience_id,
                revision_id,
                owner_kind="ExperienceRevision",
            )
            if parent is not None
            else self.source.create_tree(
                experience_id,
                revision_id,
                owner_kind="ExperienceRevision",
            )
        )
        revision = ExperienceRevision(
            id=revision_id,
            experience_id=experience_id,
            source_tree_id=tree.id,
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
        self.store.create_edge(
            node_ref("ExperienceRevision", id=revision.id),
            "HAS_SOURCE_TREE",
            node_ref("SourceTree", id=tree.id),
        )
        if parent is not None:
            self.store.create_edge(
                node_ref("ExperienceRevision", id=revision.id),
                "PARENT_REVISION",
                node_ref("ExperienceRevision", id=parent.id),
            )
            self._clone_frontend(parent, revision)
        self._link_access(revision)
        return revision

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
        dependency = RuntimeDependency(
            id=str(uuid.uuid4()),
            owner_kind="ExperienceRevision",
            owner_id=experience_revision_id,
            ecosystem="javascript",
            package=package,
            spec=spec,
        )
        self.records.runtime_dependencies.save(dependency)
        self._link_dependency(dependency)
        return dependency

    def _clone_frontend(
        self, parent: ExperienceRevision, draft: ExperienceRevision
    ) -> None:
        for dependency in self.records.runtime_dependencies.values():
            if (
                dependency.owner_kind != "ExperienceRevision"
                or dependency.owner_id != parent.id
                or dependency.ecosystem != "javascript"
            ):
                continue
            clone = dependency.model_copy(
                update={
                    "id": str(uuid.uuid4()),
                    "owner_id": draft.id,
                    "created_at": now_utc(),
                }
            )
            self.records.runtime_dependencies.save(clone)
            self._link_dependency(clone)

        for surface in self.records.experience_surfaces.values():
            if surface.experience_revision_id != parent.id:
                continue
            clone = surface.model_copy(
                update={
                    "experience_revision_id": draft.id,
                    "source_tree_id": draft.source_tree_id,
                    "created_at": now_utc(),
                    "created_by": "revision_clone",
                }
            )
            self.records.experience_surfaces.save(clone)
            self.surfaces.relink(clone)

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

    def _resolve_parent(self, experience: Experience) -> ExperienceRevision | None:
        drafts = [
            revision
            for revision in self.uow.experiences.list_revisions(experience.id)
            if revision.status == "draft"
        ]
        if drafts:
            return max(drafts, key=lambda revision: revision.created_at)
        if experience.active_revision_id:
            return self.records.experience_revisions.get(
                experience.active_revision_id
            )
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

    def _link_dependency(self, dependency: RuntimeDependency) -> None:
        self.store.create_edge(
            node_ref("ExperienceRevision", id=dependency.owner_id),
            "DECLARES_DEPENDENCY",
            node_ref("RuntimeDependency", id=dependency.id),
        )
