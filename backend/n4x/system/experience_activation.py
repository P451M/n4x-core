"""Experience activation owned by the System."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Protocol

from n4x.graph.service_base import transactional
from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ImmutableRevisionError, ValidationFailure
from n4x.kernel.models import (
    BuildArtifact,
    ExperienceRevision,
    ExperienceSurface,
    ExperienceValidationReport,
)
from n4x.kernel.surface_types import BrowserSurfaceConfig
from n4x.system.surface_notifications import SurfaceCatalogNotifier


class ExperienceActivationSourcePort(Protocol):
    def snapshot_tree(self, source_tree_id: str, revision_id: str): ...

    def read_source_file(self, source_tree_id: str, path: str): ...


class ExperienceSurfaceRuntimePort(Protocol):
    def build(self, surface: ExperienceSurface): ...


class ExperienceActivationService:
    """Single-pass Experience activation. No durable attempt rows."""

    def __init__(
        self,
        uow: GraphUnitOfWork,
        source: ExperienceActivationSourcePort,
        surface_runtime: ExperienceSurfaceRuntimePort,
        surface_notifier: SurfaceCatalogNotifier | None = None,
    ) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.source = source
        self.surface_runtime = surface_runtime
        self.surface_notifier = surface_notifier

    @transactional
    def validate(
        self, experience_revision_id: str
    ) -> ExperienceValidationReport:
        revision = self.records.experience_revisions[experience_revision_id]
        errors: list[str] = []
        for access in revision.application_access:
            application = self.records.applications.get(access.application_id)
            if (
                application is None
                or application.active_revision_id is None
                or application.status not in {"active", "triggers_paused"}
            ):
                errors.append(
                    f"{access.application_id}: application is not active"
                )
                continue
            for identifiers, collection, revision_collection, label in (
                (
                    access.object_type_ids,
                    self.records.object_types,
                    self.records.object_type_revisions,
                    "object type",
                ),
                (
                    access.relation_type_ids,
                    self.records.relation_types,
                    self.records.relation_type_revisions,
                    "relation type",
                ),
                (
                    access.action_ids,
                    self.records.actions,
                    self.records.action_revisions,
                    "action",
                ),
            ):
                for identifier in identifiers or []:
                    stable = collection.get(identifier)
                    active = (
                        None
                        if stable is None or stable.active_revision_id is None
                        else revision_collection.get(stable.active_revision_id)
                    )
                    if (
                        stable is None
                        or stable.application_id != application.id
                        or active is None
                        or active.application_revision_id
                        != application.active_revision_id
                    ):
                        errors.append(
                            f"{identifier}: {label} is not active in "
                            f"Application {application.id}"
                        )

        for dependency in self.records.runtime_dependencies.values():
            if dependency.owner_id != revision.id:
                continue
            if (
                dependency.owner_kind != "ExperienceRevision"
                or dependency.ecosystem != "javascript"
            ):
                errors.append(
                    f"{dependency.id}: invalid Experience dependency ownership"
                )

        for surface in self._surfaces(revision.id):
            if surface.source_tree_id != revision.source_tree_id:
                errors.append(
                    f"{surface.surface_id}: invalid Experience Surface source ownership"
                )
            for path in surface.source_paths:
                try:
                    self.source.read_source_file(surface.source_tree_id, path)
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        f"{surface.surface_id}:{path}: "
                        f"{type(exc).__name__}: {exc}"
                    )
            if surface.surface_type == "browser":
                config = BrowserSurfaceConfig.model_validate(surface.config)
                if config.pwa is not None:
                    manifest_path = config.pwa.manifest_path
                    candidates = {manifest_path, f"public/{manifest_path}"}
                    if not candidates.intersection(surface.source_paths):
                        errors.append(
                            f"{surface.surface_id}: PWA requires SourceFile at "
                            f"{manifest_path!r} or {f'public/{manifest_path}'!r}"
                        )

        report = ExperienceValidationReport(
            id=str(uuid.uuid4()),
            experience_revision_id=revision.id,
            status="failed" if errors else "passed",
            errors=errors,
        )
        self.records.experience_validation_reports.save(report)
        self.store.create_edge(
            node_ref("ExperienceRevision", id=revision.id),
            "HAS_VALIDATION_REPORT",
            node_ref("ExperienceValidationReport", id=report.id),
        )
        return report

    def activate(self, experience_revision_id: str) -> ExperienceRevision:
        revision = self.records.experience_revisions[experience_revision_id]
        experience = self.records.experiences[revision.experience_id]
        expected_active = experience.active_revision_id
        switched = False
        try:
            with self.uow:
                self._freeze(experience_revision_id)
            self.uow.require_inactive("build Experience surfaces")
            self._build(experience_revision_id)
            with self.uow:
                activated = self._activate_edges(
                    experience_revision_id, expected_active
                )
            switched = True
            if self.surface_notifier is not None:
                self.surface_notifier.activated(activated.experience_id, activated.id)
            return activated
        except Exception as exc:
            if switched:
                raise
            self._reject(experience_revision_id)
            if isinstance(exc, ValidationFailure):
                raise
            raise ValidationFailure(f"{type(exc).__name__}: {exc}") from exc

    def _freeze(self, experience_revision_id: str) -> None:
        revision = self.records.experience_revisions[experience_revision_id]
        if revision.status not in {"draft", "validating"}:
            raise ImmutableRevisionError(
                "Experience activation requires a draft revision"
            )
        if revision.status == "draft":
            revision = revision.model_copy(update={"status": "validating"})
            self.records.experience_revisions.save(revision)
        tree = self.records.source_trees[revision.source_tree_id]
        if tree.status == "immutable_snapshot":
            snapshot = tree
        else:
            snapshot = self.source.snapshot_tree(
                revision.source_tree_id, revision.id
            )
            revision = revision.model_copy(update={"source_tree_id": snapshot.id})
            self.records.experience_revisions.save(revision)
            self.store.replace_single_edge(
                node_ref("ExperienceRevision", id=revision.id),
                "HAS_SOURCE_TREE",
                node_ref("SourceTree", id=snapshot.id),
            )
        for current in self._surfaces(revision.id):
            updated = current.model_copy(update={"source_tree_id": snapshot.id})
            self.records.experience_surfaces.save(updated)
            surface_ref = node_ref(
                "ExperienceSurface",
                experience_revision_id=updated.experience_revision_id,
                surface_id=updated.surface_id,
            )
            self.store.delete_edge(surface_ref, "USES_SOURCE")
            for path in updated.source_paths:
                self.store.create_edge(
                    surface_ref,
                    "USES_SOURCE",
                    node_ref("SourceFile", source_tree_id=snapshot.id, path=path),
                )

    def _build(self, experience_revision_id: str) -> None:
        for surface in self._surfaces(experience_revision_id):
            result = self.surface_runtime.build(surface)
            self._require_pwa_manifest(surface, result.artifact)

    def _activate_edges(
        self,
        experience_revision_id: str,
        expected_active_revision_id: str | None,
    ) -> ExperienceRevision:
        target = self.records.experience_revisions[experience_revision_id]
        experience = self.records.experiences[target.experience_id]
        if experience.active_revision_id != expected_active_revision_id:
            raise ValidationFailure(
                "Experience active revision changed during activation"
            )
        if experience.active_revision_id is not None:
            current = self.records.experience_revisions[
                experience.active_revision_id
            ]
            if current.id != target.id:
                self.records.experience_revisions.save(
                    current.model_copy(update={"status": "superseded"})
                )
        activated = target.model_copy(update={"status": "active"})
        self.records.experience_revisions.save(activated)
        self.records.experiences.save(
            experience.model_copy(
                update={
                    "active_revision_id": activated.id,
                    "status": "active",
                }
            )
        )
        self.uow.experiences.replace_active_revision(
            experience.id,
            activated.id,
            expected_revision_id=expected_active_revision_id,
        )
        return activated

    def _reject(self, experience_revision_id: str) -> None:
        with self.uow:
            revision = self.records.experience_revisions.get(experience_revision_id)
            if revision is None:
                return
            if revision.status == "validating":
                self.records.experience_revisions.save(
                    revision.model_copy(update={"status": "rejected"})
                )

    def _require_pwa_manifest(
        self, surface: ExperienceSurface, artifact: BuildArtifact
    ) -> None:
        if surface.surface_type != "browser":
            return
        config = BrowserSurfaceConfig.model_validate(surface.config)
        if config.pwa is None:
            return
        root_value = None if artifact.manifest is None else artifact.manifest.get("root")
        if not isinstance(root_value, str):
            raise ValidationFailure(
                f"{surface.surface_id}: PWA Surface artifact is missing a root"
            )
        manifest = Path(root_value) / config.pwa.manifest_path
        if not manifest.is_file():
            raise ValidationFailure(
                f"{surface.surface_id}: PWA manifest "
                f"{config.pwa.manifest_path!r} is missing from the artifact"
            )
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationFailure(
                f"{surface.surface_id}: PWA manifest is not valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ValidationFailure(
                f"{surface.surface_id}: PWA manifest must be a JSON object"
            )

    def _surfaces(self, experience_revision_id: str) -> list[ExperienceSurface]:
        return sorted(
            (
                item
                for item in self.records.experience_surfaces.values()
                if item.experience_revision_id == experience_revision_id
            ),
            key=lambda item: item.surface_id,
        )
