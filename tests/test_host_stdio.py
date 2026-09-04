from __future__ import annotations

import sys
from pathlib import Path

from n4x.host.supervisor import Host
from n4x.testing import InMemoryGraphStore
from tests.host_support import make_host


def test_host_stdio_runs_worker_then_releases_lock(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    host.system_graph.import_official_archive(host.official_archive, enable=True)
    spec = host.spec_for(host.system_graph.enabled_revision())
    spec.command = [sys.executable, "-c", "raise SystemExit(0)"]
    host.spec_for = lambda revision: spec  # type: ignore[method-assign]
    assert host.run_stdio() == 0
    assert not host.lock.held


def test_host_without_archive_does_not_boot_from_source_tree(tmp_path: Path) -> None:
    host = Host(tmp_path, graph_store=InMemoryGraphStore(), official_archive=tmp_path / "missing.zip")
    assert host.system_graph.enabled_revision() is None
