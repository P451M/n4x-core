from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import Any

import pytest

from n4x.graph.neo4j import Neo4jGraph
from n4x.graph.store import Neo4jGraphStore, NodeRef
from n4x.graph.uow import GraphUnitOfWork, transactional
from n4x.kernel.errors import TransientGraphConflictError
from n4x.kernel.models import Application
from n4x.system.applications import Applications
from n4x.source_store.service import SourceStore
from n4x.testing import InMemoryGraphStore


class RecordingStore(InMemoryGraphStore):
    def __init__(self) -> None:
        self.events: list[str] = []
        super().__init__()

    def acquire_write_lock(self, ref: NodeRef) -> None:
        self.events.append(f"lock:{ref.label}:{ref.identity['id']}")
        super().acquire_write_lock(ref)

    def list_nodes(
        self, label: str, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.events.append(f"read:{label}")
        return super().list_nodes(label, filters)


class TransactionProbe:
    def __init__(self, uow: GraphUnitOfWork) -> None:
        self.uow = uow

    @transactional
    def outer(self) -> None:
        self.uow.records.applications.values()
        self.inner()

    @transactional
    def inner(self) -> None:
        self.uow.records.revisions.values()


class FailingAuthoringService:
    def __init__(
        self, uow: GraphUnitOfWork, locked: Event, release: Event
    ) -> None:
        self.uow = uow
        self.locked = locked
        self.release = release

    @transactional
    def write_then_fail(self, application_id: str) -> None:
        self.uow.records.applications.save(
            Application(id=application_id, name="Rolled back")
        )
        self.locked.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("timed out waiting to release failed transaction")
        raise RuntimeError("forced authoring rollback")


@pytest.fixture
def neo4j_authoring_store(
    neo4j_graph: Neo4jGraph,
) -> Iterator[tuple[Neo4jGraphStore, str]]:
    prefix = f"authoring-serial-{uuid.uuid4()}"
    try:
        yield Neo4jGraphStore(neo4j_graph), prefix
    finally:
        neo4j_graph.run_cypher(
            """
            MATCH (n)
            WHERE n.id STARTS WITH $prefix
               OR n.application_id STARTS WITH $prefix
               OR n.source_tree_id STARTS WITH $prefix
            DETACH DELETE n
            """,
            {"prefix": prefix},
        )


def _application_service(store: Neo4jGraphStore) -> Applications:
    uow = GraphUnitOfWork(store)
    return Applications(uow, SourceStore(store, uow))


def _create_revision(
    store: Neo4jGraphStore, application_id: str, barrier: Barrier
):
    service = _application_service(store)
    barrier.wait(timeout=5)
    return service.create_revision(application_id)


def test_transactional_acquires_root_before_reads_and_only_at_outer_entry() -> None:
    store = RecordingStore()
    probe = TransactionProbe(GraphUnitOfWork(store))

    probe.outer()

    assert store.events == [
        "lock:N4XRoot:n4x",
        "read:Application",
        "read:ApplicationRevision",
    ]


@pytest.mark.neo4j
def test_concurrent_independent_application_revisions_serialize(
    neo4j_authoring_store: tuple[Neo4jGraphStore, str],
) -> None:
    store, prefix = neo4j_authoring_store
    setup = _application_service(store)
    application_ids = [f"{prefix}-{name}" for name in ("notes", "mail", "calendar")]
    for application_id in application_ids:
        setup.create(application_id, application_id)
    barrier = Barrier(len(application_ids))

    with ThreadPoolExecutor(max_workers=len(application_ids)) as executor:
        futures = [
            executor.submit(_create_revision, store, application_id, barrier)
            for application_id in application_ids
        ]
        revisions = [future.result(timeout=10) for future in futures]

    assert {revision.id for revision in revisions} == {
        f"{application_id}@1" for application_id in application_ids
    }


@pytest.mark.neo4j
def test_concurrent_same_application_revisions_reuse_draft(
    neo4j_authoring_store: tuple[Neo4jGraphStore, str],
) -> None:
    store, prefix = neo4j_authoring_store
    application_id = f"{prefix}-same"
    _application_service(store).create(application_id, "Same")
    count = 3
    barrier = Barrier(count)

    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = [
            executor.submit(_create_revision, store, application_id, barrier)
            for _ in range(count)
        ]
        revisions = [future.result(timeout=10) for future in futures]

    assert {revision.id for revision in revisions} == {f"{application_id}@1"}


@pytest.mark.neo4j
def test_failed_authoring_transaction_releases_root_lock(
    neo4j_authoring_store: tuple[Neo4jGraphStore, str],
) -> None:
    store, prefix = neo4j_authoring_store
    locked = Event()
    release = Event()
    started = Event()
    failed_id = f"{prefix}-failed"
    succeeding_id = f"{prefix}-succeeded"
    failing = FailingAuthoringService(
        GraphUnitOfWork(store), locked=locked, release=release
    )

    def create_after_failure() -> Application:
        started.set()
        return _application_service(store).create(succeeding_id, "Succeeded")

    with ThreadPoolExecutor(max_workers=2) as executor:
        failed_future = executor.submit(failing.write_then_fail, failed_id)
        assert locked.wait(timeout=5)
        succeeding_future = executor.submit(create_after_failure)
        assert started.wait(timeout=5)
        assert not succeeding_future.done()
        release.set()
        with pytest.raises(RuntimeError, match="forced authoring rollback"):
            failed_future.result(timeout=10)
        succeeded = succeeding_future.result(timeout=10)

    assert succeeded.id == succeeding_id
    assert store.get_node("Application", {"id": failed_id}) is None
    assert store.get_node("Application", {"id": succeeding_id}) is not None


def test_memory_cypher_rejects_map_property_values() -> None:
    store = InMemoryGraphStore()
    with pytest.raises(TypeError, match="primitive types"):
        store.run_cypher(
            """
            MERGE (n:ApplicationObject {
                application_id: $application_id,
                data_space_id: $data_space_id,
                id: $id
            })
            SET n.values = $values
            RETURN n.id AS id
            """,
            {
                "application_id": "app",
                "data_space_id": "production",
                "id": "item",
                "values": {"name": "p"},
            },
        )


def test_transactional_retries_transient_graph_conflict() -> None:
    store = InMemoryGraphStore()
    attempts = {"count": 0}

    class Probe:
        def __init__(self) -> None:
            self.uow = GraphUnitOfWork(store)

        @transactional
        def write(self) -> str:
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise TransientGraphConflictError("deadlock")
            return "ok"

    assert Probe().write() == "ok"
    assert attempts["count"] == 3
