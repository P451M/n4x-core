from __future__ import annotations

import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from n4x.host.identity import HOST_ADAPTER
from n4x.host.supervisor import Host
from n4x.host.worker import WorkerBootError, WorkerSpec, WorkerSupervisor
from n4x.testing import InMemoryGraphStore
from tests.host_support import official_archive, make_host

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_host_boots_official_import_and_proxies_health(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    try:
        revision = host.boot_active()
        client = TestClient(host.app)
        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert body["host"]["adapter"] == HOST_ADAPTER
        assert body["system"]["revision_id"] == revision.id
        assert body["system"]["content_root"].startswith("sha256:")
        assert body["system"]["source_tree_id"] == revision.source_tree_id
        info = client.get("/n4x/system/info")
        assert info.status_code == 200
        assert info.json()["host_abi"] == HOST_ADAPTER
        assert info.json()["content_root"] == revision.content_root
        control = client.get("/n4x-host/control")
        assert control.status_code == 200
        assert control.json()["revision_id"] == revision.id
        assert control.json()["worker"]["revision_id"] == revision.id
        experiences = client.get("/experiences")
        assert experiences.status_code == 200
        assert experiences.json() == {"experiences": []}
        contract = client.get("/bridge/contract")
        assert contract.status_code == 200
        assert contract.json()["version"] == "n4x.experience.bridge.v1"
        staged = client.put("/packages/proxied.n4xp", content=b"through-proxy")
        assert staged.status_code == 200
        assert staged.json()["archive_name"] == "proxied.n4xp"
        downloaded = client.get("/packages/proxied.n4xp")
        assert downloaded.content == b"through-proxy"
    finally:
        host.release()


def test_later_import_does_not_enable(tmp_path: Path) -> None:
    host = make_host(tmp_path, version="first")
    try:
        first = host.boot_active()
        second_zip = official_archive(tmp_path / "second", version="second")
        imported = host.import_official_archive(second_zip)
        assert imported["enabled"] is False
        assert imported["imported"] != first.id
        enabled = host.system_graph.enabled_revision()
        assert enabled is not None
        assert enabled.id == first.id
        host.enable_revision(str(imported["imported"]))
        client = TestClient(host.app)
        assert client.get("/health").json()["system"]["revision_id"] == imported["imported"]
    finally:
        host.release()


def test_hung_import_does_not_take_host_control(tmp_path: Path) -> None:
    host = Host(tmp_path, graph_store=InMemoryGraphStore())
    host.acquire()
    try:
        control = TestClient(host.app).get("/n4x-host/control")
        assert control.status_code == 200
        assert control.json()["runtime"] == "n4x-host"
        supervisor = WorkerSupervisor(tmp_path / "hung-runtime")
        empty = tmp_path / "empty-materialized"
        empty.mkdir()
        with pytest.raises(WorkerBootError):
            supervisor.start(
                WorkerSpec(
                    revision_id="hung",
                    materialized_root=empty,
                    command=[sys.executable, str(FIXTURES / "hung_system_worker.py")],
                    boot_timeout_seconds=0.8,
                )
            )
        still = TestClient(host.app).get("/n4x-host/control")
        assert still.status_code == 200
        assert still.json()["runtime"] == "n4x-host"
    finally:
        host.release()


def test_host_control_http_rejects_non_loopback(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    try:
        host.boot_active()
        remote = TestClient(host.app, client=("203.0.113.10", 50000))
        assert remote.get("/n4x-host/control").status_code == 403
        assert remote.get("/n4x-host/control").json()["error"] == "local_only"
        assert remote.get("/n4x-host/exports").status_code == 403
        assert remote.get("/n4x-host/exports/instance-test.n4xi").status_code == 403
        local = TestClient(host.app)
        assert local.get("/n4x-host/control").status_code == 200
    finally:
        host.release()


def test_enable_rpc_is_local_only(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    try:
        first = host.boot_active()
        imported = host.import_official_archive(
            official_archive(tmp_path / "other", version="other")
        )
        client = TestClient(host.app)
        allowed = client.post(
            "/n4x-host/enable", json={"revision_id": imported["imported"]}
        )
        assert allowed.status_code == 200
        assert allowed.json()["enabled"] == imported["imported"]
        assert client.get("/health").json()["system"]["revision_id"] == imported["imported"]
        assert first.id != imported["imported"]
    finally:
        host.release()


def _failing_spec(host: Host, revision_id: str | None = None):
    original = host.spec_for

    def spec_for(revision):
        spec = original(revision)
        if revision_id is None or revision.id == revision_id:
            spec.command = [sys.executable, "-c", "raise SystemExit(1)"]
            spec.boot_timeout_seconds = 0.8
        return spec

    return spec_for


def test_failed_worker_still_serves_host_control(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    host.spec_for = _failing_spec(host)
    try:
        with pytest.raises(WorkerBootError):
            host.boot_active()
        client = TestClient(host.app)
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["worker"] is None
        assert health.json()["system"]["revision_id"]
        control = client.get("/n4x-host/control")
        assert control.status_code == 200
        assert control.json()["runtime"] == "n4x-host"
    finally:
        host.release()


def test_failed_enable_does_not_move_the_enabled_edge(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    try:
        first = host.boot_active()
        imported = host.import_official_archive(
            official_archive(tmp_path / "broken", version="broken")
        )
        host.spec_for = _failing_spec(host, str(imported["imported"]))
        with pytest.raises(WorkerBootError):
            host.enable_revision(str(imported["imported"]))
        enabled = host.system_graph.enabled_revision()
        assert enabled is not None
        assert enabled.id == first.id
        assert host.supervisor.current is not None
        assert host.supervisor.current.spec.revision_id == first.id
        client = TestClient(host.app)
        refused = client.post(
            "/n4x-host/enable", json={"revision_id": imported["imported"]}
        )
        assert refused.status_code == 503
        assert refused.json()["error"] == "system_worker_unavailable"
        still = host.system_graph.enabled_revision()
        assert still is not None
        assert still.id == first.id
    finally:
        host.release()


def test_healed_source_runs_after_rematerialize(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    try:
        revision = host.boot_active()
        marker = "n4x_healed_marker = True\n"
        host.system_graph.source.write_source_file(
            revision.source_tree_id,
            "n4x/system/healed_marker.py",
            marker,
            role="helper",
            language="python",
        )
        host.enable_revision(revision.id)
        dest = host.install_root / "revisions" / revision.id / "n4x" / "system" / "healed_marker.py"
        assert dest.read_text() == marker
    finally:
        host.release()
