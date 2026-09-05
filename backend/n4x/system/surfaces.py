"""Experience Surface declarations owned by the System."""

from __future__ import annotations

from typing import Any

from n4x.graph.bindings import RevisionBindings
from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.intern import experience_surface_id
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
        self.bindings = RevisionBindings(uow)

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
        if self.bindings.named(
            revision.id,
            "DECLARES_SURFACE",
            self.records.experience_surfaces,
            "surface_id",
            surface_id,
        ) is not None:
            raise ValueError(
                f"Surface already exists: {experience_revision_id}/{surface_id}"
            )
        surface = self._upsert(
            surface_id=surface_id,
            surface_type=surface_type,
            surface_type_version=surface_type_version,
            entrypoint=entrypoint,
            source_paths=source_paths,
            title=title,
            description=description,
            config=config or {},
            created_by=created_by,
        )
        self._validate_sources(revision.id, surface)
        self.bindings.replace_named(
            revision.id,
            "DECLARES_SURFACE",
            None,
            surface.id,
            "ExperienceSurface",
        )
        return surface

    def inspect(
        self, experience_revision_id: str, surface_id: str
    ) -> ExperienceSurface:
        return self.bindings.surface(experience_revision_id, surface_id)

    def list(self, experience_revision_id: str) -> list[ExperienceSurface]:
        return sorted(
            self.bindings.surfaces(experience_revision_id),
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
        surface = self._upsert(
            surface_id=surface_id,
            surface_type=values["surface_type"],
            surface_type_version=values["surface_type_version"],
            entrypoint=values["entrypoint"],
            source_paths=values["source_paths"],
            title=values["title"],
            description=values["description"],
            config=values["config"],
            created_by=current.created_by,
        )
        self._validate_sources(revision.id, surface)
        self.bindings.replace_named(
            revision.id,
            "DECLARES_SURFACE",
            current.id,
            surface.id,
            "ExperienceSurface",
        )
        return surface

    @transactional
    def delete(self, experience_revision_id: str, surface_id: str) -> None:
        revision = self.drafts.require_experience(experience_revision_id)
        current = self.inspect(revision.id, surface_id)
        self.bindings.remove_named(
            revision.id, "DECLARES_SURFACE", "ExperienceSurface", current.id
        )

    def relink(self, surface: ExperienceSurface) -> None:
        return None

    def _upsert(
        self,
        *,
        surface_id: str,
        surface_type: str,
        surface_type_version: int,
        entrypoint: str,
        source_paths: list[str],
        title: str,
        description: str | None,
        config: dict[str, Any],
        created_by: str,
    ) -> ExperienceSurface:
        payload = {
            "surface_id": surface_id,
            "surface_type": surface_type,
            "surface_type_version": surface_type_version,
            "entrypoint": entrypoint,
            "source_paths": list(source_paths),
            "title": title,
            "description": description,
            "config": config,
        }
        interned_id = experience_surface_id(payload)
        surface = self.records.experience_surfaces.get(interned_id)
        if surface is None:
            surface = ExperienceSurface(
                id=interned_id,
                created_by=created_by,
                **payload,
            )
            self.records.experience_surfaces.save(surface)
        return surface

    def _validate_sources(
        self, experience_revision_id: str, surface: ExperienceSurface
    ) -> None:
        tree_id = self.bindings.tree_id(experience_revision_id)
        for path in surface.source_paths:
            self.source.read_source_file(tree_id, path)
