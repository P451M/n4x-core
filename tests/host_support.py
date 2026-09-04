from __future__ import annotations

from pathlib import Path

from n4x.host.archive import write_official_archive
from n4x.host.supervisor import Host
from n4x.testing import InMemoryGraphStore

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"


def official_archive(tmp_path: Path, *, version: str = "test") -> Path:
    destination = tmp_path / "official-system.zip"
    write_official_archive(destination, backend_root=BACKEND_ROOT, version=version)
    return destination


def make_host(tmp_path: Path, *, version: str = "test") -> Host:
    return Host(
        tmp_path / "install",
        graph_store=InMemoryGraphStore(),
        official_archive=official_archive(tmp_path, version=version),
    )
