"""Graph SoT for SystemRevision. Host uses the installed adapter only."""

from __future__ import annotations

import uuid
from pathlib import Path

from n4x.contracts.action_context import ACTION_CONTEXT_VERSION
from n4x.graph.bindings import RevisionBindings
from n4x.graph.store import GraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.host.archive import read_official_archive
from n4x.host.content import content_root
from n4x.host.identity import HOST_ADAPTER, PLATFORM_SYSTEM_ID
from n4x.kernel.models import PlatformSystem, SourceRole, SystemRevision
from n4x.source_store import SourceStore


def _language_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".md": "markdown",
        ".json": "json",
        ".css": "css",
        ".html": "html",
        ".txt": "text",
    }.get(suffix, "text")


def _role_for(path: str) -> SourceRole:
    return "helper"


class SystemGraph:
    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self.uow = GraphUnitOfWork(store)
        self.source = SourceStore(store, self.uow)
        self.bindings = RevisionBindings(self.uow)

    def tree_id(self, revision_id: str) -> str:
        return self.bindings.tree_id(revision_id)

    def ensure_platform_system(self) -> PlatformSystem:
        with self.uow:
            current = self.uow.systems.get(PLATFORM_SYSTEM_ID)
            if current is not None:
                return current
            system = PlatformSystem(id=PLATFORM_SYSTEM_ID, name="N4X")
            self.uow.systems.save(system)
            self.uow.systems.attach_to_root(system.id)
            return system

    def enabled_revision(self) -> SystemRevision | None:
        with self.uow:
            system = self.uow.systems.get(PLATFORM_SYSTEM_ID)
            if system is None or system.active_revision_id is None:
                return None
            return self.uow.systems.get_revision(system.active_revision_id)

    def get_revision(self, revision_id: str) -> SystemRevision:
        with self.uow:
            revision = self.uow.systems.get_revision(revision_id)
            if revision is None:
                raise KeyError(revision_id)
            return revision

    def import_official_archive(
        self,
        archive: Path,
        *,
        enable: bool = False,
    ) -> SystemRevision:
        manifest, files = read_official_archive(archive)
        digest = content_root(files, abi_range=HOST_ADAPTER)
        declared = str(manifest.get("content_root") or "")
        if declared and declared != digest:
            raise ValueError(
                f"archive content_root {declared} != payload {digest}"
            )
        version = str(manifest.get("version") or archive.stem)
        revision_id = f"official.{version}.{uuid.uuid4().hex[:8]}"
        return self._write_revision(
            revision_id,
            files,
            content_root=digest,
            provenance_kind="official",
            version=version,
            enable=enable,
        )

    def enable_revision(self, revision_id: str) -> SystemRevision:
        with self.uow:
            revision = self.uow.systems.get_revision(revision_id)
            if revision is None:
                raise KeyError(revision_id)
            system = self.ensure_platform_system()
            self.uow.systems.replace_active_revision(
                PLATFORM_SYSTEM_ID,
                revision.id,
                expected_revision_id=system.active_revision_id,
            )
            self.uow.systems.save(
                system.model_copy(update={"active_revision_id": revision.id})
            )
            return revision

    def list_source_files(self, source_tree_id: str) -> list[tuple[str, str]]:
        return [
            (file.path, file.content)
            for file in self.source.list_source_tree(source_tree_id)
        ]

    def _write_revision(
        self,
        revision_id: str,
        files: dict[str, bytes],
        *,
        content_root: str,
        provenance_kind: str,
        version: str,
        enable: bool,
    ) -> SystemRevision:
        with self.uow:
            system = self.ensure_platform_system()
            revision = SystemRevision(
                id=revision_id,
                system_id=PLATFORM_SYSTEM_ID,
                content_root=content_root,
                host_abi=HOST_ADAPTER,
                action_context=ACTION_CONTEXT_VERSION,
                provenance_kind=provenance_kind,  # type: ignore[arg-type]
                version=version,
            )
            self.uow.systems.save_revision(revision)
            self.uow.systems.attach_revision(PLATFORM_SYSTEM_ID, revision.id)
            tree = self.source.create_working_tree(
                revision.id, owner_kind="SystemRevision"
            )
            self.store.create_edge(
                node_ref("SystemRevision", id=revision.id),
                "HAS_SOURCE_TREE",
                node_ref("SourceTree", id=tree.id),
            )
            for path, blob in sorted(files.items()):
                self.source.write_source_file(
                    revision.id,
                    path,
                    blob.decode("utf-8"),
                    role=_role_for(path),
                    language=_language_for(path),
                    actor="host",
                    tool="import_official_archive",
                )
            tree = self.source.intern_tree(revision.id)
            if enable:
                self.uow.systems.replace_active_revision(
                    PLATFORM_SYSTEM_ID,
                    revision.id,
                    expected_revision_id=system.active_revision_id,
                )
                self.uow.systems.save(
                    system.model_copy(update={"active_revision_id": revision.id})
                )
            return revision
