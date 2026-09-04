from __future__ import annotations

from n4x.testing import InMemoryGraphStore, create_test_runtime
from tests.cypher_source import action_source


def test_actions_can_create_list_and_delete_application_relations() -> None:
    system = create_test_runtime()
    app = system.create_application("projects", "Projects")
    revision = system.create_application_revision(app.id)
    project_type = system.create_object_type(
        revision.id,
        "projects.Project",
        name="Project",
        properties={"title": {"type": "string"}},
    )
    task_type = system.create_object_type(
        revision.id,
        "projects.Task",
        name="Task",
        properties={"title": {"type": "string"}},
    )
    relation_type = system.create_relation_type(
        revision.id,
        "projects.project_task",
        name="project_task",
        from_object_type_id=project_type.object_type_id,
        to_object_type_id=task_type.object_type_id,
    )
    physical = relation_type.physical_type
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/projects.py",
        action_source(
            "def create_project_task(ctx, input):\n"
            "    project = upsert_object(ctx, 'projects.Project', "
            "{'title': input['project']})\n"
            "    task = upsert_object(ctx, 'projects.Task', "
            "{'title': input['task']})\n"
            f"    relation = merge_rel(ctx, '{physical}', "
            "'projects.project_task', project['id'], task['id'], {'rank': 1})\n"
            "    return {'project_id': project['id'], 'task_id': task['id'], "
            "'relation_id': relation['id']}\n",
            "def delete_task(ctx, input):\n"
            "    delete_object(ctx, input['task_id'])\n"
            "    return {'relations': list_rels(ctx, 'projects.project_task')}\n",
        ),
        role="action",
        language="python",
    )
    create_action = system.create_action(
        revision.id,
        "projects.create_project_task",
        kind="normal",
        entrypoint="actions/projects.py:create_project_task",
        source_paths=["actions/projects.py"],
    )
    delete_action = system.create_action(
        revision.id,
        "projects.delete_task",
        kind="normal",
        entrypoint="actions/projects.py:delete_task",
        source_paths=["actions/projects.py"],
    )

    created = system.run_draft_action(
        create_action.id, {"project": "Launch", "task": "Ship"}
    )
    relations = system.list_application_relations(
        app.id, relation_type.relation_type_id
    )

    assert created.status == "succeeded", created.error
    assert len(relations) == 1
    assert relations[0].from_object_id == created.output["project_id"]
    assert relations[0].to_object_id == created.output["task_id"]
    assert relations[0].values == {"rank": 1}

    deleted = system.run_draft_action(
        delete_action.id, {"task_id": created.output["task_id"]}
    )

    assert deleted.status == "succeeded", deleted.error
    assert system.list_application_relations(app.id) == []


def test_app_relations_preserve_multiple_edges_between_same_objects() -> None:
    system = create_test_runtime()
    app = system.create_application("multi-rel", "Multiple Relations")
    revision = system.create_application_revision(app.id)
    left_type = system.create_object_type(revision.id, "multi-rel.Left", name="Left")
    right_type = system.create_object_type(revision.id, "multi-rel.Right", name="Right")
    relation_type = system.create_relation_type(
        revision.id,
        "multi-rel.left_right",
        name="left_right",
        from_object_type_id=left_type.object_type_id,
        to_object_type_id=right_type.object_type_id,
    )
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/create_pair.py",
        action_source(
            "def run(ctx, input):\n"
            "    left = upsert_object(ctx, 'multi-rel.Left', {'name': 'left'})\n"
            "    right = upsert_object(ctx, 'multi-rel.Right', {'name': 'right'})\n"
            "    return {'left_id': left['id'], 'right_id': right['id']}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "multi-rel.create_pair",
        kind="normal",
        entrypoint="actions/create_pair.py:run",
        source_paths=["actions/create_pair.py"],
    )
    created = system.run_draft_action(action.id, {})
    assert created.status == "succeeded", created.error

    first = system.create_application_relation(
        app.id,
        relation_type.relation_type_id,
        created.output["left_id"],
        created.output["right_id"],
        relation_id="edge-1",
        values={"rank": 1},
    )
    second = system.create_application_relation(
        app.id,
        relation_type.relation_type_id,
        created.output["left_id"],
        created.output["right_id"],
        relation_id="edge-2",
        values={"rank": 2},
    )

    assert system.list_application_relations(app.id) == [first, second]
    assert isinstance(system.store, InMemoryGraphStore)
    assert (
        len(
            [
                edge
                for edge in system.store.edges
                if edge.type == relation_type.physical_type
                and edge.props.get("id") in {"edge-1", "edge-2"}
            ]
        )
        == 2
    )
