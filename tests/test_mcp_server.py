import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from n4x.graph.neo4j import Neo4jConfig
from n4x.main import app
from n4x.mcp.client_guide import CLIENT_GUIDE_TOPICS
from n4x.system.mcp import create_system_mcp
from n4x.testing import create_test_runtime
from typer.testing import CliRunner

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _plain_cli_text(text: str) -> str:
    return " ".join(_ANSI_ESCAPE.sub("", text).split())


def test_mcp_server_can_be_created() -> None:
    server = create_system_mcp(create_test_runtime())
    assert server.name == "N4X"
    assert "inspect_client_guide" in (server.instructions or "")
    assert "inspect_experience_design_context" in (server.instructions or "")
    assert "guidance is advisory" in (server.instructions or "")


def test_cursor_experience_skill_is_not_a_second_path() -> None:
    skill = (
        Path(__file__).resolve().parents[1]
        / ".cursor"
        / "skills"
        / "n4x-experience-authoring"
    )
    assert not skill.exists()


def test_mcp_client_guide_is_the_authoring_playbook() -> None:
    asyncio.run(_assert_mcp_client_guide_is_the_authoring_playbook())


def test_mcp_authoring_tools_are_registered_and_callable() -> None:
    asyncio.run(_assert_mcp_authoring_tools_are_registered_and_callable())


def test_mcp_list_packages_reports_upload_url() -> None:
    asyncio.run(_assert_mcp_list_packages_reports_upload_url())


def test_mcp_can_create_and_run_application_tests() -> None:
    asyncio.run(_assert_mcp_can_create_and_run_application_tests())


def test_mcp_can_run_active_action() -> None:
    asyncio.run(_assert_mcp_can_run_active_action())


def test_mcp_can_author_canonical_experience() -> None:
    asyncio.run(_assert_mcp_can_author_canonical_experience())


def test_mcp_secret_inspection_requires_application_scope() -> None:
    asyncio.run(_assert_mcp_secret_inspection_requires_application_scope())


def test_mcp_reset_requires_exact_database_confirmation() -> None:
    asyncio.run(_assert_mcp_reset_requires_exact_database_confirmation())


def test_production_cli_rejects_missing_neo4j_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "N4X_NEO4J_URI",
        "N4X_NEO4J_USER",
        "N4X_NEO4J_PASSWORD",
        "N4X_NEO4J_DATABASE",
    ):
        monkeypatch.delenv(name, raising=False)

    result = CliRunner().invoke(app, ["neo4j-health"])

    assert result.exit_code != 0
    assert "missing required Neo4j configuration" in result.output
    assert "N4X_NEO4J_PASSWORD" in result.output


def test_serve_cli_defaults_to_development_and_documents_production() -> None:
    result = CliRunner().invoke(app, ["serve", "--help"])
    help_text = _plain_cli_text(result.output)

    assert result.exit_code == 0
    assert "in development mode MCP Inspector" in help_text
    assert "--mode" in help_text
    assert "production starts" in help_text
    assert "MCP HTTP without" in help_text
    assert "[default:" in help_text
    assert "development]" in help_text


async def _assert_mcp_client_guide_is_the_authoring_playbook() -> None:
    server = create_system_mcp(create_test_runtime())
    catalog = await server.call_tool("inspect_client_guide", {})
    topics = [item["topic"] for item in catalog.structured_content["topics"]]
    assert list(CLIENT_GUIDE_TOPICS) == topics
    for item in catalog.structured_content["topics"]:
        assert "content" not in item
        assert item["description"]
        assert item["when_to_use"]

    experience = await server.call_tool(
        "inspect_client_guide", {"topic": "experience"}
    )
    content = experience.structured_content["content"]
    assert "inspect_experience_design_context" in content
    assert "Graph source is the source of truth" in content
    assert "Capture and inspect screenshots" in content
    assert "--primary:" not in content
    assert "github.com/jnsahaj/tweakcn" not in content
    assert len(content.splitlines()) < 150

    application = await server.call_tool(
        "inspect_client_guide", {"topic": "application"}
    )
    app_content = application.structured_content["content"]
    assert "create_object_type" in app_content
    assert "create_action" in app_content
    assert "validate_application_revision" in app_content
    assert "activate_application_revision" in app_content

    created = await server.call_tool(
        "create_application",
        {"application_id": "guide-app", "name": "Guide App"},
    )
    assert created.structured_content["authoring_next_step"]["tool"] == (
        "inspect_client_guide"
    )
    assert created.structured_content["authoring_next_step"]["arguments"] == {
        "topic": "application"
    }
    revision = await server.call_tool(
        "create_application_revision",
        {"application_id": "guide-app"},
    )
    assert revision.structured_content["authoring_next_step"]["arguments"] == {
        "topic": "application"
    }

    tools = {tool.name: tool for tool in await server.list_tools()}
    assert tools["inspect_client_guide"].annotations.readOnlyHint is True
    assert tools["inspect_client_guide"].annotations.openWorldHint is False
    assert tools["reset_dev_graph"].annotations.readOnlyHint is False
    assert tools["reset_dev_graph"].annotations.destructiveHint is True
    assert tools["delete_working_set"].annotations.destructiveHint is True


async def _assert_mcp_list_packages_reports_upload_url() -> None:
    runtime = create_test_runtime()
    runtime.stage_package("intake.n4xp", b"n4xp-bytes")
    server = create_system_mcp(
        runtime, public_origin="https://box.example"
    )
    listed = await server.call_tool("list_packages", {})
    content = listed.structured_content
    assert content["upload_url"] == "https://box.example/packages"
    assert content["archives"][0]["archive_name"] == "intake.n4xp"
    assert content["archives"][0]["url"] == (
        "https://box.example/packages/intake.n4xp"
    )
    tools = {tool.name for tool in await server.list_tools()}
    assert "list_packages" in tools
    assert "stage_package" not in tools


async def _assert_mcp_authoring_tools_are_registered_and_callable() -> None:
    server = create_system_mcp(create_test_runtime())
    tools = await server.list_tools()
    tool_names = {tool.name for tool in tools}
    platform_release_tools = {
        name
        for name in tool_names
        if "surface_theme" in name or "authoring_guide" in name
    }

    assert {
        "inspect_official_release",
        "inspect_client_guide",
        "import_official_system",
        "enable_system_revision",
        "dump_instance",
        "inspect_instance_dumps",
        "create_application",
        "create_application_revision",
        "create_experience",
        "retire_experience",
        "create_experience_revision",
        "discard_application_revision",
        "discard_experience_revision",
        "set_experience_application_access",
        "inspect_experience_revision",
        "write_source_file",
        "apply_source_patch",
        "search_source_tree",
        "create_object_type",
        "create_relation_type",
        "create_runtime_dependency",
        "create_experience_runtime_dependency",
        "inspect_authoring_guide",
        "create_blueprint",
        "create_blueprint_revision",
        "instantiate_blueprint",
        "create_action",
        "run_draft_action",
        "run_active_action",
        "create_test_case",
        "run_application_tests",
        "validate_experience_revision",
        "activate_experience_revision",
        "create_trigger",
        "run_trigger",
        "dispatch_event",
        "inspect_scheduler",
        "create_callback_route",
        "inspect_callback_routes",
        "dispatch_callback_route",
        "build_experience_surface",
        "inspect_component_palette",
        "inspect_experience_design_context",
        "inspect_surface_theme",
        "inspect_experience_bridge",
        "inspect_invocations",
        "preview_package",
        "export_package",
        "delete_working_set",
        "list_packages",
        "inspect_package",
        "import_package",
        "inspect_package_import",
        "resume_application_triggers",
        "inspect_objects",
        "inspect_relations",
        "validate_graph_shape",
        "repair_graph_edges",
        "reset_dev_graph",
        "create_secret_reference",
        "set_secret_value",
        "create_credential_record",
        "inspect_secret_references",
        "inspect_credential_records",
        "inspect_job_attempts",
        "process_due_job_work",
    }.issubset(tool_names)
    assert "apply_official_system" not in tool_names
    assert "create_application_relation" not in tool_names
    assert "delete_application_relation" not in tool_names
    assert {
        "inspect_data_spaces",
        "list_blueprints",
        "inspect_blueprint",
        "inspect_activation_attempts",
        "inspect_builds",
        "inspect_experience_build_artifacts",
        "inspect_cypher_audits",
        "inspect_deleted_objects",
        "list_experiences",
        "inspect_experience",
        "inspect_source_changes",
    }.isdisjoint(tool_names)
    assert platform_release_tools == {
        "inspect_authoring_guide",
        "inspect_surface_theme",
    }
    result = await server.call_tool(
        "create_application", {"application_id": "mcp-app", "name": "MCP App"}
    )
    revision = await server.call_tool(
        "create_application_revision",
        {"application_id": result.structured_content["id"]},
    )

    assert result.structured_content["id"] == "mcp-app"
    assert revision.structured_content["ui_profile"] == "n4x-default"


async def _assert_mcp_can_author_canonical_experience() -> None:
    system = create_test_runtime()
    server = create_system_mcp(system)
    app = await server.call_tool(
        "create_application", {"application_id": "mcp-backend", "name": "Backend"}
    )
    app_revision = await server.call_tool(
        "create_application_revision",
        {"application_id": app.structured_content["id"]},
    )
    await server.call_tool(
        "activate_application_revision",
        {"application_revision_id": app_revision.structured_content["id"]},
    )
    experience = await server.call_tool(
        "create_experience",
        {"experience_id": "mcp-ui", "name": "MCP UI"},
    )
    revision = await server.call_tool(
        "create_experience_revision",
        {
            "experience_id": experience.structured_content["id"],
            "ui_profile": "none",
        },
    )
    design_context = await server.call_tool(
        "inspect_experience_design_context",
        {
            "experience_revision_id": revision.structured_content["id"],
            "include_content": False,
        },
    )
    await server.call_tool(
        "set_experience_application_access",
        {
            "experience_revision_id": revision.structured_content["id"],
            "application_access": [{"application_id": app.structured_content["id"]}],
        },
    )
    written = await server.call_tool(
        "write_source_file",
        {
            "revision_id": revision.structured_content["id"],
            "path": "src/main.ts",
            "content": "document.body.textContent = 'MCP';\n",
            "role": "surface",
            "language": "typescript",
        },
    )
    await server.call_tool(
        "create_experience_runtime_dependency",
        {
            "experience_revision_id": revision.structured_content["id"],
            "package": "react",
            "spec": "^19",
        },
    )
    surface = await server.call_tool(
        "create_experience_surface",
        {
            "experience_revision_id": revision.structured_content["id"],
            "surface_id": "mcp-ui.main",
            "surface_type": "browser",
            "entrypoint": "src/main.ts",
            "source_paths": ["src/main.ts"],
            "config": {"mount_path": "/"},
        },
    )
    inspected = await server.call_tool(
        "inspect_experience_revision",
        {"experience_revision_id": revision.structured_content["id"]},
    )
    read = await server.call_tool(
        "read_source_file",
        {
            "revision_id": revision.structured_content["id"],
            "path": "src/main.ts",
        },
    )
    report = await server.call_tool(
        "validate_experience_revision",
        {"experience_revision_id": revision.structured_content["id"]},
    )

    assert revision.structured_content["authoring_next_step"]["tool"] == (
        "inspect_experience_design_context"
    )
    assert design_context.structured_content["advisory_only"] is True
    assert design_context.structured_content["revision"]["ui_profile"] == "none"
    assert "complete visual implementation" in (
        design_context.structured_content["profile_guidance"]
    )
    assert "content" not in (
        design_context.structured_content["platform_release"]["authoring_guide"]
    )
    assert "css_text" not in (
        design_context.structured_content["platform_release"]["surface_theme"]
    )
    assert surface.structured_content["surface_id"] == "mcp-ui.main"
    assert "content" not in written.structured_content
    assert inspected.structured_content["surfaces"][0]["surface_id"] == "mcp-ui.main"
    assert inspected.structured_content["dirty_paths"] == ["src/main.ts"]
    assert inspected.structured_content["surfaces_without_current_artifact"] == [
        "mcp-ui.main"
    ]
    assert "content" not in inspected.structured_content["source_files"][0]
    skipped = await server.call_tool(
        "inspect_experience_design_context",
        {
            "experience_revision_id": revision.structured_content["id"],
            "last_seen_hash": design_context.structured_content["content_hash"],
        },
    )
    assert skipped.structured_content["unchanged"] is True
    assert read.structured_content["content"] == "document.body.textContent = 'MCP';\n"
    assert report.structured_content["status"] == "passed"
    retired = await server.call_tool(
        "retire_experience",
        {"experience_id": experience.structured_content["id"]},
    )
    assert retired.structured_content["status"] == "disabled"
    assert retired.structured_content["active_revision_id"] is None


async def _assert_mcp_secret_inspection_requires_application_scope() -> None:
    system = create_test_runtime()
    system.create_application("secrets-a", "Secrets A")
    system.create_application("secrets-b", "Secrets B")
    first = system.secrets.create_reference("secrets-a", "secret://secrets-a/password")
    second = system.secrets.create_reference("secrets-b", "secret://secrets-b/password")
    system.secrets.create_credential_record(
        "secrets-a",
        "example",
        "first",
        secret_reference_ids=[first.id],
    )
    system.secrets.create_credential_record(
        "secrets-b",
        "example",
        "second",
        secret_reference_ids=[second.id],
    )
    server = create_system_mcp(system)

    first_references = await server.call_tool(
        "inspect_secret_references", {"application_id": "secrets-a"}
    )
    second_references = await server.call_tool(
        "inspect_secret_references", {"application_id": "secrets-b"}
    )
    scoped_references = first_references
    all_credentials = await server.call_tool("inspect_credential_records", {})
    scoped_credentials = await server.call_tool(
        "inspect_credential_records", {"application_id": "secrets-b"}
    )

    assert {
        item["application_id"]
        for item in first_references.structured_content["result"]
    } == {"secrets-a"}
    assert {
        item["application_id"]
        for item in second_references.structured_content["result"]
    } == {"secrets-b"}
    assert [
        item["application_id"]
        for item in scoped_references.structured_content["result"]
    ] == ["secrets-a"]
    assert {
        item["application_id"] for item in all_credentials.structured_content["result"]
    } == {"secrets-a", "secrets-b"}
    assert [
        item["application_id"]
        for item in scoped_credentials.structured_content["result"]
    ] == ["secrets-b"]


async def _assert_mcp_can_create_and_run_application_tests() -> None:
    server = create_system_mcp(create_test_runtime())
    app = await server.call_tool(
        "create_application", {"application_id": "tested", "name": "Tested"}
    )
    revision = await server.call_tool(
        "create_application_revision", {"application_id": app.structured_content["id"]}
    )
    await server.call_tool(
        "write_source_file",
        {
            "revision_id": revision.structured_content["id"],
            "path": "actions/echo.py",
            "content": "def run(ctx, input):\n    return {'value': input['value']}\n",
            "role": "action",
            "language": "python",
        },
    )
    action = await server.call_tool(
        "create_action",
        {
            "application_revision_id": revision.structured_content["id"],
            "action_id": "tested.echo",
            "kind": "normal",
            "entrypoint": "actions/echo.py:run",
            "source_paths": ["actions/echo.py"],
        },
    )
    test = await server.call_tool(
        "create_test_case",
        {
            "application_revision_id": revision.structured_content["id"],
            "action_id": action.structured_content["action_id"],
            "input_value": {"value": "ok"},
            "expected_output": {"value": "ok"},
        },
    )
    invocations = await server.call_tool(
        "run_application_tests",
        {"application_revision_id": revision.structured_content["id"]},
    )

    assert (
        test.structured_content["action_id"] == action.structured_content["action_id"]
    )
    assert invocations.structured_content["result"][0]["status"] == "succeeded"


async def _assert_mcp_can_run_active_action() -> None:
    server = create_system_mcp(create_test_runtime())
    app = await server.call_tool(
        "create_application", {"application_id": "active-run", "name": "Active Run"}
    )
    revision = await server.call_tool(
        "create_application_revision", {"application_id": app.structured_content["id"]}
    )
    await server.call_tool(
        "write_source_file",
        {
            "revision_id": revision.structured_content["id"],
            "path": "actions/echo.py",
            "content": "def run(ctx, input):\n    return {'value': input['value']}\n",
            "role": "action",
            "language": "python",
        },
    )
    await server.call_tool(
        "create_action",
        {
            "application_revision_id": revision.structured_content["id"],
            "action_id": "active-run.echo",
            "kind": "normal",
            "entrypoint": "actions/echo.py:run",
            "source_paths": ["actions/echo.py"],
        },
    )
    await server.call_tool(
        "activate_application_revision",
        {"application_revision_id": revision.structured_content["id"]},
    )

    invocation = await server.call_tool(
        "run_active_action",
        {
            "application_id": "active-run",
            "action_id": "active-run.echo",
            "input_value": {"value": "ok"},
        },
    )

    assert invocation.structured_content["status"] == "succeeded"
    assert invocation.structured_content["output"] == {"value": "ok"}


async def _assert_mcp_reset_requires_exact_database_confirmation() -> None:
    system = create_test_runtime()
    system.store.graph = SimpleNamespace(  # type: ignore[attr-defined]
        config=Neo4jConfig(
            uri="bolt://unused",
            user="unused",
            password="unused",
            database="n4x-development",
        )
    )
    server = create_system_mcp(system)

    with pytest.raises(FastMCPValidationError, match="confirmation_database"):
        await server.call_tool("reset_dev_graph", {})
    with pytest.raises(ToolError, match="must exactly match"):
        await server.call_tool("reset_dev_graph", {"confirmation_database": "neo4j"})

    assert system.store.reset_count == 0  # type: ignore[attr-defined]

    confirmed = await server.call_tool(
        "reset_dev_graph",
        {"confirmation_database": "n4x-development"},
    )

    assert confirmed.structured_content == {
        "reset": True,
        "database": "n4x-development",
    }
    assert system.store.reset_count == 1  # type: ignore[attr-defined]
