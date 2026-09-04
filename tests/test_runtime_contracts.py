from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from n4x.contracts import (
    ACTION_CONTEXT_VERSION,
    ACTION_SUPERVISOR_VERSION,
    CALLBACK_CONTRACT_VERSION,
    EXPERIENCE_BRIDGE_VERSION,
    EXECUTION_CONTEXT_VERSION,
    FILE_DELIVERY_CONTRACT_VERSION,
    GRAPH_METAMODEL_SCHEMA_FINGERPRINT,
    GRAPH_METAMODEL_VERSION,
    MCP_AUTHORING_SCHEMA,
    MCP_AUTHORING_VERSION,
    PACKAGE_FORMAT_VERSION,
    PACKAGE_SCHEMA_FINGERPRINT,
    SUBPROCESS_PROTOCOL_VERSION,
)
from n4x.graph.neo4j import Neo4jGraph
from n4x.graph.store import Neo4jGraphStore
from n4x.kernel.errors import GraphUnitOfWorkError
from n4x.kernel.hash import sha256_json
from n4x.kernel.models import ApplicationObject, now_utc
from n4x.system.runtime import SystemRuntime
from n4x.runtime.actions import RuntimePaths
from n4x.testing import create_test_runtime
from tests.cypher_source import action_source


@pytest.fixture(params=["memory", pytest.param("neo4j", marks=pytest.mark.neo4j)])
def restart_kernel(request: pytest.FixtureRequest) -> Iterator[SystemRuntime]:
    if request.param == "memory":
        yield create_test_runtime()
        return
    graph: Neo4jGraph = request.getfixturevalue("neo4j_graph")
    system = SystemRuntime(Neo4jGraphStore(graph), runtime_paths=RuntimePaths.temporary())
    try:
        yield system
    finally:
        graph.run_cypher(
            """
            MATCH (app:Application)
            WHERE app.id STARTS WITH 'runtime-contract-'
            OPTIONAL MATCH (app)-[*0..]->(owned)
            WITH collect(DISTINCT owned) AS owned
            UNWIND owned AS node
            DETACH DELETE node
            """
        )
        graph.run_cypher(
            """
            MATCH (probe:CypherProbe)
            WHERE probe.id STARTS WITH 'runtime-contract-'
            DETACH DELETE probe
            """
        )


@pytest.mark.contract
def test_contract_versions_are_stable() -> None:
    assert ACTION_CONTEXT_VERSION == "n4x.action.context.v2"
    assert ACTION_SUPERVISOR_VERSION == "n4x.action.supervisor.v1"
    assert SUBPROCESS_PROTOCOL_VERSION == "n4x.action.subprocess.v3"
    assert EXPERIENCE_BRIDGE_VERSION == "n4x.experience.bridge.v1"
    assert EXECUTION_CONTEXT_VERSION == "n4x.execution.context.v1"
    assert FILE_DELIVERY_CONTRACT_VERSION == "n4x.file.delivery.v1"
    assert MCP_AUTHORING_VERSION == "n4x.mcp.authoring.v8"
    assert CALLBACK_CONTRACT_VERSION == "n4x.callback.v1"
    assert PACKAGE_FORMAT_VERSION == "n4x.package.v2"
    assert PACKAGE_SCHEMA_FINGERPRINT.startswith("sha256:")
    assert GRAPH_METAMODEL_VERSION == "n4x.graph.metamodel.v5"
    assert (
        sha256_json(Neo4jGraph.schema_statements())
        == GRAPH_METAMODEL_SCHEMA_FINGERPRINT
    )
    assert MCP_AUTHORING_SCHEMA["tool_catalog"]["complete"] is False
    assert MCP_AUTHORING_SCHEMA["client_guide"]["tool"] == "inspect_client_guide"
    assert MCP_AUTHORING_SCHEMA["payloads"]["inspect_client_guide"] == ["topic"]
    assert MCP_AUTHORING_SCHEMA["payloads"]["create_experience_surface"][0] == (
        "experience_revision_id"
    )
    assert MCP_AUTHORING_SCHEMA["ownership"]["experience_revision"][0] == "ui_profile"


def test_activation_rejects_raw_graph_access_without_gateway() -> None:
    system = create_test_runtime()
    system.activation_service.cypher_gateway = None
    app = system.create_application("cypher-app", "Cypher App")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/probe.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    system.create_action(
        revision.id,
        "cypher-app.probe",
        kind="normal",
        entrypoint="actions/probe.py:run",
        source_paths=["actions/probe.py"],
    )
    report = system.validate_application_revision(revision.id)
    assert report.status == "failed"
    assert any("Cypher gateway is unavailable" in error for error in report.errors)
    activated = system.activate_application_revision(revision.id)
    assert activated.status == "active"


def test_fat_existing_nodes_do_not_put_object_json_on_the_child_env() -> None:
    system = create_test_runtime()
    app = system.create_application("snapshot-none", "Snapshot None")
    revision = system.create_application_revision(app.id)
    system.create_object_type(revision.id, "snapshot-none.Item", name="Item")
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/create.py",
        action_source(
            "def run(ctx, input):\n"
            "    import os\n"
            "    created = upsert_object("
            "ctx, 'snapshot-none.Item', {'created': True}, "
            "object_id='created-none')\n"
            "    return {\n"
            "        'id': created['id'],\n"
            "        'has_objects_env': 'N4X_ACTION_OBJECTS' in os.environ,\n"
            "        'has_input_env': 'N4X_ACTION_INPUT' in os.environ,\n"
            "        'has_input_path': bool(os.environ.get('N4X_ACTION_INPUT_PATH')),\n"
            "    }\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "snapshot-none.create",
        kind="normal",
        entrypoint="actions/create.py:run",
        source_paths=["actions/create.py"],
    )
    system.activate_application_revision(revision.id)
    object_type = system.graph.object_types["snapshot-none.Item"]
    with system.uow:
        for index in range(10_000):
            system.graph.objects.save(
                ApplicationObject(
                    id=f"snapshot-none-{index}",
                    application_id=app.id,
                    object_type_id=object_type.id,
                    object_type_revision_id=object_type.active_revision_id,
                    values={"index": index},
                )
            )

    invocation = system.run_active_action(app.id, action.action_id, {})

    assert invocation.status == "succeeded", invocation.error
    assert invocation.output["id"] == "created-none"
    assert invocation.output["has_objects_env"] is False
    assert invocation.output["has_input_env"] is False
    assert invocation.output["has_input_path"] is False
    assert system.graph.objects[
        (app.id, "production", "created-none")
    ].values == {"created": True}


def test_every_action_receives_the_cypher_gateway() -> None:
    system = create_test_runtime()
    app = system.create_application("cypher-always", "Cypher Always")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
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
        "cypher-always.read",
        kind="normal",
        entrypoint="actions/read.py:run",
        source_paths=["actions/read.py"],
    )
    system.activate_application_revision(revision.id)

    invocation = system.run_active_action(app.id, action.action_id, {})

    assert invocation.status == "succeeded", invocation.error
    assert invocation.output == [{"ok": 1}]


def test_cypher_gateway_audits_read_and_write_queries(
    restart_kernel: SystemRuntime,
) -> None:
    system = restart_kernel
    application_id = f"runtime-contract-cypher-{uuid.uuid4()}"
    app = system.create_application(application_id, "Cypher OK")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/probe.py",
        (
            "def run(ctx, input):\n"
            "    written = ctx.graph.run_app_cypher_write(\n"
            "        'CREATE (n:CypherProbe {id: $id}) "
            "SET n.marker = $marker RETURN n.id AS id, n.marker AS marker',\n"
            "        {'id': input['id'], 'marker': input['marker']},\n"
            "    )\n"
            "    read = ctx.graph.run_app_cypher_read(\n"
            "        'MATCH (n:CypherProbe {id: $id}) RETURN n.marker AS marker',\n"
            "        {'id': input['id']},\n"
            "    )\n"
            "    return {'written': written, 'read': read}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        f"{application_id}.probe",
        kind="normal",
        entrypoint="actions/probe.py:run",
        source_paths=["actions/probe.py"],
        input_schema={"type": "object", "required": ["id", "marker"]},
    )
    system.activate_application_revision(revision.id)
    invocation = system.run_active_action(
        app.id,
        f"{application_id}.probe",
        {"id": f"{application_id}-probe", "marker": "live"},
    )
    audits = system.queries.cypher_audits(invocation.id)

    assert invocation.status == "succeeded", invocation.error
    assert invocation.output["written"][0]["marker"] == "live"
    assert invocation.output["read"][0]["marker"] == "live"
    assert [audit.mode for audit in audits] == ["write", "read"]
    assert all(audit.query_hash.startswith("sha256:") for audit in audits)
    assert all(audit.params_hash.startswith("sha256:") for audit in audits)


def test_action_rejects_inherited_uow_before_preparation() -> None:
    system = create_test_runtime()
    app = system.create_application("uow-action", "UoW Action")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/run.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "uow-action.run",
        kind="normal",
        entrypoint="actions/run.py:run",
        source_paths=["actions/run.py"],
    )

    with system.uow:
        with pytest.raises(
            GraphUnitOfWorkError, match="run action requires an inactive"
        ):
            system.runtime.run(action, {}, invocation_kind="draft")
        assert system.uow.is_active is True

    assert not any(
        artifact.metadata.get("action_revision_id") == action.id
        for artifact in system.uow.records.build_artifacts.values()
    )


def test_failed_action_rolls_back_an_open_cypher_transaction() -> None:
    system = create_test_runtime()
    app = system.create_application("buffer-fail", "Buffer Fail")
    revision = system.create_application_revision(app.id)
    system.create_object_type(revision.id, "buffer-fail.Item", name="Item")
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/fail.py",
        action_source(
            "def run(ctx, input):\n"
            "    with ctx.graph.transaction():\n"
            "        upsert_object("
            "ctx, 'buffer-fail.Item', {'name': 'transient'}, "
            "object_id='buffered-object')\n"
            "        raise RuntimeError('after buffering')\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "buffer-fail.run",
        kind="normal",
        entrypoint="actions/fail.py:run",
        source_paths=["actions/fail.py"],
    )

    invocation = system.run_draft_action(action.id, {})

    assert invocation.status == "failed"
    assert "after buffering" in (invocation.error or "")
    assert system.uow.records.invocations[invocation.id].status == "failed"
    assert (
        system.uow.records.objects.get(
            (app.id, "production", "buffered-object")
        )
        is None
    )


def test_read_mode_rejects_write_cypher() -> None:
    system = create_test_runtime()
    app = system.create_application("cypher-read", "Cypher Read")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/probe.py",
        (
            "def run(ctx, input):\n"
            "    return ctx.graph.run_app_cypher_read("
            "'CREATE (n:CypherProbe {id: $id}) RETURN n.id AS id', "
            "{'id': input['id']})\n"
        ),
        role="action",
        language="python",
    )
    system.create_action(
        revision.id,
        "cypher-read.probe",
        kind="normal",
        entrypoint="actions/probe.py:run",
        source_paths=["actions/probe.py"],
        input_schema={"type": "object", "required": ["id"]},
    )
    system.activate_application_revision(revision.id)
    invocation = system.run_active_action(app.id, "cypher-read.probe", {"id": "denied"})
    assert invocation.status == "failed"
    assert "write clauses are not allowed" in (invocation.error or "")


def test_delete_object_succeeds_without_provenance_record() -> None:
    system = create_test_runtime()
    app = system.create_application("notes-del", "Notes Del")
    revision = system.create_application_revision(app.id)
    system.create_object_type(revision.id, "notes-del.Note", name="Note")
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/notes.py",
        action_source(
            "def create_note(ctx, input):\n"
            "    return upsert_object(ctx, 'notes-del.Note', "
            "{'title': input['title']})\n",
            "def archive_note(ctx, input):\n"
            "    delete_object(ctx, input['id'])\n"
            "    return {'deleted': input['id']}\n",
        ),
        role="action",
        language="python",
    )
    create_action = system.create_action(
        revision.id,
        "notes-del.create",
        kind="normal",
        entrypoint="actions/notes.py:create_note",
        source_paths=["actions/notes.py"],
        input_schema={"type": "object", "required": ["title"]},
    )
    system.create_action(
        revision.id,
        "notes-del.archive",
        kind="normal",
        entrypoint="actions/notes.py:archive_note",
        source_paths=["actions/notes.py"],
        input_schema={"type": "object", "required": ["id"]},
    )
    created = system.run_draft_action(create_action.id, {"title": "Temp"})
    assert created.status == "succeeded", created.error
    note_id = created.output["id"]
    system.activate_application_revision(revision.id)
    archived = system.run_active_action(app.id, "notes-del.archive", {"id": note_id})

    assert archived.status == "succeeded", archived.error
    assert system.list_application_objects(app.id, "notes-del.Note") == []


def test_expired_job_lease_recovers_after_restart(
    restart_kernel: SystemRuntime,
) -> None:
    system = restart_kernel
    application_id = f"runtime-contract-lease-{uuid.uuid4()}"
    app = system.create_application(application_id, "Lease App")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/ok.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        f"{application_id}.run",
        kind="normal",
        entrypoint="actions/ok.py:run",
        source_paths=["actions/ok.py"],
    )
    system.create_trigger(
        revision.id,
        f"{application_id}.external",
        trigger_type="external",
        action_revision_id=action.id,
        max_attempts=2,
        retry_policy={"base_seconds": 0},
    )
    system.activate_application_revision(revision.id)
    job = system.run_trigger(f"{application_id}.external")
    system.uow.records.job_records.save(
        job.model_copy(
            update={
                "status": "running",
                "lease_owner": "dead-worker",
                "lease_expires_at": now_utc() - timedelta(seconds=5),
                "attempt": 1,
                "max_attempts": 2,
            }
        )
    )
    restarted = SystemRuntime(system.store, runtime_paths=RuntimePaths.temporary())
    recovered = restarted.recover_expired_job_leases()
    assert recovered[0].id == job.id
    assert recovered[0].status == "retry_wait"
    restarted.uow.records.job_records.save(
        recovered[0].model_copy(
            update={"next_retry_at": now_utc() - timedelta(seconds=1)}
        )
    )
    completed = restarted.process_due_job_work()
    assert completed[0].status == "succeeded", completed[0].error
    assert completed[0].attempt == 2
