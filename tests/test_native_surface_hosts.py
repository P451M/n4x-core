from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urljoin

import pytest
from fastmcp.exceptions import NotFoundError
from starlette.testclient import TestClient

from n4x.system.http import create_system_http_app
from n4x.kernel.models import BuildArtifact
from n4x.system.mcp import create_system_mcp
from n4x.mcp.surface_apps import (
    McpAppSurfaceProvider,
    _absolute_file_delivery_urls,
    _development_surface_uri,
    _surface_tool_name,
    _surface_uri,
)
from n4x.testing import create_test_runtime


def test_mcp_app_file_delivery_urls_are_qualified_by_public_origin() -> None:
    output = {
        "file_deliveries": [
            {"url": "/api/experiences/ui/apps/app/files/token"},
        ]
    }

    assert _absolute_file_delivery_urls(output, "https://n4x.example") == {
        "file_deliveries": [
            {
                "url": (
                    "https://n4x.example/api/experiences/ui/apps/app/files/token"
                )
            }
        ]
    }


def test_browser_surfaces_select_longest_mount_and_serve_spa_with_csp(
    tmp_path: Path,
) -> None:
    system = create_test_runtime()
    experience = system.create_experience("native-browser", "Native Browser")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _source(system, revision.source_tree_id)
    root_surface = system.create_experience_surface(
        revision.id,
        "root",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={},
    )
    admin_surface = system.create_experience_surface(
        revision.id,
        "admin",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={
            "mount_path": "/admin",
            "csp": {
                "connect_domains": ["https://api.example.com"],
                "resource_domains": ["https://cdn.example.com"],
            },
        },
    )
    _activate_with_artifacts(
        system,
        experience,
        revision,
        [
            (root_surface, _browser_artifact(tmp_path / "root", "root")),
            (admin_surface, _browser_artifact(tmp_path / "admin", "admin")),
        ],
    )

    client = TestClient(create_system_http_app(system))
    listing = client.get("/experiences")
    root_url = "/experience/native-browser/deep/nested/link"
    admin_url = "/experience/native-browser/admin/deep/nested/link"
    root = client.get(root_url)
    admin = client.get(admin_url)
    asset = client.get("/experience/native-browser/admin/assets/app.js")

    assert [item["surface_id"] for item in listing.json()["experiences"][0]["surfaces"]] == [
        "admin",
        "root",
    ]
    assert '<base href="/experience/native-browser/">' in root.text
    assert '<base href="/experience/native-browser/admin/">' in admin.text
    assert root.status_code == 200
    assert urljoin(
        f"http://testserver{root_url}",
        "/experience/native-browser/assets/app.js",
    ).endswith("/experience/native-browser/assets/app.js")
    assert client.get("/experience/native-browser/assets/app.js").status_code == 200
    assert client.get("/experience/native-browser/admin/assets/app.css").status_code == 200
    assert client.get("/experience/native-browser/missing.js").status_code == 404
    assert asset.text == "window.surface = 'admin'"
    assert "https://api.example.com" in admin.headers["content-security-policy"]
    assert "https://cdn.example.com" in admin.headers["content-security-policy"]


def test_development_deployment_serves_draft_surface_with_runtime_context(
    tmp_path: Path,
) -> None:
    system = create_test_runtime()
    application = system.create_application("preview-data", "Preview Data")
    app_revision = system.create_application_revision(application.id)
    experience = system.create_experience("preview-ui", "Preview UI")
    revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[{"application_id": application.id}],
    )
    _source(system, revision.source_tree_id)
    surface = system.create_experience_surface(
        revision.id,
        "browser",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/"},
    )
    artifact = _browser_artifact(tmp_path / "preview", "preview")
    system.surfaces.build_input_hash = lambda _surface: "current"
    with system.uow:
        system.graph.build_artifacts.save(
            artifact.model_copy(
                update={
                    "owner_id": revision.id,
                    "surface_id": surface.surface_id,
                }
            )
        )
    deployment = system.create_development_deployment(
        revision.id,
        {application.id: app_revision.id},
    )

    with TestClient(create_system_http_app(system)) as client:
        response = client.get(
            f"/development/{deployment.id}/experience/{experience.id}"
        )

    assert response.status_code == 200
    assert (
        f'<base href="/development/{deployment.id}/experience/'
        f'{experience.id}/">'
    ) in response.text
    assert "window.__N4X_EXECUTION_CONTEXT__" in response.text
    assert f'"deployment_id":"{deployment.id}"' in response.text
    assert f'"api_base":"/api/development/{deployment.id}"' in response.text


def test_mcp_surface_provider_uses_prebuilt_html_and_revision_qualified_uri(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_mcp_surface_provider(tmp_path))


def test_development_mcp_surface_is_deployment_qualified(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_development_mcp_surface(tmp_path))


async def _assert_development_mcp_surface(tmp_path: Path) -> None:
    system = create_test_runtime()
    application = system.create_application("dev-mcp-data", "Dev MCP Data")
    app_revision = system.create_application_revision(application.id)
    experience = system.create_experience("dev-mcp", "Dev MCP")
    revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[{"application_id": application.id}],
    )
    _source(system, revision.source_tree_id)
    surface = system.create_experience_surface(
        revision.id,
        "assistant",
        surface_type="mcp_app",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"related_browser_path": "/details"},
    )
    html_path = tmp_path / "dev-mcp.html"
    html_path.write_text(
        "<html><head></head><body>development-mcp</body></html>",
        encoding="utf-8",
    )
    system.surfaces.build_input_hash = lambda _surface: "current"
    with system.uow:
        system.graph.build_artifacts.save(
            BuildArtifact(
                id="dev-mcp-artifact",
                owner_kind="ExperienceRevision",
                owner_id=revision.id,
                build_invocation_id="build",
                artifact_type="surface_bundle",
                path=str(tmp_path),
                content_hash="dev-mcp",
                surface_id=surface.surface_id,
                surface_type="mcp_app",
                input_hash="current",
                manifest={"html": str(html_path)},
            )
        )
    deployment = system.create_development_deployment(
        revision.id,
        {application.id: app_revision.id},
    )
    uri = _development_surface_uri(
        deployment.id,
        experience.id,
        revision.id,
        surface.surface_id,
    )
    server = create_system_mcp(
        system, public_origin="http://127.0.0.1:7744"
    )

    resources = await server.list_resources()
    contents = await server.read_resource(uri)
    tools = await server.list_tools()
    open_tool = next(
        tool
        for tool in tools
        if (tool.meta or {}).get("openai/outputTemplate") == uri
    )
    opened = await server.call_tool(open_tool.name, {})

    assert uri in {str(resource.uri) for resource in resources}
    assert "__N4X_EXECUTION_CONTEXT__" in contents.contents[0].content
    assert deployment.id in contents.contents[0].content
    assert opened.structured_content["deployment_id"] == deployment.id
    assert (
        f"/development/{deployment.id}/experience/{experience.id}/details"
        in opened.structured_content["related_browser_url"]
    )
    assert opened.structured_content["origin_kind"] == "host_bind"

    public = create_system_mcp(system, public_origin="https://box.example")
    public_opened = await public.call_tool(open_tool.name, {})
    assert public_opened.structured_content["origin_kind"] == "public"
    assert public_opened.structured_content["related_browser_url"].startswith(
        f"https://box.example/development/{deployment.id}/"
    )
    assert public_opened.structured_content["host_bind_url"].startswith(
        "http://127.0.0.1:7744/development/"
    )


async def _assert_mcp_surface_provider(tmp_path: Path) -> None:
    system = create_test_runtime()
    application = system.create_application("native-data", "Native Data")
    app_revision = system.create_application_revision(application.id)
    system.create_object_type(app_revision.id, "native.Item", name="Item")
    system.source.write_source_file(
        app_revision.source_tree_id,
        "actions/run.py",
        "def run(ctx, input):\n    return {'echo': input['value']}\n",
        role="action",
        language="python",
    )
    system.create_action(
        app_revision.id,
        "native.echo",
        kind="normal",
        entrypoint="actions/run.py:run",
        source_paths=["actions/run.py"],
        input_schema={"type": "object", "required": ["value"]},
    )
    system.activate_application_revision(app_revision.id)
    experience = system.create_experience("native-mcp", "Native MCP")
    revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[
            {
                "application_id": application.id,
                "object_type_ids": ["native.Item"],
                "action_ids": ["native.echo"],
            }
        ],
    )
    _source(system, revision.source_tree_id)
    browser = system.create_experience_surface(
        revision.id,
        "browser",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/"},
    )
    surface = system.create_experience_surface(
        revision.id,
        "assistant",
        surface_type="mcp_app",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        description="Native assistant",
        config={
            "related_browser_path": "/details",
            "csp": {"connect_domains": ["https://api.example.com"]},
        },
    )
    html = "<html><body>prebuilt-native-html</body></html>"
    mcp_root = tmp_path / "mcp"
    mcp_root.mkdir()
    html_path = mcp_root / "mcp-app.html"
    html_path.write_text(html, encoding="utf-8")
    _activate_with_artifacts(
        system,
        experience,
        revision,
        [
            (browser, _browser_artifact(tmp_path / "browser", "browser")),
            (
                surface,
                BuildArtifact(
                    id="mcp-artifact",
                    owner_kind="ExperienceRevision",
                    owner_id=revision.id,
                    build_invocation_id="build",
                    artifact_type="surface_bundle",
                    path=str(mcp_root),
                    content_hash="mcp",
                    surface_id=surface.surface_id,
                    surface_type="mcp_app",
                    input_hash="current",
                    manifest={"html": str(html_path)},
                ),
            ),
        ],
    )
    server = create_system_mcp(system, public_origin="http://127.0.0.1:7744")
    uri = _surface_uri(experience.id, revision.id, surface.surface_id)

    resources = await server.list_resources()
    contents = await server.read_resource(uri)
    opened = await server.call_tool(
        _surface_tool_name("open", experience.id, surface.surface_id), {}
    )
    model_tools = await server.list_tools()
    provider = McpAppSurfaceProvider(
        system, public_origin="http://127.0.0.1:7744"
    )
    tools = await provider.list_tools()
    list_local_name = _surface_tool_name(
        "list_objects", experience.id, surface.surface_id, application.id
    )
    invoke_local_name = _surface_tool_name(
        "invoke", experience.id, surface.surface_id, application.id
    )
    bridge_tool = next(tool for tool in tools if tool.name.endswith(list_local_name))
    invoke_tool = next(tool for tool in tools if tool.name.endswith(invoke_local_name))
    listed = await bridge_tool.run({})
    invoked = await invoke_tool.run(
        {"action_id": "native.echo", "input": {"value": "ok"}}
    )

    assert [str(resource.uri) for resource in resources] == [uri]
    assert contents.contents[0].content == html
    assert contents.contents[0].meta["ui"]["csp"]["connectDomains"] == [
        "http://127.0.0.1:7744",
        "https://api.example.com",
    ]
    assert contents.contents[0].meta["csp"] == contents.contents[0].meta["ui"]["csp"]
    assert opened.structured_content["related_browser_url"].endswith(
        "/experience/native-mcp/details"
    )
    assert opened.structured_content["origin_kind"] == "host_bind"
    assert opened.structured_content["host_bind_url"] == (
        opened.structured_content["related_browser_url"]
    )
    assert bridge_tool.meta["ui"]["visibility"] == ["app"]
    assert not any(tool.name == list_local_name for tool in model_tools)
    assert listed.structured_content["result"] == []
    assert invoked.structured_content["output"] == {"echo": "ok"}
    assert invoked.structured_content["metadata"]["bridge"]["experience_id"] == experience.id
    with pytest.raises(NotFoundError):
        await server.read_resource(
            _surface_uri(experience.id, "stale-revision", surface.surface_id)
        )


def test_activation_emits_surface_catalog_hook() -> None:
    system = create_test_runtime()
    seen: list[tuple[str, str]] = []
    system.surface_catalog_notifier.subscribe(
        lambda experience, revision: seen.append((experience, revision))
    )
    experience = system.create_experience("notify-surface", "Notify")
    revision = system.create_experience_revision(experience.id, ui_profile="none")

    system.activate_experience_revision(revision.id)

    assert seen == [(experience.id, revision.id)]


def test_pwa_manifest_is_bound_to_live_document_base(tmp_path: Path) -> None:
    system = create_test_runtime()
    experience = system.create_experience("pwa-ui", "PWA UI")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _source(system, revision.source_tree_id)
    surface = system.create_experience_surface(
        revision.id,
        "browser",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"pwa": {}},
    )
    artifact = _browser_artifact(tmp_path / "pwa", "pwa")
    (tmp_path / "pwa" / "manifest.webmanifest").write_text(
        json.dumps(
            {
                "name": "PWA UI",
                "display": "standalone",
                "start_url": "/",
                "custom_member": {"keep": True},
            }
        ),
        encoding="utf-8",
    )
    _activate_with_artifacts(system, experience, revision, [(surface, artifact)])
    client = TestClient(create_system_http_app(system))

    html = client.get("/experience/pwa-ui/inbox")
    manifest = client.get("/experience/pwa-ui/manifest.webmanifest")
    missing = client.get("/experience/pwa-ui/missing.js")

    assert html.status_code == 200
    assert 'rel="manifest"' not in html.text
    assert html.headers["content-type"].startswith("text/html")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    payload = manifest.json()
    assert payload["name"] == "PWA UI"
    assert payload["display"] == "standalone"
    assert payload["custom_member"] == {"keep": True}
    assert payload["start_url"] == "/experience/pwa-ui/"
    assert payload["scope"] == "/experience/pwa-ui/"
    assert payload["id"] == "/experience/pwa-ui/"
    assert missing.status_code == 404


def test_pwa_manifest_resolves_relative_start_url_and_rejects_escape(
    tmp_path: Path,
) -> None:
    system = create_test_runtime()
    experience = system.create_experience("pwa-mail", "PWA Mail")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _source(system, revision.source_tree_id)
    surface = system.create_experience_surface(
        revision.id,
        "browser",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"pwa": {}},
    )
    artifact = _browser_artifact(tmp_path / "mail", "mail")
    (tmp_path / "mail" / "manifest.webmanifest").write_text(
        json.dumps({"start_url": "./mail", "scope": "./", "id": "./"}),
        encoding="utf-8",
    )
    _activate_with_artifacts(system, experience, revision, [(surface, artifact)])
    client = TestClient(create_system_http_app(system))

    payload = client.get("/experience/pwa-mail/manifest.webmanifest").json()
    assert payload["start_url"] == "/experience/pwa-mail/mail"
    assert payload["scope"] == "/experience/pwa-mail/"
    assert payload["id"] == "/experience/pwa-mail/"

    (tmp_path / "mail" / "manifest.webmanifest").write_text(
        json.dumps({"start_url": "/other"}),
        encoding="utf-8",
    )
    escaped = client.get("/experience/pwa-mail/manifest.webmanifest")
    assert escaped.status_code == 400
    assert "escapes" in escaped.json()["error"]


def test_development_pwa_manifest_uses_deployment_document_base(
    tmp_path: Path,
) -> None:
    system = create_test_runtime()
    application = system.create_application("pwa-data", "PWA Data")
    app_revision = system.create_application_revision(application.id)
    experience = system.create_experience("pwa-preview", "PWA Preview")
    revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[{"application_id": application.id}],
    )
    _source(system, revision.source_tree_id)
    surface = system.create_experience_surface(
        revision.id,
        "browser",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"pwa": {}},
    )
    artifact = _browser_artifact(tmp_path / "preview-pwa", "preview-pwa")
    (tmp_path / "preview-pwa" / "manifest.webmanifest").write_text(
        json.dumps({"start_url": "/"}),
        encoding="utf-8",
    )
    system.surfaces.build_input_hash = lambda _surface: "current"
    with system.uow:
        system.graph.build_artifacts.save(
            artifact.model_copy(
                update={
                    "owner_id": revision.id,
                    "surface_id": surface.surface_id,
                }
            )
        )
    deployment = system.create_development_deployment(
        revision.id,
        {application.id: app_revision.id},
    )

    with TestClient(create_system_http_app(system)) as client:
        response = client.get(
            f"/development/{deployment.id}/experience/{experience.id}"
            "/manifest.webmanifest"
        )

    assert response.status_code == 200
    document_base = (
        f"/development/{deployment.id}/experience/{experience.id}/"
    )
    assert response.json()["start_url"] == document_base
    assert response.json()["scope"] == document_base
    assert response.json()["id"] == document_base


def _source(system, tree_id: str) -> None:
    system.source.write_source_file(
        tree_id,
        "src/main.ts",
        "ready",
        role="surface",
        language="typescript",
    )


def _browser_artifact(root: Path, label: str) -> BuildArtifact:
    (root / "assets").mkdir(parents=True)
    index = root / "index.html"
    index.write_text(
        (
            "<html><head>"
            '<link rel="stylesheet" href="./assets/app.css">'
            "</head><body>"
            f"<main>{label}</main>"
            '<script type="module" src="./assets/app.js"></script>'
            "</body></html>"
        ),
        encoding="utf-8",
    )
    (root / "assets" / "app.js").write_text(
        f"window.surface = '{label}'", encoding="utf-8"
    )
    (root / "assets" / "app.css").write_text(
        f".surface::after {{ content: '{label}' }}", encoding="utf-8"
    )
    return BuildArtifact(
        id=f"{label}-artifact",
        owner_kind="ExperienceRevision",
        owner_id="placeholder",
        build_invocation_id="build",
        artifact_type="surface_bundle",
        path=str(root),
        content_hash=label,
        surface_id=label,
        surface_type="browser",
        input_hash="current",
        manifest={"root": str(root), "index": str(index)},
    )


def _activate_with_artifacts(system, experience, revision, entries) -> None:
    system.surfaces.build_input_hash = lambda _surface: "current"
    with system.uow:
        system.graph.experience_revisions.save(
            revision.model_copy(update={"status": "active"})
        )
        system.graph.experiences.save(
            experience.model_copy(
                update={"status": "active", "active_revision_id": revision.id}
            )
        )
        for surface, artifact in entries:
            system.graph.build_artifacts.save(
                artifact.model_copy(
                    update={
                        "owner_id": revision.id,
                        "surface_id": surface.surface_id,
                        "surface_type": surface.surface_type,
                    }
                )
            )
