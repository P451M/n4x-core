from __future__ import annotations

from n4x.testing import create_test_runtime
from tests.cypher_source import action_source


def test_actions_can_create_and_read_application_objects() -> None:
    system = create_test_runtime()
    app = system.create_application("notes", "Notes")
    revision = system.create_application_revision(app.id)
    system.create_object_type(
        revision.id,
        "notes.Note",
        name="Note",
        properties={"title": {"type": "string"}, "body": {"type": "string"}},
    )
    system.source.write_source_file(
        revision.id,
        "actions/notes.py",
        action_source(
            "def create_note(ctx, input):\n"
            "    note = upsert_object(ctx, 'notes.Note', "
            "{'title': input['title'], 'body': input['body']})\n"
            "    return {'id': note['id']}\n",
            "def list_notes(ctx, input):\n"
            "    return {'notes': list_objects(ctx, 'notes.Note')}\n",
        ),
        role="action",
        language="python",
    )
    create_action = system.create_action(
        revision.id,
        "notes.create",
        kind="normal",
        entrypoint="actions/notes.py:create_note",
        source_paths=["actions/notes.py"],
        input_schema={"type": "object", "required": ["title", "body"]},
    )
    list_action = system.create_action(
        revision.id,
        "notes.list",
        kind="normal",
        entrypoint="actions/notes.py:list_notes",
        source_paths=["actions/notes.py"],
    )

    create_invocation = system.run_draft_action(
        revision.id, create_action.action_id, {"title": "First", "body": "Hello"}
    )
    list_invocation = system.run_draft_action(revision.id, list_action.action_id, {})

    assert create_invocation.status == "succeeded", create_invocation.error
    assert len(system.uow.records.objects) == 1
    persisted = system.list_application_objects(app.id, "notes.Note")[0]
    assert persisted.values["title"] == "First"
    assert persisted.created_at is not None
    assert persisted.updated_at is not None
    assert list_invocation.output["notes"][0]["values"] == {
        "title": "First",
        "body": "Hello",
    }
    assert "created_at" not in list_invocation.output["notes"][0]
    assert "updated_at" not in list_invocation.output["notes"][0]


def test_action_operations_commit_or_roll_back_in_app_transaction() -> None:
    system = create_test_runtime()
    app = system.create_application("staged", "Staged")
    revision = system.create_application_revision(app.id)
    system.create_object_type(revision.id, "staged.Item", name="Item")
    system.source.write_source_file(
        revision.id,
        "actions/staged.py",
        action_source(
            "def create_update(ctx, input):\n"
            "    with ctx.graph.transaction():\n"
            "        item = upsert_object(ctx, 'staged.Item', {'value': 1})\n"
            "        return upsert_object("
            "ctx, 'staged.Item', {'value': 2}, object_id=item['id'])\n",
            "def create_delete(ctx, input):\n"
            "    item = upsert_object(ctx, 'staged.Item', {'value': 3})\n"
            "    delete_object(ctx, item['id'])\n"
            "    return {'id': item['id']}\n",
            "def invalid_update(ctx, input):\n"
            "    with ctx.graph.transaction():\n"
            "        upsert_object(ctx, 'staged.Item', {'value': 4})\n"
            "        raise RuntimeError('missing')\n",
        ),
        role="action",
        language="python",
    )
    create_update = system.create_action(
        revision.id,
        "staged.create-update",
        kind="normal",
        entrypoint="actions/staged.py:create_update",
        source_paths=["actions/staged.py"],
    )
    create_delete = system.create_action(
        revision.id,
        "staged.create-delete",
        kind="normal",
        entrypoint="actions/staged.py:create_delete",
        source_paths=["actions/staged.py"],
    )
    invalid_update = system.create_action(
        revision.id,
        "staged.invalid-update",
        kind="normal",
        entrypoint="actions/staged.py:invalid_update",
        source_paths=["actions/staged.py"],
    )

    updated = system.run_draft_action(revision.id, create_update.action_id, {})
    deleted = system.run_draft_action(revision.id, create_delete.action_id, {})
    before_invalid = set(system.uow.records.objects)
    invalid = system.run_draft_action(revision.id, invalid_update.action_id, {})

    assert updated.status == "succeeded", updated.error
    assert updated.output["values"] == {"value": 2}
    assert deleted.status == "succeeded", deleted.error
    assert deleted.output["id"] not in {
        key[2] for key in system.uow.records.objects
    }
    assert invalid.status == "failed"
    assert "missing" in (invalid.error or "")
    assert set(system.uow.records.objects) == before_invalid
