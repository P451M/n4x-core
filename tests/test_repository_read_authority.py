from __future__ import annotations

from n4x.kernel.models import ApplicationObject
from n4x.system.runtime import SystemRuntime
from n4x.runtime.actions import RuntimePaths
from n4x.testing import InMemoryGraphStore


def test_fresh_runtime_composition_reads_existing_store_records() -> None:
    store = InMemoryGraphStore()
    first = SystemRuntime(store, runtime_paths=RuntimePaths.temporary())
    app = first.create_application("cold-read", "Cold Read")
    revision = first.create_application_revision(app.id)
    first.create_object_type(revision.id, "cold-read.Item", name="Item")

    second = SystemRuntime(store, runtime_paths=RuntimePaths.temporary())

    assert second.inspect_application(app.id) == app
    assert second.uow.records.revisions[revision.id] == revision
    assert second.uow.records.object_types["cold-read.Item"].application_id == app.id


def test_repository_objects_and_relations_are_shared_across_compositions() -> None:
    store = InMemoryGraphStore()
    first = SystemRuntime(store, runtime_paths=RuntimePaths.temporary())
    app = first.create_application("cold-rel", "Cold Relations")
    revision = first.create_application_revision(app.id)
    left_type = first.create_object_type(revision.id, "cold-rel.Left", name="Left")
    right_type = first.create_object_type(revision.id, "cold-rel.Right", name="Right")
    relation_type = first.create_relation_type(
        revision.id,
        "cold-rel.left_right",
        name="left_right",
        from_object_type_id=left_type.object_type_id,
        to_object_type_id=right_type.object_type_id,
    )
    left = ApplicationObject(
        id="cold-left",
        application_id=app.id,
        object_type_id=left_type.object_type_id,
    )
    right = ApplicationObject(
        id="cold-right",
        application_id=app.id,
        object_type_id=right_type.object_type_id,
    )
    with first.uow:
        first.uow.objects.save(left)
        first.uow.objects.save(right)
        first.uow.objects.attach(left)
        first.uow.objects.attach(right)
    relation = first.create_application_relation(
        app.id,
        relation_type.relation_type_id,
        left.id,
        right.id,
        relation_id="cold-edge",
    )

    second = SystemRuntime(store, runtime_paths=RuntimePaths.temporary())

    assert second.list_application_objects(app.id) == [left, right]
    assert second.list_application_relations(app.id) == [relation]
