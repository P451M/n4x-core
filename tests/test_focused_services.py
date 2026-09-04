from __future__ import annotations

import inspect

from n4x.graph.uow import GraphUnitOfWork
from n4x.source_store.service import SourceStore
from n4x.system.applications import Applications
from n4x.testing import InMemoryGraphStore, create_test_runtime


def test_schema_and_relation_services_share_uow_repositories() -> None:
    runtime = create_test_runtime()
    app = runtime.create_application("focused", "Focused")
    revision = runtime.create_application_revision(app.id)
    left_type = runtime.schema.create_object_type(
        revision.id, "focused.Left", name="Left"
    )
    right_type = runtime.schema.create_object_type(
        revision.id, "focused.Right", name="Right"
    )
    relation = runtime.schema.create_relation_type(
        revision.id,
        "focused.left_right",
        name="left_right",
        from_object_type_id=left_type.object_type_id,
        to_object_type_id=right_type.object_type_id,
    )

    assert runtime.queries.application(app.id) == app
    assert (
        runtime.uow.records.relation_type_revisions[relation.id].physical_type
        == relation.physical_type
    )


def test_application_service_needs_no_runtime_composition() -> None:
    store = InMemoryGraphStore()
    uow = GraphUnitOfWork(store)
    service = Applications(uow, SourceStore(store, uow))

    application = service.create("standalone", "Standalone")
    revision = service.create_revision(application.id)

    with uow:
        assert uow.applications.get(application.id) == application
    assert revision.application_id == application.id


def test_system_runtime_contains_no_transactional_domain_methods() -> None:
    from n4x.system.runtime import SystemRuntime

    source = inspect.getsource(SystemRuntime)

    assert "@transactional" not in source
    assert "self.graph." not in source
