from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from n4x.graph.neo4j import Neo4jGraph
from n4x.graph.store import Neo4jGraphStore, node_ref
from n4x.kernel.errors import ValidationFailure
from n4x.system.runtime import SystemRuntime
from n4x.runtime.actions import RuntimePaths
from n4x.testing import InMemoryGraphStore, create_test_runtime
from tests.cypher_source import action_source


@pytest.fixture(
    params=["memory", pytest.param("neo4j", marks=pytest.mark.neo4j)]
)
def checkpoint_kernel(
    request: pytest.FixtureRequest,
) -> Iterator[SystemRuntime]:
    if request.param == "memory":
        yield SystemRuntime(InMemoryGraphStore(), runtime_paths=RuntimePaths.temporary())
        return
    graph: Neo4jGraph = request.getfixturevalue("neo4j_graph")
    system = SystemRuntime(Neo4jGraphStore(graph), runtime_paths=RuntimePaths.temporary())
    try:
        yield system
    finally:
        graph.run_cypher(
            """
            MATCH (app:Application)
            WHERE app.id STARTS WITH 'checkpoint-contract-'
            OPTIONAL MATCH (app)-[*0..]->(owned)
            WITH collect(DISTINCT owned) AS owned
            UNWIND owned AS node
            DETACH DELETE node
            """
        )


def _create_seeded_graph_app(
    system: SystemRuntime | None = None,
    *,
    application_id: str = "migrate",
):
    system = system or create_test_runtime()
    app = system.create_application(application_id, "Migrate")
    revision = system.create_application_revision(app.id)
    system.create_object_type(revision.id, "migrate.Parent", name="Parent")
    system.create_object_type(revision.id, "migrate.Child", name="Child")
    relation_type = system.create_relation_type(
        revision.id,
        "migrate.parent_child",
        name="parent_child",
        from_object_type_id="migrate.Parent",
        to_object_type_id="migrate.Child",
    )
    physical = relation_type.physical_type
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/seed.py",
        action_source(
            "def run(ctx, input):\n"
            "    parent = upsert_object("
            "ctx, 'migrate.Parent', {'name': 'p'}, object_id='parent')\n"
            "    child = upsert_object("
            "ctx, 'migrate.Child', {'name': 'old'}, object_id='old-child')\n"
            f"    relation = merge_rel(ctx, '{physical}', "
            "'migrate.parent_child', parent['id'], child['id'], "
            f"relation_id='parent-child', relation_type_revision_id='{relation_type.id}')\n"
            "    return {'parent': parent['id'], 'child': child['id'], "
            "'relation': relation['id']}\n"
        ),
        role="action",
        language="python",
    )
    seed = system.create_action(
        revision.id,
        "migrate.seed",
        kind="normal",
        entrypoint="actions/seed.py:run",
        source_paths=["actions/seed.py"],
    )
    system.activate_application_revision(revision.id)
    invocation = system.run_active_action(app.id, seed.action_id, {})
    assert invocation.status == "succeeded", invocation.error
    return system, app, revision


def test_checkpoint_snapshot_adapter_contract(
    checkpoint_kernel: SystemRuntime,
) -> None:
    application_id = f"checkpoint-contract-{uuid.uuid4()}"
    system, app, revision = _create_seeded_graph_app(
        checkpoint_kernel, application_id=application_id
    )
    checkpoint = checkpoint_kernel.create_checkpoint(
        application_id,
        level="application_data",
        reason="adapter contract",
    )
    snapshot = checkpoint_kernel.uow.records.checkpoint_snapshots[
        checkpoint.snapshot_id
    ]
    blob = checkpoint_kernel.uow.records.checkpoint_blobs[snapshot.blob_id]
    assert checkpoint.level == "application_data"
    assert checkpoint.application_revision_id == revision.id
    assert snapshot.content_hash
    assert blob.encoding == "base64_json"
    parent = system.uow.records.objects[
        (app.id, "production", "parent")
    ]
    system.uow.records.objects.save(
        parent.model_copy(update={"values": {"name": "changed"}})
    )

    restarted = SystemRuntime(
        checkpoint_kernel.store, runtime_paths=RuntimePaths.temporary()
    )
    restarted.restore_checkpoint(checkpoint.id)

    assert restarted.uow.records.objects[
        (app.id, "production", "parent")
    ].values == {"name": "p"}
    assert restarted.inspect_application(app.id).active_revision_id == revision.id
    graph_shape = restarted.validate_graph_shape()
    assert graph_shape["ok"] is True, "\n".join(graph_shape["errors"])


def test_final_active_edge_replacement_rolls_back_as_one_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = create_test_runtime()
    app = system.create_application("atomic-active", "Atomic Active")
    first_revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        first_revision.source_tree_id,
        "actions/run.py",
        "def run(ctx, input):\n    return {'version': 1}\n",
        role="action",
        language="python",
    )
    first_action = system.create_action(
        first_revision.id,
        "atomic-active.run",
        kind="normal",
        entrypoint="actions/run.py:run",
        source_paths=["actions/run.py"],
    )
    system.activate_application_revision(first_revision.id)

    second_revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        second_revision.source_tree_id,
        "actions/run.py",
        "def run(ctx, input):\n    return {'version': 2}\n",
        role="action",
        language="python",
    )
    second_action = system.create_action(
        second_revision.id,
        "atomic-active.run",
        kind="normal",
        entrypoint="actions/run.py:run",
        source_paths=["actions/run.py"],
    )
    system.activate_application_revision(second_revision.id)

    original_replace = system.store.replace_single_edge

    def fail_action_edge(from_ref, edge_type, to_ref, *args, **kwargs):
        if from_ref.label == "Action" and edge_type == "ACTIVE_REVISION":
            raise RuntimeError("injected active-edge failure")
        return original_replace(from_ref, edge_type, to_ref, *args, **kwargs)

    monkeypatch.setattr(system.store, "replace_single_edge", fail_action_edge)

    with pytest.raises(RuntimeError, match="injected active-edge failure"):
        system.rollback_application(app.id, first_revision.id)

    assert system.inspect_application(app.id).active_revision_id == second_revision.id
    assert system.uow.records.revisions[first_revision.id].status == "superseded"
    assert system.uow.records.revisions[second_revision.id].status == "active"
    application_edges = system.store.list_edges(
        node_ref("Application", id=app.id), "ACTIVE_REVISION"
    )
    action_edges = system.store.list_edges(
        node_ref("Action", id=second_action.action_id), "ACTIVE_REVISION"
    )
    assert [edge.to_ref.identity["id"] for edge in application_edges] == [
        second_revision.id
    ]
    assert [edge.to_ref.identity["id"] for edge in action_edges] == [second_action.id]
    assert first_action.action_id == second_action.action_id


def test_revision_and_application_data_checkpoint_restore(
    checkpoint_kernel: SystemRuntime,
) -> None:
    system, app, revision = _create_seeded_graph_app(
        checkpoint_kernel,
        application_id=f"checkpoint-contract-{uuid.uuid4()}",
    )
    data_checkpoint = system.create_checkpoint(
        app.id, level="application_data", reason="before data change"
    )
    assert data_checkpoint.application_revision_id == revision.id
    snapshot = system.uow.records.checkpoint_snapshots[data_checkpoint.snapshot_id]
    assert snapshot.object_count == 2
    assert snapshot.relation_count == 1
    assert not system.uow.records.build_artifacts.values() or all(
        artifact.artifact_type != "checkpoint"
        for artifact in system.uow.records.build_artifacts.values()
    )

    objects = system.uow.records.objects
    objects.save(
        objects[(app.id, "production", "parent")].model_copy(
            update={"values": {"name": "changed"}}
        )
    )
    with system.uow:
        relation = system.uow.relations.get(app.id, "parent-child")
        assert relation is not None
        system.uow.relations.delete(relation)
    objects.delete((app.id, "production", "old-child"))

    system.restore_checkpoint(data_checkpoint.id)
    assert objects[(app.id, "production", "parent")].values == {"name": "p"}
    assert objects[(app.id, "production", "old-child")].values == {
        "name": "old"
    }
    restored_relation = system.list_application_relations(app.id)[0]
    assert restored_relation is not None
    assert restored_relation.to_object_id == "old-child"

    next_revision = system.create_application_revision(app.id)
    system.activate_application_revision(next_revision.id)
    revision_checkpoint = system.create_checkpoint(
        app.id, level="revision", reason="capture revision two"
    )
    system.rollback_application(app.id, revision.id)
    system.restore_checkpoint(revision_checkpoint.id)
    assert system.inspect_application(app.id).active_revision_id == next_revision.id


def test_migration_backfill_and_relation_rewiring(
    checkpoint_kernel: SystemRuntime,
) -> None:
    system, app, _ = _create_seeded_graph_app(
        checkpoint_kernel,
        application_id=f"checkpoint-contract-{uuid.uuid4()}",
    )
    revision = system.create_application_revision(app.id)
    rel_rev = next(
        item
        for item in system.uow.records.relation_type_revisions.values()
        if item.application_revision_id == revision.id
        and item.relation_type_id == "migrate.parent_child"
    )
    physical = rel_rev.physical_type
    system.source.write_source_file(
        revision.source_tree_id,
        "migrations/rewire.py",
        action_source(
            "def run(ctx, input):\n"
            "    parent = get_object(ctx, 'parent')\n"
            "    upsert_object(ctx, 'migrate.Parent', "
            "{**parent['values'], 'migrated': True}, object_id=parent['id'])\n"
            "    replacement = upsert_object("
            "ctx, 'migrate.Child', {'name': 'new'}, object_id='new-child')\n"
            f"    ctx.graph.run_cypher('''MATCH ()-[r:{physical} "
            "{{id: $id, application_id: $application_id}}]->() DELETE r''', "
            "{'id': 'parent-child', 'application_id': ctx.application_id, "
            "'data_space_id': ctx.data_space_id})\n"
            f"    merge_rel(ctx, '{physical}', 'migrate.parent_child', "
            "parent['id'], replacement['id'], relation_id='parent-child', "
            f"relation_type_revision_id='{rel_rev.id}')\n"
            "    return {'rewired_to': replacement['id']}\n"
        ),
        role="migration",
        language="python",
    )
    migration = system.create_action(
        revision.id,
        "migrate.rewire",
        kind="migration",
        entrypoint="migrations/rewire.py:run",
        source_paths=["migrations/rewire.py"],
        migration_metadata={
            "mutates_application_data": True,
            "affected_schema_revision_ids": [],
        },
    )
    activated = system.activate_application_revision(revision.id)
    assert activated.status == "active"
    assert system.uow.records.objects[
        (app.id, "production", "parent")
    ].values == {
        "name": "p",
        "migrated": True,
    }
    assert system.uow.records.objects[
        (app.id, "production", "new-child")
    ].values == {"name": "new"}
    relation = system.list_application_relations(app.id)[0]
    assert relation is not None
    assert relation.to_object_id == "new-child"

    checkpoints = [
        item
        for item in system.inspect_checkpoints(app.id)
        if item.level == "application_data"
    ]
    assert checkpoints
    migration_invocations = [
        item
        for item in system.inspect_invocations()
        if item.action_revision_id == migration.id
    ]
    assert {item.invocation_kind for item in migration_invocations} == {
        "migration",
    }


def test_failed_migration_restores_data_and_active_edges(
    checkpoint_kernel: SystemRuntime,
) -> None:
    system, app, active_revision = _create_seeded_graph_app(
        checkpoint_kernel,
        application_id=f"checkpoint-contract-{uuid.uuid4()}",
    )
    revision = system.create_application_revision(app.id)
    physical = next(
        item.physical_type
        for item in system.uow.records.relation_type_revisions.values()
        if item.application_revision_id == revision.id
        and item.relation_type_id == "migrate.parent_child"
    )
    system.source.write_source_file(
        revision.source_tree_id,
        "migrations/fail.py",
        action_source(
            "def run(ctx, input):\n"
            "    parent = get_object(ctx, 'parent')\n"
            "    upsert_object(ctx, 'migrate.Parent', "
            "{**parent['values'], 'should_not_stick': True}, "
            "object_id='parent')\n"
            f"    ctx.graph.run_cypher('''MATCH ()-[r:{physical} "
            "{{id: $id, application_id: $application_id}}]->() DELETE r''', "
            "{'id': 'parent-child', 'application_id': ctx.application_id, "
            "'data_space_id': ctx.data_space_id})\n"
            "    raise RuntimeError('migration failed')\n"
        ),
        role="migration",
        language="python",
    )
    system.create_action(
        revision.id,
        "migrate.fail",
        kind="migration",
        entrypoint="migrations/fail.py:run",
        source_paths=["migrations/fail.py"],
        migration_metadata={
            "mutates_application_data": True,
            "affected_schema_revision_ids": [],
        },
    )

    with pytest.raises(ValidationFailure):
        system.activate_application_revision(revision.id)

    assert system.inspect_application(app.id).active_revision_id == active_revision.id
    assert system.uow.records.objects[
        (app.id, "production", "parent")
    ].values == {"name": "p"}
    relation = system.list_application_relations(app.id)[0]
    assert relation is not None
    assert relation.from_object_id == "parent"
    assert relation.to_object_id == "old-child"
    assert system.uow.records.revisions[revision.id].status == "rejected"
