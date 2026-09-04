from __future__ import annotations

from pathlib import Path

import pytest
from n4x.contracts.file_delivery import FILE_DELIVERY_SIGNING_KEY_ENV
from n4x.system.file_delivery import FileDeliveryService
from n4x.system.http import create_system_http_app
from n4x.kernel.paths import PathContainmentError, resolve_path_within
from n4x.system.runtime import SystemRuntime
from n4x.runtime.actions import RuntimePaths
from n4x.testing import create_test_runtime
from starlette.testclient import TestClient


def test_blank_signing_key_env_uses_the_persisted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = RuntimePaths(tmp_path / "runtime")
    first = FileDeliveryService(paths)
    monkeypatch.setenv(FILE_DELIVERY_SIGNING_KEY_ENV, "")
    second = FileDeliveryService(paths)
    assert second.signing_key == first.signing_key


def _author_delivery_app(
    system: SystemRuntime,
    *,
    application_id: str = "generic-files",
    experience_id: str = "generic-files-ui",
) -> tuple[str, str]:
    app = system.create_application(application_id, "Generic Files")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/files.py",
        (
            "import os\n"
            "from pathlib import Path\n\n"
            "def write(ctx, input):\n"
            "    root = Path(os.environ['N4X_APPLICATION_DATA_ROOT'])\n"
            "    relative = input.get('path', 'content/payload.txt')\n"
            "    target = root / relative\n"
            "    target.parent.mkdir(parents=True, exist_ok=True)\n"
            "    target.write_bytes(input['content'].encode('utf-8'))\n"
            "    return {\n"
            "        'stored_bytes': target.stat().st_size,\n"
            "        'signing_key_visible': "
            "'N4X_FILE_DELIVERY_SIGNING_KEY' in os.environ,\n"
            "        '_n4x': {'file_deliveries': [{\n"
            "            'path': relative,\n"
            "            'content_type': input.get('content_type', 'text/plain'),\n"
            "            'disposition': input.get('disposition', 'inline'),\n"
            "            'filename': input.get('filename', 'payload.txt'),\n"
            "            'expires_in_seconds': input.get('expires_in_seconds', 300),\n"
            "        }]},\n"
            "    }\n\n"
            "def read(ctx, input):\n"
            "    root = Path(os.environ['N4X_APPLICATION_DATA_ROOT'])\n"
            "    target = root / input['path']\n"
            "    return {'content': target.read_text(encoding='utf-8')}\n"
        ),
        role="action",
        language="python",
    )
    for action_id, function_name in (
        (f"{application_id}.write", "write"),
        (f"{application_id}.read", "read"),
    ):
        system.create_action(
            revision.id,
            action_id,
            kind="normal",
            entrypoint=f"actions/files.py:{function_name}",
            source_paths=["actions/files.py"],
        )
    system.activate_application_revision(revision.id)

    experience = system.create_experience(experience_id, "Generic Files UI")
    experience_revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[
            {
                "application_id": app.id,
                "action_ids": [
                    f"{application_id}.write",
                    f"{application_id}.read",
                ],
            }
        ],
    )
    system.activate_experience_revision(experience_revision.id)
    return app.id, experience.id


def test_application_data_mount_is_opaque_stable_and_revision_independent(
    tmp_path: Path,
) -> None:
    paths = RuntimePaths(tmp_path / "runtime")
    system = create_test_runtime(runtime_paths=paths)
    application_id, _ = _author_delivery_app(system)

    written = system.run_active_action(
        application_id,
        f"{application_id}.write",
        {"content": "persistent", "path": "content/stable.txt"},
    )
    data_root = paths.application_data_root(application_id)

    assert written.status == "succeeded", written.error
    assert written.output["signing_key_visible"] is False
    assert data_root.parent == paths.root / "application-data"
    assert data_root.name != application_id
    assert paths.application_data_root("other-application") != data_root
    assert (data_root / "content/stable.txt").read_text() == "persistent"
    assert paths.application_data_root(application_id) == data_root

    next_revision = system.create_application_revision(application_id)
    system.activate_application_revision(next_revision.id)
    read = system.run_active_action(
        application_id,
        f"{application_id}.read",
        {"path": "content/stable.txt"},
    )

    assert read.status == "succeeded", read.error
    assert read.output == {"content": "persistent"}


def test_development_file_delivery_is_bound_to_deployment_volume(
    tmp_path: Path,
) -> None:
    paths = RuntimePaths(tmp_path / "runtime")
    system = create_test_runtime(runtime_paths=paths)
    app = system.create_application("dev-files", "Dev Files")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/write.py",
        (
            "import os\n"
            "from pathlib import Path\n\n"
            "def run(ctx, input):\n"
            "    root = Path(os.environ['N4X_APPLICATION_DATA_ROOT'])\n"
            "    target = root / 'preview.txt'\n"
            "    target.write_text(input['content'], encoding='utf-8')\n"
            "    return {'_n4x': {'file_deliveries': [{\n"
            "        'path': 'preview.txt',\n"
            "        'content_type': 'text/plain',\n"
            "        'disposition': 'inline',\n"
            "    }]}}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "dev-files.write",
        kind="normal",
        entrypoint="actions/write.py:run",
        source_paths=["actions/write.py"],
    )
    experience = system.create_experience("dev-files-ui", "Dev Files UI")
    experience_revision = system.create_experience_revision(
        experience.id,
        application_access=[
            {
                "application_id": app.id,
                "action_ids": [action.action_id],
            }
        ],
    )
    deployment = system.create_development_deployment(
        experience_revision.id,
        {app.id: revision.id},
    )

    with TestClient(create_system_http_app(system)) as client:
        invocation = client.post(
            (
                f"/api/development/{deployment.id}/apps/{app.id}/"
                f"actions/{action.action_id}/invoke"
            ),
            json={"input": {"content": "preview-only"}},
        )
        delivery_url = invocation.json()["output"][
            "file_deliveries"
        ][0]["url"]
        delivered = client.get(delivery_url)
        system.expire_development_deployment(deployment.id)
        expired = client.get(delivery_url)

    assert invocation.status_code == 200
    assert delivery_url.startswith(
        f"/api/development/{deployment.id}/apps/{app.id}/files/"
    )
    assert delivered.text == "preview-only"
    assert expired.status_code == 404
    assert not (
        paths.application_data_root(app.id) / "preview.txt"
    ).exists()


def test_path_confinement_rejects_traversal_and_escaping_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (root / "escape").symlink_to(outside)

    with pytest.raises(PathContainmentError):
        resolve_path_within(root, "../outside.txt")
    with pytest.raises(PathContainmentError):
        resolve_path_within(root, str(outside))
    with pytest.raises(PathContainmentError):
        resolve_path_within(root, "escape")


def test_experience_file_delivery_streams_ranges_and_survives_restart(
    tmp_path: Path,
) -> None:
    paths = RuntimePaths(tmp_path / "runtime")
    system = create_test_runtime(runtime_paths=paths)
    application_id, experience_id = _author_delivery_app(system)
    client = TestClient(create_system_http_app(system))
    content = "0123456789" * 100

    invocation = client.post(
        (
            f"/api/experiences/{experience_id}/apps/{application_id}/actions/"
            f"{application_id}.write/invoke"
        ),
        json={
            "input": {
                "content": content,
                "path": "content/ranged.txt",
                "filename": "ranged.txt",
                "disposition": "attachment",
            }
        },
    )

    assert invocation.status_code == 200
    assert invocation.json()["output"]["stored_bytes"] == len(content)
    assert invocation.json()["output"]["signing_key_visible"] is False
    assert "_n4x" not in invocation.text
    assert "content/ranged.txt" not in invocation.text
    delivery = invocation.json()["output"]["file_deliveries"][0]
    assert delivery["url"].startswith(
        f"/api/experiences/{experience_id}/apps/{application_id}/files/"
    )
    assert delivery["filename"] == "ranged.txt"

    response = client.get(delivery["url"])
    head = client.head(delivery["url"])
    partial = client.get(delivery["url"], headers={"Range": "bytes=10-19"})

    assert response.status_code == 200
    assert response.text == content
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert head.status_code == 200
    assert head.content == b""
    assert partial.status_code == 206
    assert partial.content == content.encode("utf-8")[10:20]
    assert partial.headers["content-range"] == f"bytes 10-19/{len(content)}"

    restarted = SystemRuntime(system.store, runtime_paths=paths)
    restarted_response = TestClient(create_system_http_app(restarted)).get(delivery["url"])

    assert restarted_response.status_code == 200
    assert restarted_response.text == content
    stored = restarted.graph.invocations[invocation.json()["id"]]
    assert stored.output == invocation.json()["output"]


def test_file_delivery_rejects_tampering_scope_changes_and_missing_files(
    tmp_path: Path,
) -> None:
    paths = RuntimePaths(tmp_path / "runtime")
    system = create_test_runtime(runtime_paths=paths)
    application_id, experience_id = _author_delivery_app(system)
    other = system.create_experience("other-files-ui", "Other Files UI")
    other_revision = system.create_experience_revision(
        other.id,
        ui_profile="none",
        application_access=[{"application_id": application_id}],
    )
    system.activate_experience_revision(other_revision.id)
    client = TestClient(create_system_http_app(system))
    invocation = client.post(
        (
            f"/api/experiences/{experience_id}/apps/{application_id}/actions/"
            f"{application_id}.write/invoke"
        ),
        json={"input": {"content": "temporary", "path": "content/temporary.txt"}},
    )
    url = invocation.json()["output"]["file_deliveries"][0]["url"]
    token = url.rsplit("/", 1)[1]
    changed = ("A" if token[0] != "A" else "B") + token[1:]
    tampered = url.rsplit("/", 1)[0] + f"/{changed}"
    wrong_experience = url.replace(
        f"/experiences/{experience_id}/", f"/experiences/{other.id}/"
    )

    assert client.get(tampered).status_code == 404
    assert client.get(wrong_experience).status_code == 403

    (paths.application_data_root(application_id) / "content/temporary.txt").unlink()
    missing = client.get(url)
    assert missing.status_code == 404
    assert missing.json()["code"] == "file_delivery_not_found"

    (paths.application_data_root(application_id) / "content/temporary.txt").write_text(
        "temporary"
    )
    next_revision = system.create_experience_revision(
        experience_id,
        ui_profile="none",
        application_access=[{"application_id": application_id}],
    )
    system.activate_experience_revision(next_revision.id)
    stale_revision = client.get(url)
    assert stale_revision.status_code == 403
    assert stale_revision.json()["code"] == "file_delivery_scope_mismatch"


def test_file_delivery_expires_and_rejects_invalid_action_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import n4x.system.file_delivery as delivery_module

    paths = RuntimePaths(tmp_path / "runtime")
    system = create_test_runtime(runtime_paths=paths)
    application_id, experience_id = _author_delivery_app(system)
    client = TestClient(create_system_http_app(system))
    now = 1_800_000_000
    monkeypatch.setattr(delivery_module.time, "time", lambda: now)

    invocation = client.post(
        (
            f"/api/experiences/{experience_id}/apps/{application_id}/actions/"
            f"{application_id}.write/invoke"
        ),
        json={
            "input": {
                "content": "expires",
                "path": "content/expires.txt",
                "expires_in_seconds": 1,
            }
        },
    )
    url = invocation.json()["output"]["file_deliveries"][0]["url"]
    monkeypatch.setattr(delivery_module.time, "time", lambda: now + 2)

    expired = client.get(url)
    assert expired.status_code == 410
    assert expired.json()["code"] == "file_delivery_expired"

    invocation_ids = set(system.graph.invocations)
    invalid = client.post(
        (
            f"/api/experiences/{experience_id}/apps/{application_id}/actions/"
            f"{application_id}.write/invoke"
        ),
        json={
            "input": {
                "content": "invalid",
                "path": "../outside.txt",
            }
        },
    )
    assert invalid.status_code == 502
    assert invalid.json()["code"] == "invalid_file_delivery"
    invalid_id = (set(system.graph.invocations) - invocation_ids).pop()
    stored = system.graph.invocations[invalid_id]
    assert stored.metadata["bridge"]["experience_id"] == experience_id
    assert "_n4x" not in stored.output
    assert "file_deliveries" not in stored.output
