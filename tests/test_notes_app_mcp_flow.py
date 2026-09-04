from __future__ import annotations

import asyncio
import shutil

import pytest
from n4x.system.mcp import create_system_mcp
from n4x.testing import create_test_runtime
from tests.cypher_source import action_source


@pytest.mark.pnpm
@pytest.mark.skipif(
    shutil.which("pnpm") is None, reason="pnpm is required for Surface build tests"
)
def test_notes_app_can_be_authored_through_mcp_tools() -> None:
    asyncio.run(_assert_notes_app_can_be_authored_through_mcp_tools())


async def _assert_notes_app_can_be_authored_through_mcp_tools() -> None:
    server = create_system_mcp(create_test_runtime())

    app = await server.call_tool(
        "create_application", {"application_id": "notes-mcp", "name": "Notes MCP"}
    )
    revision = await server.call_tool(
        "create_application_revision", {"application_id": app.structured_content["id"]}
    )
    source_tree_id = revision.structured_content["source_tree_id"]
    revision_id = revision.structured_content["id"]

    notebook_type = await server.call_tool(
        "create_object_type",
        {
            "application_revision_id": revision_id,
            "object_type_id": "notes-mcp.Notebook",
            "name": "Notebook",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    )
    note_type = await server.call_tool(
        "create_object_type",
        {
            "application_revision_id": revision_id,
            "object_type_id": "notes-mcp.Note",
            "name": "Note",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            "required": ["title"],
        },
    )
    relation_type = await server.call_tool(
        "create_relation_type",
        {
            "application_revision_id": revision_id,
            "relation_type_id": "notes-mcp.note_notebook",
            "name": "note_notebook",
            "from_object_type_id": note_type.structured_content["object_type_id"],
            "to_object_type_id": notebook_type.structured_content["object_type_id"],
        },
    )
    physical = relation_type.structured_content["physical_type"]

    await server.call_tool(
        "write_source_file",
        {
            "source_tree_id": source_tree_id,
            "path": "actions/notes.py",
            "role": "action",
            "language": "python",
            "content": action_source(
                "def create_note(ctx, input):\n"
                "    notebook = upsert_object(ctx, 'notes-mcp.Notebook', "
                "{'title': input.get('notebook', 'Inbox')})\n"
                "    note = upsert_object(ctx, 'notes-mcp.Note', "
                "{'title': input['title'], 'body': input['body']})\n"
                "    relation = merge_rel(ctx, "
                f"'{physical}', "
                "'notes-mcp.note_notebook', note['id'], notebook['id'])\n"
                "    return {'id': note['id'], 'notebook_id': notebook['id'], "
                "'relation_id': relation['id']}\n",
                "def list_notes(ctx, input):\n"
                "    return {'notes': list_objects(ctx, 'notes-mcp.Note'), "
                "'relations': list_rels(ctx, 'notes-mcp.note_notebook')}\n",
            ),
        },
    )
    create_action = await server.call_tool(
        "create_action",
        {
            "application_revision_id": revision_id,
            "action_id": "notes-mcp.create",
            "kind": "normal",
            "entrypoint": "actions/notes.py:create_note",
            "source_paths": ["actions/notes.py"],
            "input_schema": {"type": "object", "required": ["title", "body"]},
        },
    )
    await server.call_tool(
        "create_action",
        {
            "application_revision_id": revision_id,
            "action_id": "notes-mcp.list",
            "kind": "normal",
            "entrypoint": "actions/notes.py:list_notes",
            "source_paths": ["actions/notes.py"],
        },
    )
    invocation = await server.call_tool(
        "run_draft_action",
        {
            "action_revision_id": create_action.structured_content["id"],
            "input_value": {"title": "First", "body": "Hello", "notebook": "Inbox"},
        },
    )
    objects = await server.call_tool(
        "inspect_objects",
        {
            "application_id": "notes-mcp",
            "object_type_id": "notes-mcp.Note",
            "include_values": True,
        },
    )
    relations = await server.call_tool(
        "inspect_relations",
        {"application_id": "notes-mcp", "include_values": True},
    )
    assert invocation.structured_content["status"] == "succeeded"
    assert objects.structured_content["items"][0]["values"] == {
        "title": "First",
        "body": "Hello",
    }
    assert (
        relations.structured_content["items"][0]["relation_type_id"]
        == "notes-mcp.note_notebook"
    )
