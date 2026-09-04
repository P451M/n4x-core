from __future__ import annotations

import os
from pathlib import Path

from starlette.applications import Starlette

from n4x.graph.integrity import GraphIntegrityService
from n4x.graph.neo4j import Neo4jConfig, Neo4jGraph
from n4x.graph.store import GraphStore, Neo4jGraphStore
from n4x.host.locking import HostLock
from n4x.host.identity import official_archive_path
from n4x.host.materialize import materialize_from_graph, materialized_root
from n4x.host.proxy import ReverseProxy, create_control_routes
from n4x.host.system_graph import SystemGraph
from n4x.host.worker import WorkerBootError, WorkerSpec, WorkerSupervisor
from n4x.kernel.models import SystemRevision


class Host:
    def __init__(
        self,
        install_root: Path,
        *,
        graph_store: GraphStore | None = None,
        official_archive: Path | None = None,
        worker_env: dict[str, str] | None = None,
    ) -> None:
        self.install_root = install_root
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.lock = HostLock(install_root / "host.lock")
        self.supervisor = WorkerSupervisor(install_root / "runtime")
        self.graph_store = graph_store if graph_store is not None else _graph_store()
        self.system_graph = SystemGraph(self.graph_store)
        GraphIntegrityService(self.graph_store, self.system_graph.uow).bootstrap_schema()
        self.official_archive = (
            Path(official_archive) if official_archive is not None else official_archive_path()
        )
        self.worker_env = dict(worker_env or {})
        self.control_app = Starlette(routes=create_control_routes(self))
        self.app = ReverseProxy(self)

    def acquire(self) -> None:
        self.lock.acquire()

    def release(self) -> None:
        self.supervisor.stop()
        self.lock.release()

    def boot_active(self) -> SystemRevision:
        self.acquire()
        revision = self.system_graph.enabled_revision()
        if revision is None:
            revision = self.system_graph.import_official_archive(
                self.official_archive, enable=True
            )
        return self._materialize_and_start(revision)

    def import_official_archive(self, archive: Path) -> dict[str, object]:
        revision = self.system_graph.import_official_archive(Path(archive), enable=False)
        return {
            "imported": revision.id,
            "enabled": False,
            "content_root": revision.content_root,
            "source_tree_id": revision.source_tree_id,
            "version": revision.version,
        }

    def enable_revision(self, revision_id: str) -> SystemRevision:
        if not self.lock.held:
            self.acquire()
        previous = self.system_graph.enabled_revision()
        revision = self.system_graph.get_revision(revision_id)
        self.supervisor.stop()
        try:
            self._materialize_and_start(revision)
        except WorkerBootError:
            if previous is not None:
                try:
                    self._materialize_and_start(previous)
                except WorkerBootError:
                    pass
            raise
        return self.system_graph.enable_revision(revision_id)

    def run_stdio(self) -> int:
        import subprocess

        from n4x.host.worker import worker_pythonpath

        self.acquire()
        revision = self.system_graph.enabled_revision()
        if revision is None:
            revision = self.system_graph.import_official_archive(
                self.official_archive, enable=True
            )
        destination = materialized_root(self.install_root, revision.id)
        materialize_from_graph(self.system_graph, revision, destination)
        spec = self.spec_for(revision)
        env = os.environ.copy()
        env.update(self.worker_env)
        env.update(
            {
                "N4X_WORKER_MODE": "stdio",
                "N4X_SYSTEM_REVISION_ID": revision.id,
                "N4X_SYSTEM_CONTENT_ROOT": revision.content_root,
                "N4X_SYSTEM_SOURCE_TREE_ID": revision.source_tree_id,
                "N4X_SYSTEM_MATERIALIZED_ROOT": str(destination),
            }
        )
        env["PYTHONPATH"] = worker_pythonpath(destination, env.get("PYTHONPATH", ""))
        try:
            completed = subprocess.run(spec.resolved_command(), env=env)
            return int(completed.returncode)
        finally:
            self.release()

    def dump_instance(
        self, output: Path | None = None, *, neo4j_dump: Path | None = None
    ) -> dict:
        from datetime import datetime, timezone

        from n4x.host.dump import dump_instance
        from n4x.host.paths import default_exports_root, default_runtime_root

        if output is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output = default_exports_root() / f"instance-{stamp}.n4xi"
        result = dump_instance(
            self,
            Path(output),
            runtime_root=default_runtime_root(),
            neo4j_dump=neo4j_dump,
        )
        result["download_path"] = f"/n4x-host/exports/{Path(output).name}"
        return result

    def list_exports(self) -> dict:
        from n4x.host.paths import default_exports_root

        root = default_exports_root()
        items = []
        if root.is_dir():
            for path in sorted(root.glob("*.n4xi")):
                stat = path.stat()
                items.append(
                    {
                        "name": path.name,
                        "size": stat.st_size,
                        "download_path": f"/n4x-host/exports/{path.name}",
                    }
                )
        return {"exports": items}

    def export_file(self, name: str) -> Path:
        from n4x.host.paths import resolve_export_file

        path = resolve_export_file(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def restore_instance(self, archive: Path, *, neo4j_dump: Path | None = None) -> dict:
        from n4x.host.dump import restore_instance
        from n4x.host.paths import default_runtime_root

        return restore_instance(
            self,
            Path(archive),
            runtime_root=default_runtime_root(),
            neo4j_dump=neo4j_dump,
        )

    def release_status(self, *, index_url: str | None = None) -> dict:
        from n4x.host.release_index import load_release_index

        enabled = self.system_graph.enabled_revision()
        current = "" if enabled is None else enabled.content_root
        releases = [item.to_json() for item in load_release_index(index_url)]
        available = next(
            (item for item in reversed(releases) if item["content_root"] != current),
            None,
        )
        return {
            "revision_id": None if enabled is None else enabled.id,
            "source_tree_id": None if enabled is None else enabled.source_tree_id,
            "content_root": current,
            "releases": releases,
            "update_available": available,
        }

    def health_payload(self) -> dict[str, object]:
        enabled = self.system_graph.enabled_revision()
        worker = self.supervisor.current
        return {
            "status": "ok" if worker is not None else "starting",
            "host": {"adapter": "n4x.host.v1"},
            "system": None
            if enabled is None
            else {
                "revision_id": enabled.id,
                "source_tree_id": enabled.source_tree_id,
                "content_root": enabled.content_root,
                "provenance_kind": enabled.provenance_kind,
            },
            "worker": None
            if worker is None
            else {
                "revision_id": worker.spec.revision_id,
                "port": worker.port,
            },
        }

    def spec_for(self, revision: SystemRevision) -> WorkerSpec:
        root = materialized_root(self.install_root, revision.id)
        return WorkerSpec(
            revision_id=revision.id,
            content_root=revision.content_root,
            source_tree_id=revision.source_tree_id,
            materialized_root=root,
            env=self.worker_env,
        )

    def _materialize_and_start(self, revision: SystemRevision) -> SystemRevision:
        destination = materialized_root(self.install_root, revision.id)
        materialize_from_graph(self.system_graph, revision, destination)
        self.supervisor.start(self.spec_for(revision))
        return revision


def _graph_store() -> GraphStore:
    return Neo4jGraphStore(Neo4jGraph(Neo4jConfig.from_env()))
