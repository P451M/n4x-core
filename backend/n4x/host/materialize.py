"""Write current System SourceFiles to an exec cache. Graph remains SoT."""

from __future__ import annotations

import shutil
from pathlib import Path

from n4x.host.system_graph import SystemGraph
from n4x.kernel.models import SystemRevision


def materialized_root(install_root: Path, revision_id: str) -> Path:
    return install_root / "revisions" / revision_id


def materialize_from_graph(
    graph: SystemGraph,
    revision: SystemRevision,
    destination: Path,
) -> Path:
    destination = destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for path, content in graph.list_source_files(revision.source_tree_id):
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return destination
