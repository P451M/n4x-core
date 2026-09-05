from __future__ import annotations

import time

import pytest

from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import ApplicationObject, ExecutionContext
from n4x.testing import create_test_runtime
from tests.cypher_source import action_source


def test_application_gets_production_and_can_create_development_data_space() -> None:
    system = create_test_runtime()
    system.create_application("spaces", "Spaces")

    production = system.data_spaces.list("spaces")
    development = system.create_development_data_space(
        "spaces", data_space_id="preview"
    )

    assert [(item.id, item.kind) for item in production] == [
        ("production", "production")
    ]
    assert development.kind == "development"
    assert {
        (item.application_id, item.id, item.kind)
        for item in system.data_spaces.list("spaces")
    } == {
        ("spaces", "production", "production"),
        ("spaces", "preview", "development"),
    }


def test_objects_and_relations_allow_same_logical_ids_in_isolated_data_spaces() -> None:
    system = create_test_runtime()
    app = system.create_application("isolated", "Isolated")
    system.create_development_data_space(app.id, data_space_id="preview")
    revision = system.create_application_revision(app.id)
    left_type = system.create_object_type(
        revision.id, "isolated.Left", name="Left"
    )
    right_type = system.create_object_type(
        revision.id, "isolated.Right", name="Right"
    )
    relation_type = system.create_relation_type(
        revision.id,
        "isolated.left_right",
        name="left_right",
        from_object_type_id=left_type.object_type_id,
        to_object_type_id=right_type.object_type_id,
    )

    with system.uow:
        for data_space_id, marker in (
            ("production", "live"),
            ("preview", "draft"),
        ):
            left = ApplicationObject(
                id="same-left",
                application_id=app.id,
                data_space_id=data_space_id,
                object_type_id=left_type.object_type_id,
                object_type_revision_id=left_type.id,
                values={"marker": marker},
            )
            right = ApplicationObject(
                id="same-right",
                application_id=app.id,
                data_space_id=data_space_id,
                object_type_id=right_type.object_type_id,
                object_type_revision_id=right_type.id,
                values={"marker": marker},
            )
            system.uow.objects.save(left)
            system.uow.objects.attach(left)
            system.uow.objects.save(right)
            system.uow.objects.attach(right)

    for data_space_id in ("production", "preview"):
        relation = system.relation_service.create(
            app.id,
            relation_type.relation_type_id,
            "same-left",
            "same-right",
            relation_id="same-edge",
            data_space_id=data_space_id,
        )
        assert relation.data_space_id == data_space_id

    production = system.object_service.list(app.id)
    preview = system.object_service.list(
        app.id, data_space_id="preview"
    )
    assert {item.values["marker"] for item in production} == {"live"}
    assert {item.values["marker"] for item in preview} == {"draft"}
    assert system.relation_service.list(app.id)[0].data_space_id == "production"
    assert (
        system.relation_service.list(
            app.id, data_space_id="preview"
        )[0].data_space_id
        == "preview"
    )
    report = system.validate_graph_shape()
    assert report["ok"] is True, report["errors"]


def test_action_execution_context_reads_and_commits_only_bound_data_space() -> None:
    system = create_test_runtime()
    app = system.create_application("context", "Context")
    system.create_development_data_space(app.id, data_space_id="preview")
    revision = system.create_application_revision(app.id)
    object_type = system.create_object_type(
        revision.id, "context.Item", name="Item"
    )
    system.source.write_source_file(
        revision.id,
        "actions/create.py",
        action_source(
            "def run(ctx, input):\n"
            "    before = len(list_objects(ctx))\n"
            "    created = upsert_object(\n"
            "        ctx, 'context.Item', {'scope': input['scope']}, "
            "object_id='same-id')\n"
            "    return {'before': before, 'id': created['id']}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "context.create",
        kind="normal",
        entrypoint="actions/create.py:run",
        source_paths=["actions/create.py"],
    )
    production = ApplicationObject(
        id="same-id",
        application_id=app.id,
        object_type_id=object_type.object_type_id,
        object_type_revision_id=object_type.id,
        values={"scope": "production"},
    )
    with system.uow:
        system.uow.objects.save(production)
        system.uow.objects.attach(production)

    invocation = system.action_supervisor.run(
        action,
        {"scope": "preview"},
        execution_context=ExecutionContext(
            mode="development",
            deployment_id="deployment-1",
            correlation_id="correlation-1",
            application_revision_id=revision.id,
            application_id=app.id,
            data_space_id="preview",
        ),
    )

    assert invocation.status == "succeeded", invocation.error
    assert invocation.output == {"before": 0, "id": "same-id"}
    assert invocation.data_space_id == "preview"
    assert invocation.deployment_id == "deployment-1"
    assert invocation.correlation_id == "correlation-1"
    assert system.uow.objects.get(
        app.id, "same-id", "production"
    ).values == {"scope": "production"}
    assert system.uow.objects.get(
        app.id, "same-id", "preview"
    ).values == {"scope": "preview"}


def test_development_deployment_binds_candidates_and_detects_source_drift() -> None:
    system = create_test_runtime()
    app = system.create_application("deployed", "Deployed")
    revision = system.create_application_revision(app.id)
    system.create_object_type(revision.id, "deployed.Item", name="Item")
    system.source.write_source_file(
        revision.id,
        "actions/count.py",
        action_source(
            "def run(ctx, input):\n"
            "    before = len(list_objects(ctx))\n"
            "    upsert_object(ctx, 'deployed.Item', {'before': before})\n"
            "    return {'before': before}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "deployed.count",
        kind="normal",
        entrypoint="actions/count.py:run",
        source_paths=["actions/count.py"],
    )
    system.create_experience("deployed-ui", "Deployed UI")
    experience_revision = system.create_experience_revision(
        "deployed-ui",
        application_access=[{"application_id": app.id}],
    )

    deployment = system.create_development_deployment(
        experience_revision.id,
        {app.id: revision.id},
    )
    first = system.run_development_action(
        deployment.id, app.id, action.action_id, {}
    )
    second = system.run_development_action(
        deployment.id, app.id, action.action_id, {}
    )

    assert first.output == {"before": 0}
    assert second.output == {"before": 1}
    assert first.data_space_id == deployment.data_space_ids[app.id]
    assert system.object_service.list(app.id) == []
    assert len(
        system.object_service.list(
            app.id,
            data_space_id=deployment.data_space_ids[app.id],
        )
    ) == 2

    system.source.write_source_file(
        revision.id,
        "README.md",
        "changed after deployment",
        role="helper",
        language="markdown",
    )
    with pytest.raises(ValidationFailure, match="redeploy required"):
        system.run_development_action(
            deployment.id, app.id, action.action_id, {}
        )


def test_development_deployment_can_bounded_clone_production_subgraph() -> None:
    system = create_test_runtime()
    app = system.create_application("clone", "Clone")
    revision = system.create_application_revision(app.id)
    item_type = system.create_object_type(
        revision.id, "clone.Item", name="Item"
    )
    relation_type = system.create_relation_type(
        revision.id,
        "clone.link",
        name="link",
        from_object_type_id=item_type.object_type_id,
        to_object_type_id=item_type.object_type_id,
    )
    with system.uow:
        for index in range(3):
            item = ApplicationObject(
                id=f"item-{index}",
                application_id=app.id,
                object_type_id=item_type.object_type_id,
                object_type_revision_id=item_type.id,
                values={"index": index},
            )
            system.uow.objects.save(item)
            system.uow.objects.attach(item)
    system.relation_service.create(
        app.id,
        relation_type.relation_type_id,
        "item-1",
        "item-2",
        relation_id="selected-link",
    )
    system.create_experience("clone-ui", "Clone UI")
    experience_revision = system.create_experience_revision(
        "clone-ui",
        application_access=[{"application_id": app.id}],
    )

    deployment = system.create_development_deployment(
        experience_revision.id,
        {app.id: revision.id},
        initialization="clone",
        clone_specs={
            app.id: {"max_nodes": 2, "max_relations": 1}
        },
    )
    data_space_id = deployment.data_space_ids[app.id]

    cloned = system.object_service.list(
        app.id, data_space_id=data_space_id
    )
    relations = system.relation_service.list(
        app.id, data_space_id=data_space_id
    )
    assert {item.id for item in cloned} == {"item-1", "item-2"}
    assert [relation.id for relation in relations] == ["selected-link"]
    assert len(system.object_service.list(app.id)) == 3
    scope_key = system.runtime.paths.application_data_scope_key(
        app.id, data_space_id
    )
    volume = system.runtime.paths.application_data_root(scope_key)
    (volume / "temporary.txt").write_text("preview", encoding="utf-8")

    expired = system.expire_development_deployment(deployment.id)

    assert expired.status == "expired"
    assert not volume.exists()
    assert system.graph.data_spaces.get((app.id, data_space_id)) is None
    assert (
        system.object_service.list(
            app.id, data_space_id=data_space_id
        )
        == []
    )
    with pytest.raises(ValidationFailure, match="expired"):
        system.run_development_action(
            deployment.id, app.id, "missing", {}
        )


def test_development_cypher_audit_carries_execution_context() -> None:
    system = create_test_runtime()
    app = system.create_application("audit-context", "Audit Context")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.id,
        "actions/read.py",
        (
            "def run(ctx, input):\n"
            "    return ctx.graph.run_app_cypher_read('RETURN 1 AS ok')\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "audit-context.read",
        kind="normal",
        entrypoint="actions/read.py:run",
        source_paths=["actions/read.py"],
    )
    system.create_experience("audit-context-ui", "Audit Context UI")
    experience_revision = system.create_experience_revision(
        "audit-context-ui",
        application_access=[{"application_id": app.id}],
    )
    deployment = system.create_development_deployment(
        experience_revision.id,
        {app.id: revision.id},
    )

    invocation = system.run_development_action(
        deployment.id, app.id, action.action_id, {}
    )
    audits = system.queries.cypher_audits(invocation.id)

    assert invocation.status == "succeeded", invocation.error
    assert len(audits) == 1
    assert audits[0].data_space_id == deployment.data_space_ids[app.id]
    assert audits[0].deployment_id == deployment.id
    assert audits[0].correlation_id == invocation.correlation_id


def test_expiring_deployment_cancels_running_action_before_purge() -> None:
    system = create_test_runtime()
    app = system.create_application("expire-running", "Expire Running")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.id,
        "actions/wait.py",
        (
            "import time\n\n"
            "def run(ctx, input):\n"
            "    print('running', flush=True)\n"
            "    time.sleep(10)\n"
            "    return {'late': True}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "expire-running.wait",
        kind="normal",
        entrypoint="actions/wait.py:run",
        source_paths=["actions/wait.py"],
    )
    system.create_experience("expire-running-ui", "Expire Running UI")
    experience_revision = system.create_experience_revision(
        "expire-running-ui",
        application_access=[{"application_id": app.id}],
    )
    deployment = system.create_development_deployment(
        experience_revision.id,
        {app.id: revision.id},
    )
    queued = system.submit_development_action(
        deployment.id, app.id, action.action_id, {}
    )
    deadline = time.monotonic() + 3
    while system.action_supervisor.inspect(queued.id).status == "queued":
        assert time.monotonic() < deadline
        time.sleep(0.01)

    system.expire_development_deployment(deployment.id)
    cancelled = system.action_supervisor.inspect(queued.id)

    assert cancelled.status == "cancelled"
    assert system.graph.data_spaces.get(
        (app.id, deployment.data_space_ids[app.id])
    ) is None
