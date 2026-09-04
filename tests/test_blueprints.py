from __future__ import annotations

import asyncio

from n4x.testing import create_test_runtime
from n4x.system.mcp import create_system_mcp


def test_authoring_guide_is_graph_owned_and_reused() -> None:
    system = create_test_runtime()

    guide = system.inspect_authoring_guide()
    reused = system.inspect_authoring_guide()

    assert guide["id"] == "n4x.authoring-guide"
    assert guide["release_version"] == "n4x-ui-v8"
    assert "inspect_experience_design_context" in guide["content"]
    assert "visual quality" in guide["content"]
    assert "inspect_surface_theme" in guide["content"]
    assert 'variant="inset"' in guide["content"]
    assert "active action" in guide["content"]
    assert reused["active_revision_id"] == guide["active_revision_id"]
    assert (
        system.uow.records.authoring_guide_revisions[
            guide["active_revision_id"]
        ].content
        == guide["content"]
    )


def test_blueprint_instantiates_ordinary_app_revision() -> None:
    system = create_test_runtime()
    blueprint = system.create_blueprint("echo.basic", "Echo Basic")
    revision = system.create_blueprint_revision(
        blueprint.id,
        instructions="Create a tiny echo app.",
        content=_echo_blueprint_content(),
        activate=True,
    )

    created = system.instantiate_blueprint(revision.id, application_id="echo")
    test_invocations = system.run_application_tests(created["application_revision_id"])
    activated = system.activate_application_revision(created["application_revision_id"])

    assert activated.status == "active"
    assert test_invocations[0].status == "succeeded"
    assert (
        system.uow.records.actions["echo.run"].active_revision_id
        == created["action_revision_ids"]["echo.run"]
    )
    assert (
        system.uow.records.action_revisions[
            created["action_revision_ids"]["echo.run"]
        ].kind
        == "normal"
    )


def test_blueprint_selects_ui_profile_without_creating_theme_nodes() -> None:
    system = create_test_runtime()
    blueprint = system.create_blueprint("custom-ui", "Custom UI")
    content = _echo_blueprint_content()
    content["application"]["ui_profile"] = "custom"
    blueprint_revision = system.create_blueprint_revision(
        blueprint.id,
        instructions="Use an application-owned design system.",
        content=content,
    )
    themes_before = system.uow.records.ui_themes.values()
    theme_revisions_before = system.uow.records.ui_theme_revisions.values()

    created = system.instantiate_blueprint(
        blueprint_revision.id, application_id="custom-ui-app"
    )

    app_revision = system.uow.records.revisions[created["application_revision_id"]]
    assert app_revision.ui_profile == "custom"
    assert system.uow.records.ui_themes.values() == themes_before
    assert system.uow.records.ui_theme_revisions.values() == theme_revisions_before


def test_cloned_application_revision_preserves_ui_profile() -> None:
    system = create_test_runtime()
    app = system.create_application("profile-clone", "Profile Clone")
    first = system.create_application_revision(app.id, ui_profile="none")
    system.activate_application_revision(first.id)

    cloned = system.create_application_revision(app.id)

    assert cloned.parent_revision_id == first.id
    assert cloned.ui_profile == "none"


def test_blueprint_instantiates_typed_experience_content() -> None:
    system = create_test_runtime()
    blueprint = system.create_blueprint("full-stack", "Full Stack")
    content = _echo_blueprint_content()
    content["experiences"] = [
        {
            "id": "full-stack-ui",
            "name": "Full Stack UI",
            "ui_profile": "none",
            "application_access": [{"application_id": "$application"}],
            "dependencies": [{"id": "react", "package": "react", "spec": "^19"}],
            "source_files": [
                {
                    "path": "src/main.ts",
                    "role": "surface",
                    "language": "typescript",
                    "content": "document.body.textContent = 'ready';\n",
                }
            ],
            "surfaces": [
                {
                    "id": "full-stack-ui.main",
                    "surface_type": "browser",
                    "entrypoint": "src/main.ts",
                    "source_paths": ["src/main.ts"],
                    "config": {"mount_path": "/"},
                }
            ],
        }
    ]
    revision = system.create_blueprint_revision(
        blueprint.id,
        instructions="Create backend and frontend drafts.",
        content=content,
    )

    created = system.instantiate_blueprint(
        revision.id, application_id="full-stack-backend"
    )

    experience_result = created["experiences"]["full-stack-ui"]
    experience_revision = system.graph.experience_revisions[
        experience_result["experience_revision_id"]
    ]
    surface = system.graph.experience_surfaces[(
        experience_revision.id, "full-stack-ui.main"
    )]
    assert experience_revision.application_access[0].application_id == (
        "full-stack-backend"
    )
    assert surface.experience_revision_id == experience_revision.id
    assert experience_result["surface_ids"] == ["full-stack-ui.main"]


def test_mcp_can_inspect_and_instantiate_blueprint() -> None:
    asyncio.run(_assert_mcp_can_inspect_and_instantiate_blueprint())


async def _assert_mcp_can_inspect_and_instantiate_blueprint() -> None:
    system = create_test_runtime()
    server = create_system_mcp(system)

    guide = await server.call_tool("inspect_authoring_guide", {})
    blueprint = await server.call_tool(
        "create_blueprint", {"blueprint_id": "echo.basic", "name": "Echo Basic"}
    )
    blueprint_revision = await server.call_tool(
        "create_blueprint_revision",
        {
            "blueprint_id": blueprint.structured_content["id"],
            "instructions": "Create a tiny echo app.",
            "content": _echo_blueprint_content(),
            "activate": True,
        },
    )
    created = await server.call_tool(
        "instantiate_blueprint",
        {
            "blueprint_revision_id": blueprint_revision.structured_content["id"],
            "application_id": "echo",
        },
    )
    tests = await server.call_tool(
        "run_application_tests",
        {
            "application_revision_id": created.structured_content[
                "application_revision_id"
            ]
        },
    )

    assert guide.structured_content["id"] == "n4x.authoring-guide"
    assert guide.structured_content["release_version"] == "n4x-ui-v8"
    assert tests.structured_content["result"][0]["status"] == "succeeded"


def _echo_blueprint_content() -> dict:
    return {
        "application": {"name": "Echo", "description": "Tiny blueprint app"},
        "source_files": [
            {
                "path": "actions/echo.py",
                "role": "action",
                "language": "python",
                "content": "def run(ctx, input):\n    return {'value': input['value']}\n",
            }
        ],
        "actions": [
            {
                "id": "echo.run",
                "kind": "normal",
                "entrypoint": "actions/echo.py:run",
                "source_paths": ["actions/echo.py"],
                "input_schema": {"type": "object", "required": ["value"]},
            }
        ],
        "tests": [
            {
                "action_id": "echo.run",
                "input": {"value": "ok"},
                "expected_output": {"value": "ok"},
            }
        ],
    }
