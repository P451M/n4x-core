from __future__ import annotations

import asyncio

from n4x.system.mcp import create_system_mcp
from n4x.system.runtime import SystemRuntime
from n4x.testing.graph_store import InMemoryGraphStore


def test_system_mcp_declares_authoring_catalog() -> None:
    mcp = create_system_mcp()
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {
        "inspect_system",
        "inspect_client_guide",
        "import_official_system",
        "enable_system_revision",
        "create_application",
        "create_application_revision",
        "create_object_type",
        "create_relation_type",
        "create_action",
        "create_trigger",
        "create_experience",
        "create_experience_revision",
        "write_source_file",
        "read_source_file",
        "apply_source_patch",
        "search_source_tree",
        "list_source_tree",
        "create_experience_surface",
        "create_test_case",
        "run_draft_action",
        "run_application_tests",
        "validate_application_revision",
        "activate_application_revision",
        "validate_experience_revision",
        "activate_experience_revision",
        "export_package",
        "delete_working_set",
        "list_packages",
        "import_package",
        "inspect_invocations",
        "process_due_job_work",
        "inspect_experience_design_context",
        "inspect_authoring_guide",
        "create_blueprint",
        "instantiate_blueprint",
        "create_development_data_space",
        "run_trigger",
        "dispatch_event",
        "inspect_scheduler",
        "build_experience_surface",
        "create_callback_route",
        "inspect_objects",
        "create_secret_reference",
        "validate_graph_shape",
    }.issubset(names)
    assert "apply_official_system" not in names
    assert "create_application_relation" not in names
    assert "delete_application_relation" not in names
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
    }.isdisjoint(names)


def test_system_mcp_can_author_application_source() -> None:
    runtime = SystemRuntime(InMemoryGraphStore())
    mcp = create_system_mcp(runtime)

    async def author() -> None:
        app = await mcp.call_tool(
            "create_application",
            {"application_id": "mail", "name": "Mail"},
        )
        revision = await mcp.call_tool(
            "create_application_revision",
            {"application_id": app.structured_content["id"]},
        )
        written = await mcp.call_tool(
            "write_source_file",
            {
                "source_tree_id": revision.structured_content["source_tree_id"],
                "path": "actions/echo.py",
                "content": "def run():\n    return {'ok': True}\n",
                "role": "action",
                "language": "python",
            },
        )
        listed = await mcp.call_tool(
            "list_source_tree",
            {"source_tree_id": revision.structured_content["source_tree_id"]},
        )
        read = await mcp.call_tool(
            "read_source_file",
            {
                "source_tree_id": revision.structured_content["source_tree_id"],
                "path": "actions/echo.py",
            },
        )
        assert "content" not in written.structured_content
        assert listed.structured_content["result"][0]["path"] == "actions/echo.py"
        assert "return {'ok': True}" in read.structured_content["content"]

    asyncio.run(author())


def test_system_mcp_exposes_mcp_app_surface(tmp_path) -> None:
    from n4x.kernel.models import BuildArtifact
    from n4x.mcp.surface_apps import _surface_tool_name, _surface_uri
    from n4x.runtime.actions import RuntimePaths
    from n4x.testing.graph_store import InMemoryGraphStore

    runtime = SystemRuntime(
        InMemoryGraphStore(), runtime_paths=RuntimePaths.temporary()
    )
    try:
        runtime.applications.create("native-data", "Native Data")
        experience = runtime.experiences.create("native-mcp", "Native MCP")
        revision = runtime.experiences.create_revision(
            "native-mcp", ui_profile="none"
        )
        runtime.source.write_source_file(
            revision.source_tree_id,
            "src/main.ts",
            "ready",
            role="surface",
            language="typescript",
        )
        surface = runtime.experiences.surfaces.create(
            revision.id,
            "assistant",
            surface_type="mcp_app",
            surface_type_version=1,
            entrypoint="src/main.ts",
            source_paths=["src/main.ts"],
            description="Native assistant",
            config={"related_browser_path": "/details"},
        )
        html = "<html><body>system-mcp-html</body></html>"
        html_path = tmp_path / "mcp-app.html"
        html_path.write_text(html, encoding="utf-8")
        runtime.surfaces.build_input_hash = lambda _surface: "current"
        with runtime.uow:
            runtime.graph.experience_revisions.save(
                revision.model_copy(update={"status": "active"})
            )
            runtime.graph.experiences.save(
                experience.model_copy(
                    update={"status": "active", "active_revision_id": revision.id}
                )
            )
            runtime.graph.build_artifacts.save(
                BuildArtifact(
                    id="mcp-artifact",
                    owner_kind="ExperienceRevision",
                    owner_id=revision.id,
                    build_invocation_id="build",
                    artifact_type="surface_bundle",
                    path=str(tmp_path),
                    content_hash="mcp",
                    surface_id=surface.surface_id,
                    surface_type="mcp_app",
                    input_hash="current",
                    manifest={"html": str(html_path)},
                )
            )
        mcp = create_system_mcp(runtime)
        uri = _surface_uri(experience.id, revision.id, surface.surface_id)

        async def inspect() -> None:
            resources = await mcp.list_resources()
            contents = await mcp.read_resource(uri)
            opened = await mcp.call_tool(
                _surface_tool_name("open", experience.id, surface.surface_id),
                {},
            )
            assert uri in {str(resource.uri) for resource in resources}
            assert contents.contents[0].content == html
            assert opened.structured_content["related_browser_url"].endswith(
                "/experience/native-mcp/details"
            )

        asyncio.run(inspect())
    finally:
        runtime.close()


def test_system_activate_experience_notifies_mcp_catalog() -> None:
    from fastmcp import Client
    from fastmcp.client.messages import MessageHandler
    from mcp.types import ResourceListChangedNotification, ToolListChangedNotification
    from n4x.runtime.actions import RuntimePaths
    from n4x.testing.graph_store import InMemoryGraphStore

    runtime = SystemRuntime(
        InMemoryGraphStore(), runtime_paths=RuntimePaths.temporary()
    )
    try:
        runtime.applications.create("mail", "Mail")
        app_revision = runtime.applications.create_revision("mail")
        runtime.activation.activate(app_revision.id)
        runtime.experiences.create("mail-ui", "Mail UI")
        revision = runtime.experiences.create_revision(
            "mail-ui",
            ui_profile="none",
            application_access=[{"application_id": "mail"}],
        )
        received: list[str] = []

        class Handler(MessageHandler):
            async def on_resource_list_changed(
                self, message: ResourceListChangedNotification
            ) -> None:
                received.append(message.method)

            async def on_tool_list_changed(
                self, message: ToolListChangedNotification
            ) -> None:
                received.append(message.method)

        async def activate() -> None:
            async with Client(
                create_system_mcp(runtime), message_handler=Handler()
            ) as client:
                result = await client.call_tool(
                    "activate_experience_revision",
                    {"experience_revision_id": revision.id},
                )
            assert result.structured_content["status"] == "active"

        asyncio.run(activate())
        assert received == [
            "notifications/resources/list_changed",
            "notifications/tools/list_changed",
            "notifications/resources/list_changed",
            "notifications/tools/list_changed",
        ]
    finally:
        runtime.close()


def test_system_mcp_handshake_notifies_catalog() -> None:
    from fastmcp import Client
    from fastmcp.client.messages import MessageHandler
    from mcp.types import ResourceListChangedNotification, ToolListChangedNotification

    received: list[str] = []

    class Handler(MessageHandler):
        async def on_resource_list_changed(
            self, message: ResourceListChangedNotification
        ) -> None:
            received.append(message.method)

        async def on_tool_list_changed(
            self, message: ToolListChangedNotification
        ) -> None:
            received.append(message.method)

    async def connect() -> None:
        async with Client(create_system_mcp(), message_handler=Handler()):
            await asyncio.sleep(0.05)

    asyncio.run(connect())
    assert received == [
        "notifications/resources/list_changed",
        "notifications/tools/list_changed",
    ]
