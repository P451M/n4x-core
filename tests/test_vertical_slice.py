from __future__ import annotations

import pytest

from n4x.graph.store import node_ref
from n4x.kernel.errors import ImmutableRevisionError, ValidationFailure
from n4x.testing import create_test_runtime, tree_id


def test_draft_action_activation_and_rollback_vertical_slice() -> None:
    system = create_test_runtime()
    app = system.create_application("tasks", "Tasks")

    rev1 = system.create_application_revision(app.id)
    system.source.write_source_file(
        rev1.id,
        "actions/hello.py",
        'def run(ctx, input):\n    print("running")\n    return {"message": "hello " + input["name"]}\n',
        role="action",
        language="python",
    )
    action_v1 = system.create_action(
        rev1.id,
        "tasks.hello",
        kind="normal",
        entrypoint="actions/hello.py:run",
        source_paths=["actions/hello.py"],
        input_schema={"type": "object", "required": ["name"]},
    )
    draft_invocation = system.run_draft_action(
        rev1.id, action_v1.action_id, {"name": "n4x"}
    )
    assert draft_invocation.status == "succeeded"
    assert draft_invocation.output == {"message": "hello n4x"}
    assert "running" in draft_invocation.stdout

    system.create_test_case(
        rev1.id, action_v1.action_id, {"name": "test"}, {"message": "hello test"}
    )
    activated_v1 = system.activate_application_revision(rev1.id)
    assert activated_v1.status == "active"
    records = system.uow.records
    assert records.applications[app.id].active_revision_id == rev1.id
    assert records.source_trees[tree_id(system, activated_v1)].status == "interned"

    with pytest.raises(ImmutableRevisionError):
        system.run_draft_action(rev1.id, action_v1.action_id, {"name": "blocked"})

    rev2 = system.create_application_revision(app.id)
    assert system.source.read_source_file(
        tree_id(system, rev2), "actions/hello.py"
    ).content_hash
    assert any(
        action_revision.action_id == "tasks.hello"
        for action_revision in system.source.bindings.action_revisions(rev2.id)
    )
    system.source.write_source_file(
        rev2.id,
        "actions/hello.py",
        'def run(ctx, input):\n    return {"message": "hi " + input["name"]}\n',
        role="action",
        language="python",
    )
    action_v2 = system.create_action(
        rev2.id,
        "tasks.hello",
        kind="normal",
        entrypoint="actions/hello.py:run",
        source_paths=["actions/hello.py"],
        input_schema={"type": "object", "required": ["name"]},
    )
    system.create_test_case(
        rev2.id, action_v2.action_id, {"name": "test"}, {"message": "hi test"}
    )
    activated_v2 = system.activate_application_revision(rev2.id)
    assert activated_v2.status == "active"
    assert records.revisions[rev1.id].status == "superseded"

    rolled_back = system.rollback_application(app.id, rev1.id)
    assert rolled_back.status == "active"
    assert records.applications[app.id].active_revision_id == rev1.id


def test_activation_failure_leaves_current_revision_active() -> None:
    system = create_test_runtime()
    app = system.create_application("tasks", "Tasks")

    rev1 = system.create_application_revision(app.id)
    system.source.write_source_file(
        rev1.id,
        "actions/ok.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    system.create_action(
        rev1.id,
        "tasks.ok",
        kind="normal",
        entrypoint="actions/ok.py:run",
        source_paths=["actions/ok.py"],
    )
    system.activate_application_revision(rev1.id)

    rev2 = system.create_application_revision(app.id)
    system.source.write_source_file(
        rev2.id,
        "migrations/fail.py",
        "def run(ctx, input):\n    raise RuntimeError('migration failed')\n",
        role="migration",
        language="python",
    )
    system.create_action(
        rev2.id,
        "tasks.fail",
        kind="migration",
        entrypoint="migrations/fail.py:run",
        source_paths=["migrations/fail.py"],
    )

    with pytest.raises(ValidationFailure):
        system.activate_application_revision(rev2.id)

    records = system.uow.records
    assert records.applications[app.id].active_revision_id == rev1.id
    assert records.revisions[rev2.id].status == "rejected"


def test_cloned_schema_definitions_are_replaced_within_one_draft() -> None:
    system = create_test_runtime()
    app = system.create_application("schema-evolution", "Schema evolution")
    first = system.create_application_revision(app.id)
    system.create_object_type(first.id, "schema.Parent", name="Parent")
    system.create_object_type(first.id, "schema.Child", name="Child")
    system.create_relation_type(
        first.id,
        "schema.contains",
        name="Contains",
        from_object_type_id="schema.Parent",
        to_object_type_id="schema.Child",
    )
    system.activate_application_revision(first.id)

    draft = system.create_application_revision(app.id)
    records = system.uow.records
    bindings = system.source.bindings
    original_object = next(
        revision
        for revision in bindings.object_type_revisions(draft.id)
        if revision.object_type_id == "schema.Child"
    )
    original_relation = next(
        revision
        for revision in bindings.relation_type_revisions(draft.id)
        if revision.relation_type_id == "schema.contains"
    )

    replaced_object = system.create_object_type(
        draft.id,
        "schema.Child",
        name="Child",
        properties={"label": {"type": "string"}},
        required=["label"],
    )
    replaced_relation = system.create_relation_type(
        draft.id,
        "schema.contains",
        name="Contains",
        from_object_type_id="schema.Parent",
        to_object_type_id="schema.Child",
        properties={"position": {"type": "integer"}},
    )

    assert replaced_object.id != original_object.id
    assert replaced_relation.id != original_relation.id
    assert [
        revision.object_type_id
        for revision in bindings.object_type_revisions(draft.id)
        if revision.object_type_id == "schema.Child"
    ] == ["schema.Child"]
    assert [
        revision.relation_type_id
        for revision in bindings.relation_type_revisions(draft.id)
        if revision.relation_type_id == "schema.contains"
    ] == ["schema.contains"]

    system.activate_application_revision(draft.id)
    assert records.object_types["schema.Child"].active_revision_id == replaced_object.id
    assert (
        records.relation_types["schema.contains"].active_revision_id
        == replaced_relation.id
    )


def test_validation_rejects_duplicate_definition_revisions() -> None:
    system = create_test_runtime()
    app = system.create_application("duplicate-schema", "Duplicate schema")
    revision = system.create_application_revision(app.id)
    original = system.create_object_type(
        revision.id, "duplicate-schema.Item", name="Item"
    )
    duplicate = original.model_copy(update={"id": "duplicate-schema.Item@duplicate"})
    system.uow.records.object_type_revisions.save(duplicate)
    system.store.create_edge(
        node_ref("ApplicationRevision", id=revision.id),
        "HAS_OBJECT_TYPE_REVISION",
        node_ref("ObjectTypeRevision", id=duplicate.id),
    )

    report = system.validate_application_revision(revision.id)

    assert report.status == "failed"
    assert report.errors == [
        "ObjectTypeRevision: duplicate object_type_id duplicate-schema.Item "
        "on duplicate-schema@1"
    ]
