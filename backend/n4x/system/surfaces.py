"""Experience Surface declarations owned by the System."""

from __future__ import annotations

from typing import Any

from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.models import ExperienceSurface
from n4x.graph.service_base import transactional
from n4x.source_store.service import SourceStore
from n4x.system.drafts import Drafts


class Surfaces:
    def __init__(self, uow: GraphUnitOfWork, source: SourceStore) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.source = source
        self.drafts = Drafts(self.records)

    @transactional
    def create(
        self,
        experience_revision_id: str,
        surface_id: str,
        *,
        surface_type: str,
        surface_type_version: int,
        entrypoint: str,
        source_paths: list[str],
        title: str = "",
        description: str | None = None,
        config: dict[str, Any] | None = None,
        created_by: str = "system",
    ) -> ExperienceSurface:
        revision = self.drafts.require_experience(experience_revision_id)
        key = (revision.id, surface_id)
        if self.records.experience_surfaces.get(key) is not None:
            raise ValueError(
                f"Surface already exists: {experience_revision_id}/{surface_id}"
            )
        surface = ExperienceSurface(
            experience_revision_id=revision.id,
            surface_id=surface_id,
            surface_type=surface_type,
            surface_type_version=surface_type_version,
            entrypoint=entrypoint,
            source_tree_id=revision.source_tree_id,
            source_paths=source_paths,
            title=title,
            description=description,
            config=config or {},
            created_by=created_by,
        )
        self._validate_sources(surface)
        self.records.experience_surfaces.save(surface)
        self._link(surface)
        return surface

    def inspect(
        self, experience_revision_id: str, surface_id: str
    ) -> ExperienceSurface:
        return self.records.experience_surfaces[
            (experience_revision_id, surface_id)
        ]

    def list(self, experience_revision_id: str) -> list[ExperienceSurface]:
        return sorted(
            (
                surface
                for surface in self.records.experience_surfaces.values()
                if surface.experience_revision_id == experience_revision_id
            ),
            key=lambda surface: surface.surface_id,
        )

    @transactional
    def update(
        self,
        experience_revision_id: str,
        surface_id: str,
        *,
        surface_type: str | None = None,
        surface_type_version: int | None = None,
        entrypoint: str | None = None,
        source_paths: list[str] | None = None,
        title: str | None = None,
        description: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> ExperienceSurface:
        revision = self.drafts.require_experience(experience_revision_id)
        current = self.inspect(revision.id, surface_id)
        values = current.model_dump()
        for field, value in {
            "surface_type": surface_type,
            "surface_type_version": surface_type_version,
            "entrypoint": entrypoint,
            "source_paths": source_paths,
            "title": title,
            "description": description,
            "config": config,
        }.items():
            if value is not None:
                values[field] = value
        updated = ExperienceSurface.model_validate(values)
        self._validate_sources(updated)
        self.records.experience_surfaces.save(updated)
        self._link(updated)
        return updated

    @transactional
    def delete(self, experience_revision_id: str, surface_id: str) -> None:
        self.drafts.require_experience(experience_revision_id)
        self.inspect(experience_revision_id, surface_id)
        self.records.experience_surfaces.delete(
            (experience_revision_id, surface_id)
        )

    def relink(self, surface: ExperienceSurface) -> None:
        self._link(surface)

    def _validate_sources(self, surface: ExperienceSurface) -> None:
        revision = self.records.experience_revisions[
            surface.experience_revision_id
        ]
        if surface.source_tree_id != revision.source_tree_id:
            raise ValueError(
                "Surface source tree must belong to its ExperienceRevision"
            )
        for path in surface.source_paths:
            self.source.read_source_file(surface.source_tree_id, path)

    def _link(self, surface: ExperienceSurface) -> None:
        surface_ref = node_ref(
            "ExperienceSurface",
            experience_revision_id=surface.experience_revision_id,
            surface_id=surface.surface_id,
        )
        self.store.create_edge(
            node_ref("ExperienceRevision", id=surface.experience_revision_id),
            "DECLARES_SURFACE",
            surface_ref,
        )
        self.store.delete_edge(surface_ref, "USES_SOURCE")
        for path in surface.source_paths:
            self.store.create_edge(
                surface_ref,
                "USES_SOURCE",
                node_ref(
                    "SourceFile",
                    source_tree_id=surface.source_tree_id,
                    path=path,
                ),
            )
