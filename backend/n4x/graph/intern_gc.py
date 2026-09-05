"""Collect interned nodes that no remaining revision or in-flight record pins.

Interned trees, blobs, and declarations are Kernel graph authority and are
shared by content hash. An Application cannot see another app's intern, so
this cannot live in app code.
"""

from __future__ import annotations

from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork

_INTERNED_COLLECTIONS = (
    ("source_trees", "SourceTree"),
    ("source_contents", "SourceContent"),
    ("action_revisions", "ActionRevision"),
    ("object_type_revisions", "ObjectTypeRevision"),
    ("relation_type_revisions", "RelationTypeRevision"),
    ("trigger_revisions", "TriggerRevision"),
    ("test_cases", "TestCase"),
    ("runtime_dependencies", "RuntimeDependency"),
    ("experience_surfaces", "ExperienceSurface"),
)

# Stable nouns keep HAS_REVISION catalog edges. Those must not pin interned
# declarations after no revision still binds them.
_KEEPER_LABELS = {
    "ApplicationRevision",
    "ExperienceRevision",
    "SystemRevision",
    "SourceTree",
}


def delete_interned_orphans(uow: GraphUnitOfWork) -> dict[str, list[str]]:
    deleted: dict[str, list[str]] = {}
    while True:
        batch = _orphans(uow)
        if not batch:
            return deleted
        for collection_name, label, record_id in batch:
            getattr(uow.records, collection_name).delete(record_id)
            deleted.setdefault(label, []).append(record_id)


def _orphans(uow: GraphUnitOfWork) -> list[tuple[str, str, str]]:
    pinned = _pinned_action_revision_ids(uow)
    found: list[tuple[str, str, str]] = []
    for collection_name, label in _INTERNED_COLLECTIONS:
        for item in getattr(uow.records, collection_name).values():
            inbound = uow.store.list_edges(to_ref=node_ref(label, id=item.id))
            if any(edge.from_ref.label in _KEEPER_LABELS for edge in inbound):
                continue
            if label == "ActionRevision" and item.id in pinned:
                continue
            found.append((collection_name, label, item.id))
    return found


def _pinned_action_revision_ids(uow: GraphUnitOfWork) -> set[str]:
    pinned: set[str] = set()
    for route in uow.records.callback_routes.values():
        pinned.add(route.target_action_revision_id)
    for invocation in uow.records.invocations.values():
        pinned.add(invocation.action_revision_id)
    for job in uow.records.job_records.values():
        pinned.add(job.action_revision_id)
    return {item for item in pinned if item}
