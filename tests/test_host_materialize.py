from __future__ import annotations

from pathlib import Path

from n4x.host.archive import official_content_root
from n4x.host.materialize import materialize_from_graph, materialized_root
from tests.host_support import BACKEND_ROOT, make_host


def test_materialize_writes_graph_files(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    revision = host.system_graph.import_official_archive(
        host.official_archive, enable=True
    )
    dest = materialized_root(tmp_path / "out", revision.id)
    materialize_from_graph(host.system_graph, revision, dest)
    assert (dest / "n4x" / "system" / "worker.py").is_file()
    assert (dest / "n4x" / "runtime" / "action_runner.py").is_file()
    assert not (dest / "n4x" / "graph").exists()
    assert revision.content_root == official_content_root(BACKEND_ROOT)


def test_empty_boot_imports_and_enables(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    try:
        revision = host.boot_active()
        enabled = host.system_graph.enabled_revision()
        assert enabled is not None
        assert enabled.id == revision.id
        assert (host.install_root / "revisions" / revision.id / "n4x" / "system" / "worker.py").is_file()
    finally:
        host.release()
