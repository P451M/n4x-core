from __future__ import annotations

import os

from starlette.testclient import TestClient

from n4x.host.identity import HOST_ADAPTER
from n4x.system.worker import create_system_app


def test_system_app_serves_health_and_info(monkeypatch) -> None:
    monkeypatch.setenv("N4X_SYSTEM_REVISION_ID", "rev-test")
    monkeypatch.setenv("N4X_SYSTEM_CONTENT_ROOT", "sha256:test")
    monkeypatch.setenv("N4X_SYSTEM_SOURCE_TREE_ID", "rev-test.source")
    monkeypatch.setenv("N4X_HOST_ABI", HOST_ADAPTER)
    with TestClient(create_system_app()) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["runtime"] == "n4x-system"
        assert health.json()["content_root"] == "sha256:test"
        assert health.json()["revision_id"] == "rev-test"
        info = client.get("/n4x/system/info")
        assert info.json()["host_abi"] == HOST_ADAPTER
        assert info.json()["content_root"] == "sha256:test"


def test_system_app_serves_experience_http_and_bridge() -> None:
    with TestClient(create_system_app()) as client:
        experiences = client.get("/experiences")
        assert experiences.status_code == 200
        assert experiences.json() == {"experiences": []}

        contract = client.get("/bridge/contract")
        assert contract.status_code == 200
        assert contract.json()["version"] == "n4x.experience.bridge.v1"
        assert contract.json()["browser"]["objects"] == (
            "/api/experiences/{experience_id}/apps/{application_id}/objects"
        )

        missing = client.get("/experience/unknown")
        assert missing.status_code == 404
