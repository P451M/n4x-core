from __future__ import annotations

from n4x.runtime.actions import RuntimePaths
from n4x.system.runtime import SystemRuntime
from n4x.testing.graph_store import InMemoryGraphStore


def test_system_wipe_then_import_restores_application() -> None:
    paths = RuntimePaths.temporary()
    runtime = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    try:
        runtime.applications.create("portable", "Portable")
        revision = runtime.applications.create_revision("portable")
        runtime.schema.create_object_type(
            revision.id,
            "portable.Note",
            name="Note",
            properties={"title": {"type": "string"}},
            required=["title"],
        )
        runtime.activation.activate(revision.id)
        exported = runtime.packages.export("application", "portable", "wipe.n4xp")
        assert exported["application_ids"] == ["portable"]
        runtime.reset_dev_graph()
        assert runtime.applications.list() == []
        installed = runtime.packages.import_package("wipe.n4xp")
        assert installed["attempt"]["status"] == "succeeded"
        assert runtime.uow.records.applications["portable"].status == "triggers_paused"
        object_types = [
            item
            for item in runtime.uow.records.object_types.values()
            if item.id == "portable.Note"
        ]
        assert object_types
    finally:
        runtime.close()
