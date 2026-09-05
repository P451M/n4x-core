from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from n4x.graph.integrity import GraphIntegrityService
from n4x.graph.neo4j import Neo4jGraph
from n4x.graph.store import GraphStore, Neo4jGraphStore, node_ref
from n4x.kernel.models import (
    ApplicationObject,
    ApplicationRelation,
    Invocation,
    SourceTree,
)
from n4x.system.runtime import SystemRuntime
from n4x.runtime.actions import RuntimePaths
from n4x.testing import InMemoryGraphStore


@pytest.fixture(params=["memory", pytest.param("neo4j", marks=pytest.mark.neo4j)])
def integrity_store(request: pytest.FixtureRequest) -> Iterator[GraphStore]:
    if request.param == "memory":
        yield InMemoryGraphStore()
        return

    graph: Neo4jGraph = request.getfixturevalue("neo4j_graph")
    try:
        yield Neo4jGraphStore(graph)
    finally:
        graph.run_cypher(
            """
            MATCH (root)
            WHERE (root:Application OR root:Experience)
              AND any(prefix IN $prefixes WHERE root.id STARTS WITH prefix)
            OPTIONAL MATCH (root)-[*0..]->(owned)
            WITH collect(DISTINCT owned) AS owned
            UNWIND owned AS n
            DETACH DELETE n
            """,
            {
                "prefixes": [
                    "integrity-",
                    "relation-integrity-",
                    "deep-integrity-",
                ]
            },
        )
        graph.run_cypher(
            """
            MATCH (n)
            WHERE any(
                value IN [n.id, n.source_tree_id]
                WHERE any(prefix IN $prefixes WHERE value STARTS WITH prefix)
            )
            DETACH DELETE n
            """,
            {
                "prefixes": [
                    "integrity-",
                    "relation-integrity-",
                    "deep-integrity-",
                ]
            },
        )
        remaining = graph.run_cypher(
            """
            MATCH (n)
            WHERE any(
                value IN [n.id, n.source_tree_id]
                WHERE any(prefix IN $prefixes WHERE value STARTS WITH prefix)
            )
            RETURN labels(n) AS labels
            """,
            {
                "prefixes": [
                    "integrity-",
                    "relation-integrity-",
                    "deep-integrity-",
                ]
            },
        )
        assert remaining == [], f"integrity test records leaked: {remaining}"


def test_fresh_composition_repairs_durable_damage(
    integrity_store: GraphStore,
) -> None:
    prefix = f"integrity-{uuid.uuid4()}"
    first = _kernel(integrity_store)
    app = first.create_application(prefix, "Integrity")
    revision = first.create_application_revision(app.id)
    guide = first.inspect_authoring_guide()
    blueprint = first.create_blueprint(f"{prefix}.blueprint", "Blueprint")
    blueprint_revision = first.create_blueprint_revision(
        blueprint.id,
        instructions="test",
        content={"version": 1},
        activate=True,
    )
    source = first.source.write_source_file(
        revision.id,
        "actions/run.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    dependency = first.create_runtime_dependency(
        revision.id, "python", "packaging", ">=25"
    )
    action = first.create_action(
        revision.id,
        f"{prefix}.run",
        kind="normal",
        entrypoint="actions/run.py:run",
        source_paths=[source.path],
        dependency_ids=[dependency.id],
    )
    other_action = first.create_action(
        revision.id,
        f"{prefix}.other",
        kind="normal",
        entrypoint="actions/run.py:run",
        source_paths=[source.path],
    )
    with first.uow:
        stable_action = first.uow.records.actions[action.action_id]
        first.uow.records.actions.save(
            stable_action.model_copy(update={"active_revision_id": action.id})
        )
        first.uow.records.invocations.save(
            Invocation(
                id=f"{prefix}-invocation",
                action_revision_id=action.id,
                invocation_kind="draft",
                status="succeeded",
                input={},
            )
        )
        first.store.create_edge(
            node_ref("Action", id=action.action_id),
            "ACTIVE_REVISION",
            node_ref("ActionRevision", id=action.id),
        )
    integrity_store.delete_edge(
        node_ref("N4XRoot", id="n4x"),
        "HAS_AUTHORING_GUIDE",
        node_ref("AuthoringGuide", id=guide["id"]),
    )
    integrity_store.delete_edge(
        node_ref("N4XRoot", id="n4x"),
        "HAS_BLUEPRINT",
        node_ref("AppBlueprint", id=blueprint.id),
    )
    integrity_store.delete_edge(
        node_ref("ActionRevision", id=action.id),
        "DEPENDS_ON",
    )
    integrity_store.create_edge(
        node_ref("Action", id=action.action_id),
        "ACTIVE_REVISION",
        node_ref("ActionRevision", id=other_action.id),
    )

    fresh = _kernel(integrity_store)
    damaged = fresh.validate_graph_shape()
    repaired = fresh.repair_graph_edges()
    restarted = _kernel(integrity_store)

    assert damaged["ok"] is False
    assert any("DEPENDS_ON" in error for error in damaged["errors"])
    assert any("HAS_INVOCATION" in error for error in damaged["errors"])
    assert any("RAN" in error for error in damaged["errors"])
    assert repaired["ok"] is True, repaired["errors"]
    assert restarted.validate_graph_shape()["ok"] is True
    assert integrity_store.list_edges(
        node_ref("AppBlueprint", id=blueprint.id),
        "ACTIVE_REVISION",
        node_ref("BlueprintRevision", id=blueprint_revision.id),
    )


def test_integrity_rejects_invalid_physical_relation_endpoints(
    integrity_store: GraphStore,
) -> None:
    prefix = f"relation-integrity-{uuid.uuid4()}"
    system = _kernel(integrity_store)
    app = system.create_application(prefix, "Relations")
    revision = system.create_application_revision(app.id)
    left_type = system.create_object_type(revision.id, f"{prefix}.Left", name="Left")
    right_type = system.create_object_type(revision.id, f"{prefix}.Right", name="Right")
    relation_revision = system.create_relation_type(
        revision.id,
        f"{prefix}.left_right",
        name="left_right",
        from_object_type_id=left_type.object_type_id,
        to_object_type_id=right_type.object_type_id,
    )
    wrong_source = ApplicationObject(
        id=f"{prefix}-wrong",
        application_id=app.id,
        object_type_id=right_type.object_type_id,
        object_type_revision_id=right_type.id,
    )
    target = ApplicationObject(
        id=f"{prefix}-target",
        application_id=app.id,
        object_type_id=right_type.object_type_id,
        object_type_revision_id=right_type.id,
    )
    with system.uow:
        system.uow.objects.save(wrong_source)
        system.uow.objects.attach(wrong_source, right_type.id)
        system.uow.objects.save(target)
        system.uow.objects.attach(target, right_type.id)
        system.uow.relations.save(
            ApplicationRelation(
                id=f"{prefix}-edge",
                application_id=app.id,
                relation_type_id=relation_revision.relation_type_id,
                relation_type_revision_id=relation_revision.id,
                physical_type=relation_revision.physical_type,
                from_object_id=wrong_source.id,
                to_object_id=target.id,
            )
        )

    report = system.validate_graph_shape()

    assert report["ok"] is False
    assert any("endpoint type mismatch" in error for error in report["errors"])


def test_interned_orphans_are_warnings_not_failures(
    integrity_store: GraphStore,
) -> None:
    prefix = f"deep-integrity-{uuid.uuid4()}"
    system = _kernel(integrity_store)
    system.create_application(prefix, "Deep")
    trees: list[SourceTree] = []
    for index in range(10):
        trees.append(
            SourceTree(
                id=f"sha256:{prefix}-{index:02d}",
                status="interned",
                tree_hash=f"sha256:{prefix}-{index:02d}",
            )
        )
    with system.uow:
        for tree in trees:
            system.uow.records.source_trees.save(tree)

    report = system.validate_graph_shape()
    repaired = system.repair_graph_edges()

    assert report["ok"] is True
    assert any("unreachable node" in warning for warning in report["warnings"])
    assert repaired["ok"] is True, repaired["errors"]


def test_integrity_repairs_platform_theme_ownership_and_revision_edges(
    integrity_store: GraphStore,
) -> None:
    system = _kernel(integrity_store)
    release = system.platform_authoring_release
    theme = node_ref("UiTheme", id="n4x.ui-theme")
    theme_revision = node_ref("UiThemeRevision", id=release["theme_revision_id"])
    guide = node_ref("AuthoringGuide", id="n4x.authoring-guide")
    guide_revision = node_ref("AuthoringGuideRevision", id=release["guide_revision_id"])
    integrity_store.delete_edge(
        node_ref("N4XRoot", id="n4x"),
        "HAS_UI_THEME",
        theme,
    )
    integrity_store.delete_edge(theme, "HAS_REVISION", theme_revision)
    integrity_store.delete_edge(theme, "ACTIVE_REVISION", theme_revision)
    integrity_store.delete_edge(guide, "ACTIVE_REVISION", guide_revision)

    damaged = system.validate_graph_shape()
    repaired = system.repair_graph_edges()

    assert damaged["ok"] is False
    assert any("HAS_UI_THEME" in error for error in damaged["errors"])
    assert any("ACTIVE_REVISION" in error for error in damaged["errors"])
    assert repaired["ok"] is True, repaired["errors"]
    assert integrity_store.list_edges(
        node_ref("N4XRoot", id="n4x"),
        "HAS_UI_THEME",
        theme,
    )
    assert integrity_store.list_edges(theme, "HAS_REVISION", theme_revision)
    assert integrity_store.list_edges(theme, "ACTIVE_REVISION", theme_revision)
    assert integrity_store.list_edges(guide, "ACTIVE_REVISION", guide_revision)


def test_surface_integrity_rejects_revision_edges() -> None:
    store = InMemoryGraphStore()
    system = _kernel(store)
    experience = system.create_experience("surface-integrity", "Surface integrity")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    system.source.write_source_file(
        revision.id,
        "surfaces/main.tsx",
        "document.body.textContent = 'surface';\n",
        role="surface",
        language="typescript",
    )
    surface = system.create_experience_surface(
        revision.id,
        "main",
        surface_type="browser",
        entrypoint="surfaces/main.tsx",
        source_paths=["surfaces/main.tsx"],
        config={"mount_path": "/"},
    )
    surface_ref = node_ref("ExperienceSurface", id=surface.id)
    store.create_edge(surface_ref, "ACTIVE_REVISION", surface_ref)
    invalid = system.validate_graph_shape()
    assert invalid["ok"] is False
    assert any("must not have revision or active" in error for error in invalid["errors"])


def test_worker_start_does_not_validate_graph_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    original = GraphIntegrityService.validate_graph_shape

    def wrapped(self: GraphIntegrityService):
        calls["n"] += 1
        return original(self)

    monkeypatch.setattr(GraphIntegrityService, "validate_graph_shape", wrapped)
    _kernel(InMemoryGraphStore())
    assert calls["n"] == 0


def test_reset_and_bootstrap_are_idempotent() -> None:
    integrity_store = InMemoryGraphStore()
    system = _kernel(integrity_store)
    system.create_application(f"reset-{uuid.uuid4()}", "Disposable")

    system.reset_dev_graph()
    first_release = dict(system.platform_authoring_release)
    system.reset_dev_graph()
    second_release = dict(system.platform_authoring_release)
    system.integrity.bootstrap_schema()
    restarted = _kernel(integrity_store)
    root = integrity_store.get_node("N4XRoot", {"id": "n4x"})

    assert system.integrity.validate_graph_shape().ok
    assert root is not None
    assert root["graph_metamodel_version"] == "n4x.graph.metamodel.v6"
    assert first_release == second_release
    assert len(integrity_store.list_nodes("UiTheme")) == 1
    assert len(integrity_store.list_nodes("AuthoringGuide")) == 1
    assert restarted.uow.records.applications.values() == []
    assert restarted.validate_graph_shape()["ok"] is True


def _kernel(store: GraphStore) -> SystemRuntime:
    return SystemRuntime(store, runtime_paths=RuntimePaths.temporary())
