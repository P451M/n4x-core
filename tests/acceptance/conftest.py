from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator

import pytest
from n4x.graph.neo4j import Neo4jGraph
from n4x.graph.store import Neo4jGraphStore
from n4x.system.runtime import SystemRuntime
from n4x.runtime.actions import RuntimePaths
from n4x.testing import create_test_runtime

from .harness import McpAuthoringHarness


@pytest.fixture(params=["memory", pytest.param("neo4j", marks=pytest.mark.neo4j)])
def harness(request: pytest.FixtureRequest) -> Iterator[McpAuthoringHarness]:
    if request.param == "memory":
        yield McpAuthoringHarness(create_test_runtime())
        return
    graph: Neo4jGraph = request.getfixturevalue("neo4j_graph")
    system = SystemRuntime(Neo4jGraphStore(graph), runtime_paths=RuntimePaths.temporary())
    try:
        yield McpAuthoringHarness(system)
    finally:
        prefix = "acceptance-"
        system.store.run_cypher(
            """
            MATCH (app:Application)
            WHERE app.id STARTS WITH $prefix
            OPTIONAL MATCH (app)-[*0..]->(owned)
            WITH collect(DISTINCT owned) AS owned
            UNWIND owned AS node
            DETACH DELETE node
            """,
            {"prefix": prefix},
        )
        system.store.run_cypher(
            """
            MATCH (n)
            WHERE n.id STARTS WITH $prefix
               OR n.application_id STARTS WITH $prefix
               OR n.source_tree_id STARTS WITH $prefix
            DETACH DELETE n
            """,
            {"prefix": prefix},
        )


def unique_app_id(name: str) -> str:
    return f"acceptance-{name}-{uuid.uuid4().hex[:8]}"


def pnpm_available() -> bool:
    return shutil.which("pnpm") is not None
