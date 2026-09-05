from __future__ import annotations

from pathlib import Path

from tests.host_support import make_host


def test_host_persists_system_revision_in_graph(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    try:
        revision = host.boot_active()
        stored = host.system_graph.get_revision(revision.id)
        assert stored.content_root.startswith("sha256:")
        assert host.system_graph.tree_id(stored.id) == host.system_graph.tree_id(
            revision.id
        )
        enabled = host.system_graph.enabled_revision()
        assert enabled is not None
        assert enabled.id == revision.id
    finally:
        host.release()
