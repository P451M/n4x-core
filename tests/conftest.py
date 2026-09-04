from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from n4x.graph.neo4j import Neo4jConfig, Neo4jConfigError, Neo4jGraph


def neo4j_required() -> bool:
    return os.getenv("N4X_NEO4J_REQUIRED", "").lower() in {"1", "true", "yes"}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    try:
        Neo4jConfig.from_env()
    except Neo4jConfigError as error:
        message = f"Neo4j test configuration is unavailable: {error}"
        if neo4j_required():
            raise pytest.UsageError(message) from error
        skip = pytest.mark.skip(reason=message)
        for item in items:
            if item.get_closest_marker("neo4j") is not None:
                item.add_marker(skip)


@pytest.fixture
def neo4j_graph() -> Iterator[Neo4jGraph]:
    required = neo4j_required()
    try:
        config = Neo4jConfig.from_env()
    except Neo4jConfigError as error:
        message = f"Neo4j test configuration is unavailable: {error}"
        if required:
            pytest.fail(message, pytrace=False)
        pytest.skip(message)

    graph = Neo4jGraph(config)
    try:
        graph.verify_connectivity()
        graph.bootstrap_schema()
    except Exception as error:
        graph.close()
        message = f"configured Neo4j test target is unavailable: {error}"
        if required:
            pytest.fail(message, pytrace=False)
        pytest.skip(message)

    try:
        yield graph
    finally:
        graph.close()
