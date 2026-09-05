from __future__ import annotations

from n4x.system.runtime import SystemRuntime
from n4x.testing import tree_id
from n4x.testing.graph_store import InMemoryGraphStore


def _mark_application_revision_active(
    runtime: SystemRuntime, application_id: str, revision_id: str
) -> None:
    runtime.source.intern_tree(revision_id)
    application = runtime.uow.records.applications[application_id]
    runtime.uow.applications.replace_active_revision(
        application_id,
        revision_id,
        expected_revision_id=application.active_revision_id,
    )
    runtime.uow.applications.save(
        application.model_copy(update={"active_revision_id": revision_id})
    )
    revision = runtime.uow.records.revisions[revision_id]
    runtime.uow.applications.save_revision(
        revision.model_copy(update={"status": "active"})
    )
    bindings = runtime.source.bindings
    for stable, match in (
        *(
            (runtime.uow.records.actions[item.action_id], item)
            for item in bindings.action_revisions(revision_id)
        ),
        *(
            (runtime.uow.records.triggers[item.trigger_id], item)
            for item in bindings.trigger_revisions(revision_id)
        ),
        *(
            (runtime.uow.records.object_types[item.object_type_id], item)
            for item in bindings.object_type_revisions(revision_id)
        ),
        *(
            (runtime.uow.records.relation_types[item.relation_type_id], item)
            for item in bindings.relation_type_revisions(revision_id)
        ),
    ):
        if stable.application_id != application_id:
            continue
        collection = {
            "Action": runtime.uow.records.actions,
            "Trigger": runtime.uow.records.triggers,
            "ObjectType": runtime.uow.records.object_types,
            "RelationType": runtime.uow.records.relation_types,
        }[type(stable).__name__]
        collection.save(stable.model_copy(update={"active_revision_id": match.id}))


def test_system_creates_application() -> None:
    runtime = SystemRuntime(InMemoryGraphStore())
    created = runtime.applications.create("mail", "Mail", "Inbox")
    assert created.id == "mail"
    assert created.name == "Mail"
    listed = runtime.applications.list()
    assert [item.id for item in listed] == ["mail"]
    draft = runtime.applications.create_revision("mail")
    assert draft.id.startswith("mail@")
    assert tree_id(runtime, draft)
    spaces = list(runtime.uow.records.data_spaces.values())
    assert any(
        space.id == "production" and space.application_id == "mail" for space in spaces
    )


def test_system_draft_inherits_source_and_schema() -> None:
    runtime = SystemRuntime(InMemoryGraphStore())
    runtime.applications.create("mail", "Mail")
    first = runtime.applications.create_revision("mail")
    runtime.source.write_source_file(
        first.id,
        "actions/echo.py",
        "def run():\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    runtime.applications.create_runtime_dependency(
        first.id, "python", "httpx", ">=0.28"
    )
    object_revision = runtime.schema.create_object_type(
        first.id,
        "mail.Message",
        name="Message",
        properties={"subject": {"type": "string"}},
        required=["subject"],
    )
    runtime.schema.create_relation_type(
        first.id,
        "mail.belongs_to",
        name="belongs_to",
        from_object_type_id="mail.Message",
        to_object_type_id="mail.Message",
    )
    action = runtime.definitions.create_action(
        first.id,
        "mail.echo",
        kind="normal",
        entrypoint="actions/echo.py:run",
        source_paths=["actions/echo.py"],
    )
    trigger = runtime.definitions.create_trigger(
        first.id,
        "mail.hourly",
        trigger_type="schedule",
        action_id=action.action_id,
        config={"cron": "0 * * * *"},
    )
    _mark_application_revision_active(runtime, "mail", first.id)

    draft = runtime.applications.create_revision("mail")
    assert draft.parent_revision_id == first.id
    assert tree_id(runtime, draft) == tree_id(runtime, first)
    cloned = runtime.source.read_source_file(tree_id(runtime, draft), "actions/echo.py")
    assert "return {'ok': True}" in cloned.content
    bindings = runtime.source.bindings
    assert [item.id for item in bindings.object_type_revisions(draft.id)] == [
        object_revision.id
    ]
    assert [item.relation_type_id for item in bindings.relation_type_revisions(draft.id)] == [
        "mail.belongs_to"
    ]
    assert [item.package for item in bindings.dependencies(draft.id)] == ["httpx"]
    cloned_actions = bindings.action_revisions(draft.id)
    assert [item.action_id for item in cloned_actions] == ["mail.echo"]
    assert cloned_actions[0].id == action.id
    cloned_triggers = bindings.trigger_revisions(draft.id)
    assert [item.trigger_id for item in cloned_triggers] == ["mail.hourly"]
    assert cloned_triggers[0].id == trigger.id
    assert cloned_triggers[0].action_id == action.action_id
