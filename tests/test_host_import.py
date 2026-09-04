from __future__ import annotations

from pathlib import Path

from n4x.host.archive import official_content_root, write_official_archive
from tests.host_support import BACKEND_ROOT, make_host


def test_import_does_not_enable(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    result = host.import_official_archive(host.official_archive)
    assert result["enabled"] is False
    assert host.system_graph.enabled_revision() is None
    assert result["content_root"] == official_content_root(BACKEND_ROOT)


def test_import_then_enable(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    try:
        imported = host.import_official_archive(host.official_archive)
        host.acquire()
        host.enable_revision(str(imported["imported"]))
        enabled = host.system_graph.enabled_revision()
        assert enabled is not None
        assert enabled.id == imported["imported"]
    finally:
        host.release()


def test_content_root_includes_action_runner_not_uow(tmp_path: Path) -> None:
    baseline = official_content_root(BACKEND_ROOT)
    scratch = tmp_path / "payload"
    _copy_payload(BACKEND_ROOT, scratch)
    runner = scratch / "n4x" / "runtime" / "action_runner.py"
    runner.write_text(runner.read_text() + "\n# healed\n")
    changed = official_content_root(scratch)
    assert changed != baseline
    uow = scratch / "n4x" / "graph" / "uow.py"
    if uow.is_file():
        uow.write_text(uow.read_text() + "\n# adapter-only\n")
    assert official_content_root(scratch) == changed
    write_official_archive(tmp_path / "changed.zip", backend_root=scratch, version="healed")
    host = make_host(tmp_path)
    imported = host.import_official_archive(tmp_path / "changed.zip")
    assert imported["content_root"] == changed


def _copy_payload(source: Path, dest: Path) -> None:
    import shutil

    shutil.copytree(source / "n4x", dest / "n4x")
