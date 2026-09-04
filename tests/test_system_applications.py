from __future__ import annotations

from n4x.system.runtime import SystemRuntime
from n4x.testing.graph_store import InMemoryGraphStore


def _mark_application_revision_active(
    runtime: SystemRuntime, application_id: str, revision_id: str
) -> None:
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
    for collection, owner_field, revision_collection in (
        (runtime.uow.records.actions, "action_id", runtime.uow.records.action_revisions),
        (
            runtime.uow.records.triggers,
            "trigger_id",
            runtime.uow.records.trigger_revisions,
        ),
    ):
        for stable in collection.values():
            if stable.application_id != application_id:
                continue
            match = next(
                (
                    item
                    for item in revision_collection.values()
                    if getattr(item, owner_field) == stable.id
                    and item.application_revision_id == revision_id
                ),
                None,
            )
            if match is not None:
                collection.save(
                    stable.model_copy(update={"active_revision_id": match.id})
                )
    for object_type in runtime.uow.records.object_types.values():
        if object_type.application_id != application_id:
            continue
        match = next(
            (
                item
                for item in runtime.uow.records.object_type_revisions.values()
                if item.object_type_id == object_type.id
                and item.application_revision_id == revision_id
            ),
            None,
        )
        if match is not None:
            runtime.uow.records.object_types.save(
                object_type.model_copy(update={"active_revision_id": match.id})
            )
    for relation_type in runtime.uow.records.relation_types.values():
        if relation_type.application_id != application_id:
            continue
        match = next(
            (
                item
                for item in runtime.uow.records.relation_type_revisions.values()
                if item.relation_type_id == relation_type.id
                and item.application_revision_id == revision_id
            ),
            None,
        )
        if match is not None:
            runtime.uow.records.relation_types.save(
                relation_type.model_copy(update={"active_revision_id": match.id})
            )


def test_system_creates_application() -> None:
    runtime = SystemRuntime(InMemoryGraphStore())
    created = runtime.applications.create("mail", "Mail", "Inbox")
    assert created.id == "mail"
    assert created.name == "Mail"
    listed = runtime.applications.list()
    assert [item.id for item in listed] == ["mail"]
    draft = runtime.applications.create_revision("mail")
    assert draft.id.startswith("mail@")
    assert draft.source_tree_id
    spaces = list(runtime.uow.records.data_spaces.values())
    assert any(
        space.id == "production" and space.application_id == "mail" for space in spaces
    )


def test_system_draft_inherits_source_and_schema() -> None:
    runtime = SystemRuntime(InMemoryGraphStore())
    runtime.applications.create("mail", "Mail")
    first = runtime.applications.create_revision("mail")
    runtime.source.write_source_file(
        first.source_tree_id,
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
    runtime.definitions.create_trigger(
        first.id,
        "mail.hourly",
        trigger_type="schedule",
        action_revision_id=action.id,
        config={"cron": "0 * * * *"},
    )
    _mark_application_revision_active(runtime, "mail", first.id)

    draft = runtime.applications.create_revision("mail")
    assert draft.parent_revision_id == first.id
    assert draft.source_tree_id != first.source_tree_id
    cloned = runtime.source.read_source_file(draft.source_tree_id, "actions/echo.py")
    assert "return {'ok': True}" in cloned.content
    cloned_objects = [
        item
        for item in runtime.uow.records.object_type_revisions.values()
        if item.application_revision_id == draft.id
    ]
    assert len(cloned_objects) == 1
    assert cloned_objects[0].id != object_revision.id
    assert cloned_objects[0].object_type_id == "mail.Message"
    cloned_relations = [
        item
        for item in runtime.uow.records.relation_type_revisions.values()
        if item.application_revision_id == draft.id
    ]
    assert len(cloned_relations) == 1
    cloned_deps = [
        item
        for item in runtime.uow.records.runtime_dependencies.values()
        if item.owner_id == draft.id
    ]
    assert [item.package for item in cloned_deps] == ["httpx"]
    cloned_actions = [
        item
        for item in runtime.uow.records.action_revisions.values()
        if item.application_revision_id == draft.id
    ]
    assert [item.action_id for item in cloned_actions] == ["mail.echo"]
    assert cloned_actions[0].id != action.id
    cloned_triggers = [
        item
        for item in runtime.uow.records.trigger_revisions.values()
        if item.application_revision_id == draft.id
    ]
    assert [item.trigger_id for item in cloned_triggers] == ["mail.hourly"]
    assert cloned_triggers[0].action_revision_id == cloned_actions[0].id
