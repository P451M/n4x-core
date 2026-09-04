from __future__ import annotations

import tarfile
from pathlib import Path

from starlette.testclient import TestClient

from n4x.host.dump import dump_instance, restore_instance
from n4x.host.instance_bundle import NEO4J_MEMBER
from n4x.host.supervisor import Host
from n4x.testing import InMemoryGraphStore


def test_dump_restore_roundtrip_excludes_secret_key(tmp_path: Path) -> None:
    product = tmp_path / "product"
    (product / "config").mkdir(parents=True)
    (product / "runtime" / "app").mkdir(parents=True)
    (product / "runtime" / "app" / "note.txt").write_text("hello")
    (product / "config" / "secrets.enc").write_bytes(b"cipher")
    (product / "config" / "secrets.key").write_bytes(b"master-key-bytes")
    neo4j = tmp_path / "neo4j.dump"
    neo4j.write_bytes(b"FAKE-NEO4J")
    host = Host(product / "host", graph_store=InMemoryGraphStore())
    output = tmp_path / "instance.n4xi"
    try:
        result = dump_instance(
            host, output, runtime_root=product, neo4j_dump=neo4j
        )
        assert result["dumped"] is True
        assert result["hashes"]["neo4j"].startswith("sha256:")
        with tarfile.open(output, "r:gz") as archive:
            names = set(archive.getnames())
        assert "config/secrets.enc" in names or any(
            name.endswith("config/secrets.enc") for name in names
        )
        assert not any(name.endswith("secrets.key") for name in names)
        assert NEO4J_MEMBER in names
        (product / "runtime" / "app" / "note.txt").write_text("changed")
        (product / "config" / "secrets.key").unlink()
        restore_instance(host, output, runtime_root=product)
        assert (product / "runtime" / "app" / "note.txt").read_text() == "hello"
        assert (product / "config" / "secrets.enc").read_bytes() == b"cipher"
        assert not (product / "config" / "secrets.key").exists()
        assert (product / "neo4j-restore" / "database.dump").read_bytes() == b"FAKE-NEO4J"
    finally:
        host.release()


def test_export_download_is_confined_to_exports(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("N4X_RUNTIME_ROOT", str(tmp_path / "product"))
    exports = tmp_path / "product" / "exports"
    exports.mkdir(parents=True)
    bundle = exports / "instance-test.n4xi"
    bundle.write_bytes(b"bundle-bytes")
    host = Host(tmp_path / "install", graph_store=InMemoryGraphStore())
    try:
        client = TestClient(host.app)
        listed = client.get("/n4x-host/exports")
        assert listed.status_code == 200
        assert listed.json()["exports"][0]["download_path"] == (
            "/n4x-host/exports/instance-test.n4xi"
        )
        downloaded = client.get("/n4x-host/exports/instance-test.n4xi")
        assert downloaded.status_code == 200
        assert downloaded.content == b"bundle-bytes"
        rejected = client.get("/n4x-host/exports/not-a-bundle.txt")
        assert rejected.status_code == 400
        remote = TestClient(host.app, client=("203.0.113.10", 50000))
        assert remote.get("/n4x-host/exports").status_code == 403
        assert remote.get("/n4x-host/exports/instance-test.n4xi").status_code == 403
        assert remote.get("/n4x-host/exports/instance-test.n4xi").json()["error"] == (
            "local_only"
        )
    finally:
        host.release()
