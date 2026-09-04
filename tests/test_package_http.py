from __future__ import annotations

import hashlib

from n4x.system.http import create_system_http_app
from n4x.testing import create_test_runtime
from starlette.testclient import TestClient


def test_package_http_put_get_and_list() -> None:
    runtime = create_test_runtime()
    client = TestClient(
        create_system_http_app(runtime, public_origin="https://box.example")
    )
    payload = b"n4xp-bytes"
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()

    listed = client.get("/packages")
    assert listed.status_code == 200
    assert listed.json()["upload_url"] == "https://box.example/packages"
    assert listed.json()["archives"] == []

    created = client.put(
        "/packages/intake.n4xp",
        content=payload,
        headers={"Content-SHA256": digest},
    )
    assert created.status_code == 200
    assert created.json()["archive_name"] == "intake.n4xp"
    assert created.json()["size"] == len(payload)
    assert created.json()["content_sha256"] == digest

    listed = client.get("/packages")
    assert listed.json()["archives"][0]["archive_name"] == "intake.n4xp"
    assert listed.json()["archives"][0]["url"] == (
        "https://box.example/packages/intake.n4xp"
    )

    downloaded = client.get("/packages/intake.n4xp")
    assert downloaded.status_code == 200
    assert downloaded.content == payload


def test_package_http_rejects_hash_mismatch_existing_and_bad_name() -> None:
    runtime = create_test_runtime()
    client = TestClient(create_system_http_app(runtime))
    payload = b"one"

    mismatch = client.put(
        "/packages/intake.n4xp",
        content=payload,
        headers={"Content-SHA256": "sha256:" + "0" * 64},
    )
    assert mismatch.status_code == 400
    assert "hash mismatch" in mismatch.json()["error"]

    first = client.put("/packages/intake.n4xp", content=payload)
    assert first.status_code == 200
    conflict = client.put("/packages/intake.n4xp", content=b"two")
    assert conflict.status_code == 409
    replaced = client.put("/packages/intake.n4xp?overwrite=true", content=b"two")
    assert replaced.status_code == 200
    assert replaced.json()["size"] == 3

    bad = client.put("/packages/not-a-package.zip", content=b"pk")
    assert bad.status_code == 400
    missing = client.get("/packages/missing.n4xp")
    assert missing.status_code == 404


def test_package_http_has_no_html_picker() -> None:
    client = TestClient(create_system_http_app(create_test_runtime()))
    response = client.get("/packages", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "<html" not in response.text.lower()
