from __future__ import annotations

import pytest
from pydantic import ValidationError

from n4x.contracts import GRAPH_METAMODEL_VERSION
from n4x.graph.uow import GraphUnitOfWork
from n4x.graph.integrity import GraphIntegrityService
from n4x.kernel.errors import GraphMetamodelVersionError
from n4x.kernel.intern import experience_surface_id
from n4x.kernel.models import ApplicationAccessDeclaration, ExperienceSurface
from n4x.kernel.surface_types import SURFACE_TYPES
from n4x.testing import InMemoryGraphStore


def _surface(**overrides: object) -> ExperienceSurface:
    payload = {
        "surface_id": "main",
        "surface_type": "browser",
        "surface_type_version": 1,
        "entrypoint": "src/main.tsx",
        "source_paths": ["src/main.tsx"],
        "config": {"mount_path": "/main/"},
        **overrides,
    }
    return ExperienceSurface(id=experience_surface_id(payload), **payload)


def test_experience_surface_uses_versioned_registry_validation() -> None:
    surface = _surface()
    assert surface.config["mount_path"] == "/main"
    assert SURFACE_TYPES.definition("mcp_app", 1).surface_type == "mcp_app"
    assert "experience_revision_id" not in ExperienceSurface.model_fields
    assert "source_tree_id" not in ExperienceSurface.model_fields
    assert "active_revision_id" not in ExperienceSurface.model_fields
    with pytest.raises(ValidationError, match="unsupported Surface type"):
        ExperienceSurface.model_validate({**surface.model_dump(), "surface_type": "mobile"})
    with pytest.raises(ValidationError, match="traversal-safe"):
        ExperienceSurface.model_validate({**surface.model_dump(), "entrypoint": "../outside.ts", "source_paths": ["../outside.ts"]})
    opted_in = _surface(surface_id="root", config={"pwa": {}})
    assert "pwa" not in surface.config
    assert opted_in.config["pwa"] == {"manifest_path": "manifest.webmanifest"}
    with pytest.raises(ValidationError, match="pwa is valid only on the / browser Surface"):
        ExperienceSurface.model_validate(
            {
                **surface.model_dump(),
                "config": {"mount_path": "/admin", "pwa": {}},
            }
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExperienceSurface.model_validate(
            {
                **surface.model_dump(),
                "experience_revision_id": "office@1",
            }
        )


def test_application_access_preserves_unrestricted_and_none_semantics() -> None:
    unrestricted = ApplicationAccessDeclaration(application_id="app")
    denied = ApplicationAccessDeclaration(application_id="app", object_type_ids=[], relation_type_ids=[], action_ids=[], secret_reference_ids=[])
    assert unrestricted.object_type_ids is None
    assert unrestricted.secret_reference_ids is None
    assert denied.object_type_ids == []
    assert denied.secret_reference_ids == []
    with pytest.raises(ValidationError, match="must be unique"):
        ApplicationAccessDeclaration(application_id="app", action_ids=["send", "send"])


def test_fresh_graph_is_stamped_v6() -> None:
    store = InMemoryGraphStore()
    service = GraphIntegrityService(store, GraphUnitOfWork(store))

    assert service.initialize_graph_root() is True
    root = store.get_node("N4XRoot", {"id": "n4x"})
    assert root is not None
    assert root["graph_metamodel_version"] == GRAPH_METAMODEL_VERSION


def test_existing_v6_graph_is_accepted_without_rewriting_root() -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        "N4XRoot",
        {"id": "n4x"},
        {
            "id": "n4x",
            "graph_metamodel_version": GRAPH_METAMODEL_VERSION,
            "sentinel": "preserved",
        },
    )
    store.upsert_node("Foreign", {"id": "kept"}, {"id": "kept"})
    service = GraphIntegrityService(store, GraphUnitOfWork(store))

    assert service.initialize_graph_root() is False
    assert store.get_node("N4XRoot", {"id": "n4x"}) == {
        "id": "n4x",
        "graph_metamodel_version": GRAPH_METAMODEL_VERSION,
        "sentinel": "preserved",
    }


@pytest.mark.parametrize(
    ("version", "add_data"),
    [
        ("n4x.graph.metamodel.v4", False),
        ("n4x.graph.metamodel.v3", False),
        (None, True),
    ],
)
def test_non_v6_graph_requires_reset(
    version: str | None, add_data: bool
) -> None:
    store = InMemoryGraphStore()
    if version is not None:
        store.upsert_node(
            "N4XRoot",
            {"id": "n4x"},
            {"id": "n4x", "graph_metamodel_version": version},
        )
    if add_data:
        store.upsert_node("LegacyRecord", {"id": "legacy"}, {"id": "legacy"})
    service = GraphIntegrityService(store, GraphUnitOfWork(store))

    with pytest.raises(
        GraphMetamodelVersionError,
        match=r"graph reset required.*n4x\.graph\.metamodel\.v6",
    ):
        service.initialize_graph_root()
