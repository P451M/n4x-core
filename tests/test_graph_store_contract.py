from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Barrier, Lock, Thread
from typing import Any

import pytest

from n4x.graph.neo4j import Neo4jConfig, Neo4jConfigError, Neo4jGraph
from n4x.graph.store import GraphStore, Neo4jGraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ConcurrentGraphUpdateError, GraphUnitOfWorkError
from n4x.kernel.models import (
    Application,
    ApplicationObject,
    ApplicationRelation,
    ApplicationRevision,
)
from n4x.system.runtime import SystemRuntime
from n4x.runtime.actions import RuntimePaths
from n4x.testing import InMemoryGraphStore


pytestmark = pytest.mark.contract


@dataclass(frozen=True)
class ContractContext:
    store: GraphStore
    prefix: str


def test_neo4j_config_from_env_requires_explicit_credentials() -> None:
    with pytest.raises(Neo4jConfigError, match="N4X_NEO4J_PASSWORD"):
        Neo4jConfig.from_env(
            {
                "N4X_NEO4J_URI": "bolt://localhost:7687",
                "N4X_NEO4J_USER": "neo4j",
                "N4X_NEO4J_DATABASE": "n4x-test",
            }
        )


def test_neo4j_config_from_env_reads_one_central_contract() -> None:
    config = Neo4jConfig.from_env(
        {
            "N4X_NEO4J_URI": "bolt://db.example:7687",
            "N4X_NEO4J_USER": "n4x",
            "N4X_NEO4J_PASSWORD": "not-a-default",
            "N4X_NEO4J_DATABASE": "n4x-development",
        }
    )

    assert config == Neo4jConfig(
        uri="bolt://db.example:7687",
        user="n4x",
        password="not-a-default",
        database="n4x-development",
    )


def test_schema_omits_unused_auth_session_persistence() -> None:
    graph = Neo4jGraph.__new__(Neo4jGraph)
    graph.config = Neo4jConfig(
        uri="bolt://unused",
        user="unused",
        password="unused",
        database="unused",
    )

    assert all("AuthSession" not in statement for statement in graph.schema_statements())


@pytest.fixture(params=["memory", pytest.param("neo4j", marks=pytest.mark.neo4j)])
def graph_store_contract(request: pytest.FixtureRequest) -> Iterator[ContractContext]:
    prefix = f"contract-{uuid.uuid4()}"
    if request.param == "memory":
        yield ContractContext(InMemoryGraphStore(), prefix)
        return

    graph: Neo4jGraph = request.getfixturevalue("neo4j_graph")

    try:
        yield ContractContext(Neo4jGraphStore(graph), prefix)
    finally:
        graph.run_cypher(
            """
            MATCH (n)
            WHERE n.id STARTS WITH $prefix
               OR n.application_id STARTS WITH $prefix
               OR n.source_tree_id STARTS WITH $prefix
            DETACH DELETE n
            """,
            {"prefix": prefix},
        )


def test_atomic_node_and_structural_edge(
    graph_store_contract: ContractContext,
) -> None:
    store, prefix = graph_store_contract.store, graph_store_contract.prefix
    uow = GraphUnitOfWork(store)
    app = Application(id=f"{prefix}-app", name="Contract")
    revision = _revision(prefix, app.id, 1)

    with uow:
        uow.applications.save(app)
        uow.applications.attach_to_root(app.id)
        uow.applications.save_revision(revision)
        uow.relations.create_structural(
            node_ref("Application", id=app.id),
            "HAS_REVISION",
            node_ref("ApplicationRevision", id=revision.id),
        )

    assert store.get_node("Application", {"id": app.id}) is not None
    assert len(
        store.list_edges(
            node_ref("Application", id=app.id),
            "HAS_REVISION",
            node_ref("ApplicationRevision", id=revision.id),
        )
    ) == 1

    rolled_back = Application(id=f"{prefix}-rollback", name="Rollback")
    with pytest.raises(RuntimeError, match="before edge"):
        with uow:
            uow.applications.save(rolled_back)
            raise RuntimeError("before edge")

    assert store.get_node("Application", {"id": rolled_back.id}) is None


def test_fresh_runtime_reads_store_authority(
    graph_store_contract: ContractContext,
) -> None:
    store, prefix = graph_store_contract.store, graph_store_contract.prefix
    first = SystemRuntime(store, runtime_paths=RuntimePaths.temporary())
    app = first.create_application(f"{prefix}-app", "App")
    revision = first.create_application_revision(app.id)

    second = SystemRuntime(store, runtime_paths=RuntimePaths.temporary())

    assert second.inspect_application(app.id) == app
    assert second.uow.records.revisions[revision.id] == revision


def test_active_edge_replacement_is_atomic_and_optimistic(
    graph_store_contract: ContractContext,
) -> None:
    store, prefix = graph_store_contract.store, graph_store_contract.prefix
    uow = GraphUnitOfWork(store)
    app = Application(id=f"{prefix}-active", name="Active")
    first = _revision(prefix, app.id, 1)
    second = _revision(prefix, app.id, 2)

    with uow:
        uow.applications.save(app)
        uow.applications.save_revision(first)
        uow.applications.save_revision(second)
        uow.applications.replace_active_revision(
            app.id, first.id, expected_revision_id=None
        )

    with pytest.raises(RuntimeError, match="replacement failure"):
        with uow:
            uow.applications.replace_active_revision(
                app.id, second.id, expected_revision_id=first.id
            )
            raise RuntimeError("replacement failure")

    active_edges = store.list_edges(
        node_ref("Application", id=app.id), "ACTIVE_REVISION"
    )
    assert [edge.to_ref.identity["id"] for edge in active_edges] == [first.id]

    with pytest.raises(ConcurrentGraphUpdateError):
        with uow:
            uow.applications.replace_active_revision(
                app.id, second.id, expected_revision_id=None
            )


def test_concurrent_active_edge_replacement_has_one_winner(
    graph_store_contract: ContractContext,
) -> None:
    store, prefix = graph_store_contract.store, graph_store_contract.prefix
    uow = GraphUnitOfWork(store)
    app = Application(id=f"{prefix}-concurrent", name="Concurrent")
    revisions = [_revision(prefix, app.id, number) for number in range(1, 4)]
    with uow:
        uow.applications.save(app)
        for revision in revisions:
            uow.applications.save_revision(revision)
        uow.applications.replace_active_revision(
            app.id, revisions[0].id, expected_revision_id=None
        )

    barrier = Barrier(2)
    result_lock = Lock()
    winners: list[str] = []
    stale_updates: list[str] = []
    unexpected: list[BaseException] = []

    def replace(target_revision_id: str) -> None:
        thread_uow = GraphUnitOfWork(store)
        barrier.wait()
        try:
            with thread_uow:
                thread_uow.applications.replace_active_revision(
                    app.id,
                    target_revision_id,
                    expected_revision_id=revisions[0].id,
                )
            with result_lock:
                winners.append(target_revision_id)
        except ConcurrentGraphUpdateError:
            with result_lock:
                stale_updates.append(target_revision_id)
        except BaseException as error:
            with result_lock:
                unexpected.append(error)

    threads = [
        Thread(target=replace, args=(revision.id,))
        for revision in revisions[1:]
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert unexpected == [], [
        (type(error).__name__, str(error)) for error in unexpected
    ]
    assert len(winners) == 1
    assert len(stale_updates) == 1
    active_edges = store.list_edges(
        node_ref("Application", id=app.id), "ACTIVE_REVISION"
    )
    assert [edge.to_ref.identity["id"] for edge in active_edges] == winners


def test_app_relation_create_update_list_delete(
    graph_store_contract: ContractContext,
) -> None:
    store, prefix = graph_store_contract.store, graph_store_contract.prefix
    uow = GraphUnitOfWork(store)
    app_id = f"{prefix}-relations"
    left = _object(prefix, app_id, "left")
    right = _object(prefix, app_id, "right")
    relation = _relation(prefix, app_id, left.id, right.id, {"rank": 1})

    with uow:
        uow.objects.save(left)
        uow.objects.save(right)
        uow.relations.save(relation)

    updated = relation.model_copy(update={"values": {"rank": 2}})
    with uow:
        uow.relations.save(updated)

    assert uow.store.list_app_relations(app_id) == [updated]

    with uow:
        uow.relations.delete(updated)

    assert store.list_app_relations(app_id) == []


def test_nested_uow_reuses_outer_transaction(
    graph_store_contract: ContractContext,
) -> None:
    store, prefix = graph_store_contract.store, graph_store_contract.prefix
    uow = GraphUnitOfWork(store)
    outer = Application(id=f"{prefix}-outer", name="Outer")
    inner = Application(id=f"{prefix}-inner", name="Inner")

    with uow:
        assert uow.depth == 1
        uow.applications.save(outer)
        with uow:
            assert uow.depth == 2
            uow.applications.save(inner)
        assert uow.depth == 1

    assert store.get_node("Application", {"id": outer.id}) is not None
    assert store.get_node("Application", {"id": inner.id}) is not None


def test_explicit_begin_commit_and_rollback(
    graph_store_contract: ContractContext,
) -> None:
    store, prefix = graph_store_contract.store, graph_store_contract.prefix
    uow = GraphUnitOfWork(store)
    committed = Application(id=f"{prefix}-committed", name="Committed")
    rolled_back = Application(id=f"{prefix}-explicit-rollback", name="Rollback")

    uow.begin()
    uow.applications.save(committed)
    uow.commit()

    uow.begin()
    uow.applications.save(rolled_back)
    uow.rollback()

    assert store.get_node("Application", {"id": committed.id}) is not None
    assert store.get_node("Application", {"id": rolled_back.id}) is None


def test_begin_context_manager_commits_once(
    graph_store_contract: ContractContext,
) -> None:
    store, prefix = graph_store_contract.store, graph_store_contract.prefix
    uow = GraphUnitOfWork(store)
    app = Application(id=f"{prefix}-begin-context", name="Context")

    with uow.begin() as active:
        assert active is uow
        assert uow.depth == 1
        uow.applications.save(app)

    assert not uow.is_active
    assert store.get_node("Application", {"id": app.id}) is not None


def test_nested_failure_marks_outer_uow_for_rollback(
    graph_store_contract: ContractContext,
) -> None:
    store, prefix = graph_store_contract.store, graph_store_contract.prefix
    uow = GraphUnitOfWork(store)
    outer = Application(id=f"{prefix}-caught-outer", name="Outer")
    inner = Application(id=f"{prefix}-caught-inner", name="Inner")

    with pytest.raises(GraphUnitOfWorkError, match="marked"):
        with uow:
            uow.applications.save(outer)
            try:
                with uow:
                    uow.applications.save(inner)
                    raise RuntimeError("caught by outer service")
            except RuntimeError:
                pass

    assert store.get_node("Application", {"id": outer.id}) is None
    assert store.get_node("Application", {"id": inner.id}) is None


def test_multi_object_relation_forced_rollback(
    graph_store_contract: ContractContext,
) -> None:
    store, prefix = graph_store_contract.store, graph_store_contract.prefix
    uow = GraphUnitOfWork(store)
    app_id = f"{prefix}-multi"
    left = _object(prefix, app_id, "multi-left")
    right = _object(prefix, app_id, "multi-right")
    relation = _relation(prefix, app_id, left.id, right.id, {"atomic": True})

    with pytest.raises(RuntimeError, match="force rollback"):
        with uow:
            uow.objects.save(left)
            uow.objects.save(right)
            uow.relations.save(relation)
            raise RuntimeError("force rollback")

    assert store.get_node("ApplicationObject", {"id": left.id}) is None
    assert store.get_node("ApplicationObject", {"id": right.id}) is None
    assert store.list_app_relations(app_id) == []


def _revision(prefix: str, application_id: str, number: int) -> ApplicationRevision:
    return ApplicationRevision(
        id=f"{prefix}-revision-{number}",
        application_id=application_id,
        source_tree_id=f"{prefix}-source-{number}",
    )


def _object(prefix: str, application_id: str, suffix: str) -> ApplicationObject:
    return ApplicationObject(
        id=f"{prefix}-{suffix}",
        application_id=application_id,
        object_type_id=f"{application_id}.Item",
    )


def _relation(
    prefix: str,
    application_id: str,
    from_object_id: str,
    to_object_id: str,
    values: dict[str, Any],
) -> ApplicationRelation:
    return ApplicationRelation(
        id=f"{prefix}-relation",
        application_id=application_id,
        relation_type_id=f"{application_id}.items",
        relation_type_revision_id=f"{prefix}-relation-revision",
        physical_type="APP_REL_CONTRACT_ITEMS_12345678",
        from_object_id=from_object_id,
        to_object_id=to_object_id,
        values=values,
    )
