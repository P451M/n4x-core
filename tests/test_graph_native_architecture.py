from __future__ import annotations

import pytest

from n4x.graph.store import node_ref
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import ApplicationObject
from n4x.kernel.intern import source_content_id
from n4x.testing import InMemoryGraphStore, create_test_runtime, tree_id
from tests.cypher_source import action_source


def test_runtime_creates_explicit_edges_without_legacy_sync_relationships() -> None:
    system = create_test_runtime()
    assert isinstance(system.store, InMemoryGraphStore)

    app = system.create_application("graph", "Graph")
    revision = system.create_application_revision(app.id)
    source = system.source.write_source_file(
        revision.id,
        "actions/run.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    object_type = system.create_object_type(revision.id, "graph.Item", name="Item")
    action = system.create_action(
        revision.id,
        "graph.run",
        kind="normal",
        entrypoint="actions/run.py:run",
        source_paths=[source.path],
    )
    activated = system.activate_application_revision(revision.id)

    store = system.store
    assert isinstance(store, InMemoryGraphStore)
    assert store.has_edge(
        node_ref("N4XRoot", id="n4x"),
        "HAS_APPLICATION",
        node_ref("Application", id=app.id),
    )
    assert store.has_edge(
        node_ref("Application", id=app.id),
        "HAS_REVISION",
        node_ref("ApplicationRevision", id=revision.id),
    )
    interned_tree = tree_id(system, activated)
    assert store.has_edge(
        node_ref("ApplicationRevision", id=revision.id),
        "HAS_SOURCE_TREE",
        node_ref("SourceTree", id=interned_tree),
    )
    assert store.has_edge(
        node_ref("SourceTree", id=interned_tree),
        "HAS_FILE",
        node_ref("SourceContent", id=source_content_id(source.content)),
    )
    assert store.has_edge(
        node_ref("Application", id=app.id),
        "DEFINES_OBJECT_TYPE",
        node_ref("ObjectType", id=object_type.object_type_id),
    )
    assert store.has_edge(
        node_ref("ApplicationRevision", id=revision.id),
        "HAS_ACTION_REVISION",
        node_ref("ActionRevision", id=action.id),
    )
    assert store.has_edge(
        node_ref("Application", id=app.id),
        "ACTIVE_REVISION",
        node_ref("ApplicationRevision", id=revision.id),
    )
    assert store.edge_count("N4X_KERNEL") == 0
    assert store.edge_count("N4X_RELATION") == 0
    assert system.validate_graph_shape()["ok"] is True


def test_app_relations_use_generated_physical_type_and_validate_endpoint_types() -> (
    None
):
    system = create_test_runtime()
    app = system.create_application("graph-rel", "Graph Relations")
    revision = system.create_application_revision(app.id)
    left_type = system.create_object_type(revision.id, "graph-rel.Left", name="Left")
    right_type = system.create_object_type(revision.id, "graph-rel.Right", name="Right")
    wrong_type = system.create_object_type(revision.id, "graph-rel.Wrong", name="Wrong")
    relation_type = system.create_relation_type(
        revision.id,
        "graph-rel.left_right",
        name="left_right",
        from_object_type_id=left_type.object_type_id,
        to_object_type_id=right_type.object_type_id,
    )
    system.activate_application_revision(revision.id)
    left = ApplicationObject(
        id="left-1",
        application_id=app.id,
        object_type_id=left_type.object_type_id,
    )
    right = ApplicationObject(
        id="right-1",
        application_id=app.id,
        object_type_id=right_type.object_type_id,
    )
    wrong = ApplicationObject(
        id="wrong-1",
        application_id=app.id,
        object_type_id=wrong_type.object_type_id,
    )
    with system.uow:
        system.uow.objects.save(left)
        system.uow.objects.save(right)
        system.uow.objects.save(wrong)

    relation = system.create_application_relation(
        app.id, relation_type.relation_type_id, left.id, right.id
    )

    assert relation.physical_type is not None
    assert relation.physical_type.startswith("APP_REL_GRAPH_REL_LEFT_RIGHT_")
    assert relation.relation_type_revision_id == relation_type.id
    assert isinstance(system.store, InMemoryGraphStore)
    assert system.store.has_edge(
        node_ref(
            "ApplicationObject",
            application_id=app.id,
            data_space_id="production",
            id=left.id,
        ),
        relation.physical_type,
        node_ref(
            "ApplicationObject",
            application_id=app.id,
            data_space_id="production",
            id=right.id,
        ),
    )

    with pytest.raises(ValidationFailure):
        system.create_application_relation(
            app.id, relation_type.relation_type_id, left.id, wrong.id
        )


def test_revision_clone_and_rollback_update_structural_edges() -> None:
    system = create_test_runtime()
    app = system.create_application("clone-graph", "Clone Graph")
    rev1 = system.create_application_revision(app.id)
    system.source.write_source_file(
        rev1.id,
        "actions/run.py",
        "def run(ctx, input):\n    return {'version': 1}\n",
        role="action",
        language="python",
    )
    object_type_v1 = system.create_object_type(rev1.id, "clone-graph.Item", name="Item")
    action_v1 = system.create_action(
        rev1.id,
        "clone-graph.run",
        kind="normal",
        entrypoint="actions/run.py:run",
        source_paths=["actions/run.py"],
    )
    active_v1 = system.activate_application_revision(rev1.id)

    rev2 = system.create_application_revision(app.id)
    cloned_action = system.source.bindings.action_revision(rev2.id, "clone-graph.run")
    cloned_object_type = next(
        revision
        for revision in system.source.bindings.object_type_revisions(rev2.id)
        if revision.object_type_id == "clone-graph.Item"
    )

    assert isinstance(system.store, InMemoryGraphStore)
    assert system.store.has_edge(
        node_ref("Action", id="clone-graph.run"),
        "HAS_REVISION",
        node_ref("ActionRevision", id=cloned_action.id),
    )
    assert system.store.has_edge(
        node_ref("ApplicationRevision", id=rev2.id),
        "HAS_ACTION_REVISION",
        node_ref("ActionRevision", id=cloned_action.id),
    )
    assert system.store.has_edge(
        node_ref("ObjectType", id=object_type_v1.object_type_id),
        "HAS_REVISION",
        node_ref("ObjectTypeRevision", id=cloned_object_type.id),
    )

    system.source.write_source_file(
        rev2.id,
        "actions/run.py",
        "def run(ctx, input):\n    return {'version': 2}\n",
        role="action",
        language="python",
    )
    action_v2 = system.create_action(
        rev2.id,
        "clone-graph.run",
        kind="normal",
        entrypoint="actions/run.py:run",
        source_paths=["actions/run.py"],
    )
    active_v2 = system.activate_application_revision(rev2.id)
    assert active_v2.status == "active"
    assert system.store.has_edge(
        node_ref("Action", id="clone-graph.run"),
        "ACTIVE_REVISION",
        node_ref("ActionRevision", id=action_v2.id),
    )

    system.rollback_application(app.id, active_v1.id)

    assert system.store.has_edge(
        node_ref("Application", id=app.id),
        "ACTIVE_REVISION",
        node_ref("ApplicationRevision", id=active_v1.id),
    )
    assert system.store.has_edge(
        node_ref("Action", id="clone-graph.run"),
        "ACTIVE_REVISION",
        node_ref("ActionRevision", id=action_v1.id),
    )
    assert system.store.has_edge(
        node_ref("ObjectType", id=object_type_v1.object_type_id),
        "ACTIVE_REVISION",
        node_ref("ObjectTypeRevision", id=object_type_v1.id),
    )


def test_existing_objects_keep_conformance_revision_after_new_activation() -> None:
    system = create_test_runtime()
    app = system.create_application("schema-evolution", "Schema Evolution")
    rev1 = system.create_application_revision(app.id)
    object_type_v1 = system.create_object_type(
        rev1.id, "schema-evolution.Item", name="Item"
    )
    system.source.write_source_file(
        rev1.id,
        "actions/create.py",
        action_source(
            "def run(ctx, input):\n"
            "    return upsert_object(ctx, 'schema-evolution.Item', "
            "{'name': input['name']})\n"
        ),
        role="action",
        language="python",
    )
    system.create_action(
        rev1.id,
        "schema-evolution.create",
        kind="normal",
        entrypoint="actions/create.py:run",
        source_paths=["actions/create.py"],
    )
    system.activate_application_revision(rev1.id)
    obj = ApplicationObject(
        id="created-under-v1",
        application_id=app.id,
        object_type_id=object_type_v1.object_type_id,
        object_type_revision_id=object_type_v1.id,
        values={"name": "created under v1"},
    )
    with system.uow:
        system.uow.objects.save(obj)
        system.uow.objects.attach(obj)
    assert system.store.has_edge(
        node_ref(
            "ApplicationObject",
            application_id=app.id,
            data_space_id="production",
            id=obj.id,
        ),
        "CONFORMS_TO",
        node_ref("ObjectTypeRevision", id=object_type_v1.id),
    )

    rev2 = system.create_application_revision(app.id)
    object_type_v2 = next(
        revision
        for revision in system.source.bindings.object_type_revisions(rev2.id)
        if revision.object_type_id == object_type_v1.object_type_id
    )
    system.activate_application_revision(rev2.id)

    assert object_type_v2.id == object_type_v1.id
    assert system.validate_graph_shape()["ok"] is True
    assert system.store.has_edge(
        node_ref(
            "ApplicationObject",
            application_id=app.id,
            data_space_id="production",
            id=obj.id,
        ),
        "CONFORMS_TO",
        node_ref("ObjectTypeRevision", id=object_type_v1.id),
    )


def test_admin_repair_recreates_missing_structural_edges() -> None:
    system = create_test_runtime()
    app = system.create_application("repair-graph", "Repair Graph")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.id,
        "actions/run.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "repair-graph.run",
        kind="normal",
        entrypoint="actions/run.py:run",
        source_paths=["actions/run.py"],
    )
    system.activate_application_revision(revision.id)
    assert isinstance(system.store, InMemoryGraphStore)

    system.store.delete_edge(
        node_ref("Action", id=action.action_id),
        "ACTIVE_REVISION",
    )

    damaged = system.validate_graph_shape()
    assert damaged["ok"] is False
    assert any("ACTIVE_REVISION" in error for error in damaged["errors"])

    repaired = system.repair_graph_edges()

    assert repaired["ok"] is True
    assert system.store.has_edge(
        node_ref("Action", id=action.action_id),
        "ACTIVE_REVISION",
        node_ref("ActionRevision", id=action.id),
    )
