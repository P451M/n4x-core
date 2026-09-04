from __future__ import annotations

from pathlib import Path

from n4x.contracts.graph_metamodel import GRAPH_METAMODEL_VERSION
from n4x.graph.integrity import GraphIntegrityService
from n4x.graph.neo4j import Neo4jGraph
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.models import PlatformSystem, SystemRevision
from tests.host_support import make_host


def test_neo4j_schema_declares_system_nodes() -> None:
    statements = Neo4jGraph.schema_statements()
    assert any(":System " in statement or ":System)" in statement for statement in statements)
    assert any("SystemRevision" in statement for statement in statements)
    assert not any("SystemSourceFile" in statement for statement in statements)


def test_host_stamps_metamodel_before_system_import(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    root = host.graph_store.get_node("N4XRoot", {"id": "n4x"})
    assert root is not None
    assert root["graph_metamodel_version"] == GRAPH_METAMODEL_VERSION
    host.system_graph.import_official_archive(host.official_archive)
    later = GraphIntegrityService(host.graph_store, GraphUnitOfWork(host.graph_store))
    assert later.initialize_graph_root() is False


def test_system_revision_roundtrip(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    imported = host.system_graph.import_official_archive(host.official_archive)
    revision = host.system_graph.get_revision(imported.id)
    assert isinstance(revision, SystemRevision)
    system = host.system_graph.ensure_platform_system()
    assert isinstance(system, PlatformSystem)
    assert system.id == "n4x"
