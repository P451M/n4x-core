from __future__ import annotations

import asyncio

from n4x.system.mcp import create_system_mcp
from n4x.testing import create_test_runtime


def test_inspect_revision_reports_dirty_paths_and_unbuilt_surfaces() -> None:
    system = create_test_runtime()
    experience = system.create_experience("editor-ui", "Editor UI")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    system.source.write_source_file(
        revision.id,
        "src/main.ts",
        "export {}",
        role="surface",
        language="typescript",
    )
    system.create_experience_surface(
        revision.id,
        "home",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/"},
    )
    inspected = system.inspect_experience_revision(revision.id)
    assert inspected["dirty_paths"] == ["src/main.ts"]
    assert inspected["surfaces_without_current_artifact"] == ["home"]
    assert inspected["revision"]["parent_revision_id"] is None


def test_inspect_revision_includes_application_active_revision_id() -> None:
    system = create_test_runtime()
    application = system.create_application("notes", "Notes")
    app_revision = system.create_application_revision(application.id)
    system.activate_application_revision(app_revision.id)
    experience = system.create_experience("notes-ui", "Notes UI")
    revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[{"application_id": application.id}],
    )
    inspected = system.inspect_experience_revision(revision.id)
    assert inspected["revision"]["application_access"] == [
        {
            "application_id": application.id,
            "object_type_ids": None,
            "relation_type_ids": None,
            "action_ids": None,
            "secret_reference_ids": None,
            "active_revision_id": app_revision.id,
        }
    ]


def test_development_preview_url_uses_first_browser_mount() -> None:
    asyncio.run(_assert_development_preview_url_uses_first_browser_mount())


async def _assert_development_preview_url_uses_first_browser_mount() -> None:
    system = create_test_runtime()
    application = system.create_application("mounted", "Mounted")
    app_revision = system.create_application_revision(application.id)
    experience = system.create_experience("mounted-ui", "Mounted UI")
    revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[{"application_id": application.id}],
    )
    system.source.write_source_file(
        revision.id,
        "src/main.ts",
        "export {}",
        role="surface",
        language="typescript",
    )
    system.create_experience_surface(
        revision.id,
        "main",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/main"},
    )
    server = create_system_mcp(system, public_origin="https://box.example")
    created = await server.call_tool(
        "create_development_deployment",
        {
            "experience_revision_id": revision.id,
            "application_revision_ids": {application.id: app_revision.id},
        },
    )
    path = (
        f"/development/{created.structured_content['id']}"
        f"/experience/{experience.id}/main"
    )
    assert created.structured_content["preview_url"] == f"https://box.example{path}"
    assert created.structured_content["preview_auth"] == "deployment"
    assert created.structured_content["browser_surface_urls"][0]["preview_url"] == (
        created.structured_content["preview_url"]
    )


def test_development_deployment_mcp_returns_instance_preview_urls() -> None:
    asyncio.run(_assert_development_deployment_mcp_returns_instance_preview_urls())


async def _assert_development_deployment_mcp_returns_instance_preview_urls() -> None:
    system = create_test_runtime()
    application = system.create_application("preview-data", "Preview Data")
    app_revision = system.create_application_revision(application.id)
    experience = system.create_experience("preview-ui", "Preview UI")
    revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[{"application_id": application.id}],
    )
    system.source.write_source_file(
        revision.id,
        "src/main.ts",
        "export {}",
        role="surface",
        language="typescript",
    )
    system.create_experience_surface(
        revision.id,
        "home",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/"},
    )
    server = create_system_mcp(system, public_origin="https://box.example")
    created = await server.call_tool(
        "create_development_deployment",
        {
            "experience_revision_id": revision.id,
            "application_revision_ids": {application.id: app_revision.id},
        },
    )
    path = (
        f"/development/{created.structured_content['id']}"
        f"/experience/{experience.id}"
    )
    assert created.structured_content["origin_kind"] == "public"
    assert created.structured_content["preview_auth"] == "deployment"
    assert created.structured_content["preview_url"] == f"https://box.example{path}"
    assert created.structured_content["host_bind_url"] == (
        f"http://127.0.0.1:7744{path}"
    )
    assert created.structured_content["browser_surface_urls"][0]["surface_id"] == (
        "home"
    )

    inspected = await server.call_tool(
        "inspect_experience_revision",
        {"experience_revision_id": revision.id},
    )
    assert inspected.structured_content["preview_url"] == (
        created.structured_content["preview_url"]
    )
    assert inspected.structured_content["development_deployment_id"] == (
        created.structured_content["id"]
    )

    built = await server.call_tool(
        "inspect_development_deployment",
        {"deployment_id": created.structured_content["id"]},
    )
    assert built.structured_content["preview_url"] == (
        created.structured_content["preview_url"]
    )

    system.source.write_source_file(
        revision.id,
        "src/extra.ts",
        "export {}",
        role="surface",
        language="typescript",
    )
    drifted = await server.call_tool(
        "inspect_experience_revision",
        {"experience_revision_id": revision.id},
    )
    stale = await server.call_tool(
        "inspect_development_deployment",
        {"deployment_id": created.structured_content["id"]},
    )
    assert "preview_url" not in drifted.structured_content
    assert "preview_url" not in stale.structured_content


def test_loopback_preview_auth_is_none() -> None:
    asyncio.run(_assert_loopback_preview_auth_is_none())


async def _assert_loopback_preview_auth_is_none() -> None:
    system = create_test_runtime()
    application = system.create_application("local-data", "Local Data")
    app_revision = system.create_application_revision(application.id)
    experience = system.create_experience("local-ui", "Local UI")
    revision = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[{"application_id": application.id}],
    )
    server = create_system_mcp(system)
    created = await server.call_tool(
        "create_development_deployment",
        {
            "experience_revision_id": revision.id,
            "application_revision_ids": {application.id: app_revision.id},
        },
    )
    assert created.structured_content["origin_kind"] == "host_bind"
    assert created.structured_content["preview_auth"] == "none"
