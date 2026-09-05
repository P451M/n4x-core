from __future__ import annotations


import pytest
from n4x.http.inspector import InspectorSettings
from n4x.system.http import create_system_http_app
from n4x.kernel.errors import SecretBackendError
from n4x.secrets.backends import InMemorySecretBackend
from n4x.testing import create_test_runtime
from tests.cypher_source import action_source
from starlette.testclient import TestClient


def test_http_runtime_exposes_surface_palette_and_theme() -> None:
    client = TestClient(create_system_http_app(create_test_runtime()))

    palette = client.get("/bridge/component-palette")
    theme = client.get("/bridge/theme")

    assert palette.status_code == 200
    assert {"Button", "Input", "Calendar"}.issubset(
        {component["name"] for component in palette.json()["components"]}
    )
    assert palette.json()["ownership"] == "experience_owned"
    assert "active authoring guide" in palette.json()["design_guidance"]
    assert "active actions" in palette.json()["design_guidance"]
    assert palette.json()["components"][0]["recommended_import"].startswith("import")
    assert theme.status_code == 200
    assert theme.json()["version"] == "n4x-ui-v8"
    assert theme.json()["active_revision_id"] == "n4x.ui-theme@n4x-ui-v8"
    assert ":root {" in theme.json()["css_text"]
    assert ".dark {" in theme.json()["css_text"]
    assert "@theme inline {" in theme.json()["css_text"]

    guide = client.get("/bridge/authoring-guide")
    assert guide.status_code == 200
    assert guide.json()["active_revision_id"] == ("n4x.authoring-guide@n4x-ui-v8")
    assert "SidebarInset" in guide.json()["content"]

    contract = client.get("/bridge/contract")
    assert contract.status_code == 200
    assert contract.json()["version"] == "n4x.experience.bridge.v1"
    assert contract.json()["hosts"] == ["browser", "mcp_app"]
    assert contract.json()["authoring"]["guide"] == "/bridge/authoring-guide"
    assert contract.json()["artifacts"]["missing_artifact_status"] == 503
    assert contract.json()["browser"]["objects"] == (
        "/api/experiences/{experience_id}/apps/{application_id}/objects"
    )
    assert contract.json()["browser"]["secret"].endswith(
        "/secrets/{secret_reference_id}"
    )
    assert contract.json()["development_browser"]["secrets"] == (
        "/api/development/{deployment_id}/apps/{application_id}/secrets"
    )
    assert contract.json()["development_browser"]["secret"].endswith(
        "/secrets/{secret_reference_id}"
    )
    assert contract.json()["browser"]["file"].endswith("/files/{token}")
    assert (
        contract.json()["file_delivery_contract_version"]
        == "n4x.file.delivery.v1"
    )
    assert contract.json()["file_delivery"]["ownership"]["path_layout"] == "Application"
    assert (
        contract.json()["access"]["secret_allowlist"]
        == "explicit_only_missing_or_empty_denies"
    )
    assert contract.json()["access"]["empty_allowlist"] == "denied"
    assert contract.json()["callback_contract_version"] == "n4x.callback.v1"
    assert contract.json()["callback"]["http"]["path"] == "/callback/{route_id}"
    assert contract.json()["graph_metamodel_version"] == "n4x.graph.metamodel.v6"


def test_http_runtime_bridge_lists_objects_and_invokes_active_action() -> None:
    system = create_test_runtime()
    app = system.create_application("notes", "Notes")
    revision = system.create_application_revision(app.id)
    note_type = system.create_object_type(revision.id, "notes.Note", name="Note")
    notebook_type = system.create_object_type(
        revision.id, "notes.Notebook", name="Notebook"
    )
    relation_type = system.create_relation_type(
        revision.id,
        "notes.note_notebook",
        name="note_notebook",
        from_object_type_id=note_type.object_type_id,
        to_object_type_id=notebook_type.object_type_id,
    )
    system.source.write_source_file(
        revision.id,
        "actions/notes.py",
        action_source(
            "def create_note(ctx, input):\n"
            "    notebook = upsert_object(ctx, 'notes.Notebook', "
            "{'title': input.get('notebook', 'Inbox')})\n"
            "    note = upsert_object(ctx, 'notes.Note', "
            "{'title': input['title']})\n"
            f"    merge_rel(ctx, '{relation_type.physical_type}', "
            "'notes.note_notebook', note['id'], notebook['id'])\n"
            "    return {'id': note['id']}\n"
        ),
        role="action",
        language="python",
    )
    system.create_action(
        revision.id,
        "notes.create",
        kind="normal",
        entrypoint="actions/notes.py:create_note",
        source_paths=["actions/notes.py"],
        input_schema={"type": "object", "required": ["title"]},
    )
    system.activate_application_revision(revision.id)

    client = TestClient(create_system_http_app(system))
    invocation = client.post(
        "/api/apps/notes/actions/notes.create/invoke",
        json={"input": {"title": "First"}},
    )
    objects = client.get("/api/apps/notes/objects?object_type_id=notes.Note")
    relations = client.get(
        "/api/apps/notes/relations?relation_type_id=notes.note_notebook"
    )

    assert invocation.status_code == 200
    assert invocation.json()["status"] == "succeeded"
    assert objects.status_code == 200
    assert objects.json()["objects"][0]["values"] == {"title": "First"}
    assert relations.status_code == 200
    assert relations.json()["relations"][0]["relation_type_id"] == "notes.note_notebook"


def test_experience_bridge_enforces_access_and_records_provenance() -> None:
    system = create_test_runtime()
    app = system.create_application("bridge-app", "Bridge App")
    app_revision = system.create_application_revision(app.id)
    system.create_object_type(app_revision.id, "bridge.Visible", name="Visible")
    system.create_object_type(app_revision.id, "bridge.Hidden", name="Hidden")
    system.source.write_source_file(
        app_revision.id,
        "actions/create.py",
        action_source(
            "def create(ctx, input):\n"
            "    visible = upsert_object(ctx, 'bridge.Visible', "
            "{'name': input['name']})\n"
            "    upsert_object(ctx, 'bridge.Hidden', {'name': 'private'})\n"
            "    return {'id': visible['id']}\n"
        ),
        role="action",
        language="python",
    )
    system.create_action(
        app_revision.id,
        "bridge.create",
        kind="normal",
        entrypoint="actions/create.py:create",
        source_paths=["actions/create.py"],
        input_schema={"type": "object", "required": ["name"]},
    )
    system.activate_application_revision(app_revision.id)

    restricted = system.create_experience("restricted-ui", "Restricted")
    restricted_revision = system.create_experience_revision(
        restricted.id,
        ui_profile="none",
        application_access=[
            {
                "application_id": app.id,
                "object_type_ids": ["bridge.Visible"],
                "relation_type_ids": [],
                "action_ids": ["bridge.create"],
            }
        ],
    )
    system.activate_experience_revision(restricted_revision.id)

    denied = system.create_experience("denied-ui", "Denied")
    denied_revision = system.create_experience_revision(
        denied.id,
        ui_profile="none",
        application_access=[
            {
                "application_id": app.id,
                "object_type_ids": [],
                "action_ids": [],
            }
        ],
    )
    system.activate_experience_revision(denied_revision.id)

    client = TestClient(create_system_http_app(system))
    invocation = client.post(
        ("/api/experiences/restricted-ui/apps/bridge-app/actions/bridge.create/invoke"),
        json={"input": {"name": "allowed"}},
    )
    visible = client.get("/api/experiences/restricted-ui/apps/bridge-app/objects")
    hidden_filter = client.get(
        (
            "/api/experiences/restricted-ui/apps/bridge-app/objects"
            "?object_type_id=bridge.Hidden"
        )
    )
    empty = client.get("/api/experiences/denied-ui/apps/bridge-app/objects")
    denied_action = client.post(
        ("/api/experiences/denied-ui/apps/bridge-app/actions/bridge.create/invoke"),
        json={"input": {"name": "denied"}},
    )

    assert invocation.status_code == 200
    assert invocation.json()["metadata"]["bridge"] == {
        "experience_id": restricted.id,
        "experience_revision_id": restricted_revision.id,
        "application_id": app.id,
        "action_id": "bridge.create",
    }
    assert "input" not in invocation.json()
    assert "stderr" not in invocation.json()
    assert [item["object_type_id"] for item in visible.json()["objects"]] == [
        "bridge.Visible"
    ]
    assert hidden_filter.status_code == 403
    assert hidden_filter.json()["code"] == "access_denied"
    assert empty.json()["objects"] == []
    assert denied_action.status_code == 403
    assert denied_action.json()["code"] == "access_denied"
    stored = system.graph.invocations[invocation.json()["id"]]
    assert stored.metadata["bridge"]["experience_id"] == restricted.id


def test_experience_secret_bridge_is_explicit_and_never_persists_values() -> None:
    backend = InMemorySecretBackend()
    system = create_test_runtime(secret_backend=backend)
    app = system.create_application("secret-bridge", "Secret Bridge")
    app_revision = system.create_application_revision(app.id)
    reference = system.secrets.create_reference(
        app.id, "secret://secret-bridge/password"
    )
    system.source.write_source_file(
        app_revision.id,
        "actions/read.py",
        (
            "def run(ctx, input):\n"
            "    value = ctx.secrets.get('secret://secret-bridge/password')\n"
            "    print(value)\n"
            "    return {'value': value, 'length': len(value)}\n"
        ),
        role="action",
        language="python",
    )
    system.create_action(
        app_revision.id,
        "secret-bridge.read",
        kind="normal",
        entrypoint="actions/read.py:run",
        source_paths=["actions/read.py"],
        secret_ref_ids=[reference.id],
    )
    system.activate_application_revision(app_revision.id)

    allowed = system.create_experience("secret-allowed", "Secret Allowed")
    allowed_revision = system.create_experience_revision(
        allowed.id,
        ui_profile="none",
        application_access=[
            {
                "application_id": app.id,
                "action_ids": ["secret-bridge.read"],
                "secret_reference_ids": [reference.id],
            }
        ],
    )
    system.activate_experience_revision(allowed_revision.id)
    denied = system.create_experience("secret-denied", "Secret Denied")
    denied_revision = system.create_experience_revision(
        denied.id,
        ui_profile="none",
        application_access=[{"application_id": app.id}],
    )
    system.activate_experience_revision(denied_revision.id)

    client = TestClient(create_system_http_app(system))
    path = f"/api/experiences/secret-allowed/apps/secret-bridge/secrets/{reference.id}"
    denied_path = (
        f"/api/experiences/secret-denied/apps/secret-bridge/secrets/{reference.id}"
    )
    first_value = "first-sentinel-secret"
    second_value = "second-sentinel-secret"
    invocations_before = set(system.graph.invocations)

    discovered = client.get(
        "/api/experiences/secret-allowed/apps/secret-bridge/secrets"
    )
    denied_discovery = client.get(
        "/api/experiences/secret-denied/apps/secret-bridge/secrets"
    )
    denied_response = client.put(denied_path, json={"value": first_value})
    invalid_response = client.put(
        path, json={"value": first_value, "uri": reference.uri}
    )
    stored = client.put(path, json={"value": first_value})
    status = client.get(path)

    assert denied_response.status_code == 403
    assert discovered.status_code == 200
    assert discovered.json() == [
        {
            "secret_reference_id": reference.id,
            "uri": reference.uri,
            "name": reference.name,
            "description": reference.description,
            "configured": False,
            "backend": reference.backend,
            "updated_at": reference.updated_at.isoformat(),
        }
    ]
    assert denied_discovery.json() == []
    assert invalid_response.status_code == 400
    assert stored.status_code == 200
    assert stored.json()["configured"] is True
    assert status.json()["configured"] is True
    assert set(stored.json()) == {
        "secret_reference_id",
        "configured",
        "backend",
        "updated_at",
    }
    assert backend.get(reference.uri) == first_value
    assert set(system.graph.invocations) == invocations_before

    invocation = client.post(
        (
            "/api/experiences/secret-allowed/apps/secret-bridge/"
            "actions/secret-bridge.read/invoke"
        ),
        json={"input": {}},
    )
    invocations_after_action = set(system.graph.invocations)
    replaced = client.put(path, json={"value": second_value})

    assert invocation.status_code == 200
    assert invocation.json()["output"] == {
        "value": "[REDACTED]",
        "length": len(first_value),
    }
    assert replaced.json()["configured"] is True
    assert backend.get(reference.uri) == second_value
    deleted = client.delete(path)
    assert deleted.json()["configured"] is False
    assert client.get(path).json()["configured"] is False
    assert set(system.graph.invocations) == invocations_after_action

    public_text = "".join(
        response.text
        for response in (
            denied_response,
            invalid_response,
            stored,
            status,
            invocation,
            replaced,
            deleted,
        )
    )
    graph_text = "".join(
        record.model_dump_json()
        for records in (
            system.graph.secret_references.values(),
            system.graph.invocations.values(),
        )
        for record in records
    )
    assert first_value not in public_text + graph_text
    assert second_value not in public_text + graph_text


def test_experience_secret_bridge_redacts_backend_failures() -> None:
    class FailingBackend(InMemorySecretBackend):
        def set(self, uri: str, value: str) -> None:
            raise SecretBackendError(f"backend rejected {uri}: {value}")

    system = create_test_runtime(secret_backend=FailingBackend())
    app = system.create_application("secret-failure", "Secret Failure")
    app_revision = system.create_application_revision(app.id)
    reference = system.secrets.create_reference(
        app.id, "secret://secret-failure/password"
    )
    system.activate_application_revision(app_revision.id)
    experience = system.create_experience("secret-failure-ui", "Secret Failure UI")
    experience_revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[
            {
                "application_id": app.id,
                "secret_reference_ids": [reference.id],
            }
        ],
    )
    system.activate_experience_revision(experience_revision.id)
    client = TestClient(create_system_http_app(system))
    sentinel = "backend-failure-sentinel"

    response = client.put(
        (
            "/api/experiences/secret-failure-ui/apps/secret-failure/"
            f"secrets/{reference.id}"
        ),
        json={"value": sentinel},
    )

    assert response.status_code == 502
    assert response.json() == {
        "code": "secret_backend_error",
        "message": "secret backend operation failed",
    }
    assert sentinel not in response.text


def test_experience_bridge_rejects_inactive_undeclared_and_invalid_inputs() -> None:
    system = create_test_runtime()
    declared = system.create_application("declared-app", "Declared")
    declared_revision = system.create_application_revision(declared.id)
    system.create_object_type(declared_revision.id, "declared.Item", name="Item")
    system.activate_application_revision(declared_revision.id)
    other = system.create_application("other-app", "Other")
    system.activate_application_revision(
        system.create_application_revision(other.id).id
    )
    inactive = system.create_experience("inactive-ui", "Inactive")
    system.create_experience_revision(
        inactive.id,
        ui_profile="none",
        application_access=[{"application_id": declared.id}],
    )
    active = system.create_experience("active-ui", "Active")
    active_revision = system.create_experience_revision(
        active.id,
        ui_profile="none",
        application_access=[{"application_id": declared.id}],
    )
    system.activate_experience_revision(active_revision.id)

    client = TestClient(create_system_http_app(system))
    inactive_response = client.get(
        "/api/experiences/inactive-ui/apps/declared-app/objects"
    )
    undeclared = client.get("/api/experiences/active-ui/apps/other-app/objects")
    wrong_definition = client.get(
        ("/api/experiences/active-ui/apps/declared-app/objects?object_type_id=missing")
    )
    secret = "never-return-this-secret"
    invalid = client.post(
        ("/api/experiences/active-ui/apps/declared-app/actions/missing/invoke"),
        json={"input": secret},
    )

    assert inactive_response.status_code == 409
    assert inactive_response.json()["code"] == "inactive_experience"
    assert undeclared.status_code == 403
    assert undeclared.json()["code"] == "undeclared_application"
    assert wrong_definition.status_code == 404
    assert wrong_definition.json()["code"] == "not_found"
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_input"
    assert secret not in invalid.text


def test_http_runtime_exposes_mcp_http_on_the_same_process() -> None:
    with TestClient(create_system_http_app(create_test_runtime())) as client:
        health = client.get("/health")
        initialize = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "n4x-test", "version": "0"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )

    assert health.status_code == 200
    payload = health.json()
    assert payload["status"] == "ok"
    assert payload["runtime"] == "n4x-system"
    assert payload["mode"] == "production"
    assert payload["mcp"] == {"transport": "http", "path": "/mcp"}
    assert "inspector" not in payload
    assert initialize.status_code == 200
    assert "N4X" in initialize.text
    assert "N4X" in initialize.text


def test_development_runtime_starts_and_stops_inspector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, object] = {}
    monkeypatch.setenv("N4X_NEO4J_PASSWORD", "do-not-leak")

    class FakeProcess:
        def __init__(self) -> None:
            self.pid = 4242
            self.stdout = None

        def poll(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        started["command"] = command
        started["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr("n4x.http.inspector.shutil.which", lambda _name: "/usr/bin/npx")
    monkeypatch.setattr("n4x.http.inspector.subprocess.Popen", fake_popen)
    monkeypatch.setattr("n4x.http.inspector._port_is_open", lambda _host, _port: True)
    monkeypatch.setattr(
        "n4x.http.inspector._stop_process_group",
        lambda _process: started.__setitem__("stopped", True),
    )

    inspector = InspectorSettings(
        mcp_url="http://127.0.0.1:7744/mcp",
        ready_timeout_seconds=1,
    )
    with TestClient(
        create_system_http_app(
            create_test_runtime(),
            mode="development",
            inspector=inspector,
        )
    ) as client:
        health = client.get("/health")

    assert health.json()["mode"] == "development"
    assert health.json()["mcp"]["path"] == "/mcp"
    assert health.json()["inspector"] == {
        "url": "http://127.0.0.1:6274",
        "mcp_url": "http://127.0.0.1:7744/mcp",
    }
    command = started["command"]
    assert isinstance(command, list)
    assert command[:4] == [
        "/usr/bin/npx",
        "--yes",
        "@modelcontextprotocol/inspector",
        "--web",
    ]
    assert command[-2] == "--catalog"
    env = started["env"]
    assert isinstance(env, dict)
    assert env["CLIENT_PORT"] == "6274"
    assert env["SERVER_PORT"] == "6277"
    assert "N4X_NEO4J_PASSWORD" not in env
    assert started["stopped"] is True


def test_development_secrets_are_get_only_and_follow_clone_binding() -> None:
    backend = InMemorySecretBackend()
    system = create_test_runtime(secret_backend=backend)
    app = system.create_application("preview-secrets", "Preview Secrets")
    revision = system.create_application_revision(app.id)
    reference = system.secrets.create_reference(
        app.id, "secret://preview-secrets/password", name="Password"
    )
    system.secrets.set_secret(reference.uri, "preview-secret-value")
    system.source.write_source_file(
        revision.id,
        "actions/read_secret.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "preview-secrets.read",
        kind="normal",
        entrypoint="actions/read_secret.py:run",
        source_paths=["actions/read_secret.py"],
        secret_ref_ids=[reference.id],
    )
    experience = system.create_experience("preview-secrets-ui", "Preview Secrets UI")
    experience_revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[
            {
                "application_id": app.id,
                "action_ids": [action.action_id],
                "secret_reference_ids": [reference.id],
            }
        ],
    )
    empty = system.create_development_deployment(
        experience_revision.id,
        {app.id: revision.id},
        initialization="empty",
    )
    cloned = system.create_development_deployment(
        experience_revision.id,
        {app.id: revision.id},
        initialization="clone",
    )
    client = TestClient(create_system_http_app(system))
    empty_list = client.get(
        f"/api/development/{empty.id}/apps/{app.id}/secrets"
    )
    cloned_list = client.get(
        f"/api/development/{cloned.id}/apps/{app.id}/secrets"
    )
    cloned_status = client.get(
        f"/api/development/{cloned.id}/apps/{app.id}/secrets/{reference.id}"
    )
    put = client.put(
        f"/api/development/{cloned.id}/apps/{app.id}/secrets/{reference.id}",
        json={"value": "rotated"},
    )
    delete = client.delete(
        f"/api/development/{cloned.id}/apps/{app.id}/secrets/{reference.id}"
    )

    assert empty_list.status_code == 200
    assert empty_list.json() == []
    assert cloned_list.status_code == 200
    assert cloned_list.json()[0]["configured"] is True
    assert cloned_list.json()[0]["uri"] == reference.uri
    assert cloned_list.json()[0]["secret_reference_id"] == reference.id
    assert "value" not in cloned_list.json()[0]
    assert cloned_status.status_code == 200
    assert cloned_status.json()["configured"] is True
    assert put.status_code == 405
    assert delete.status_code == 405
    assert backend.get(reference.uri) == "preview-secret-value"

    system.expire_development_deployment(cloned.id)
    expired = client.get(
        f"/api/development/{cloned.id}/apps/{app.id}/secrets"
    )
    expired_status = client.get(
        f"/api/development/{cloned.id}/apps/{app.id}/secrets/{reference.id}"
    )
    assert expired.status_code == 404
    assert expired_status.status_code == 404


def test_production_runtime_rejects_inspector_settings() -> None:
    with pytest.raises(ValueError, match="only started in development mode"):
        create_system_http_app(
            create_test_runtime(),
            mode="production",
            inspector=InspectorSettings(mcp_url="http://127.0.0.1:7744/mcp"),
        )
