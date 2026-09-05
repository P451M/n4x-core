"""Walk interned revision edges. Records do not store owner ids."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from n4x.graph.intern_gc import delete_interned_orphans
from n4x.graph.store import GraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.models import (
    ActionRevision,
    ApplicationRevision,
    ExperienceRevision,
    ExperienceSurface,
    ObjectTypeRevision,
    RelationTypeRevision,
    RuntimeDependency,
    SourceTree,
    SystemRevision,
    TestCase,
    TriggerRevision,
)

ModelT = TypeVar("ModelT", bound=BaseModel)

REVISION_LABELS = (
    ("ApplicationRevision", "revisions"),
    ("ExperienceRevision", "experience_revisions"),
    ("SystemRevision", "system_revisions"),
)


class RevisionBindings:
    def __init__(self, uow: GraphUnitOfWork) -> None:
        self.uow = uow
        self.store: GraphStore = uow.store
        self.records = uow.records

    def resolve(
        self, revision_id: str
    ) -> tuple[str, ApplicationRevision | ExperienceRevision | SystemRevision]:
        for label, collection_name in REVISION_LABELS:
            revision = getattr(self.records, collection_name).get(revision_id)
            if revision is not None:
                return label, revision
        raise KeyError(f"unknown revision: {revision_id}")

    def label(self, revision_id: str) -> str:
        return self.resolve(revision_id)[0]

    def tree(self, revision_id: str) -> SourceTree:
        label, _ = self.resolve(revision_id)
        edges = self.store.list_edges(
            node_ref(label, id=revision_id),
            "HAS_SOURCE_TREE",
        )
        if len(edges) != 1:
            raise ValueError(
                f"{label} {revision_id} must have exactly one HAS_SOURCE_TREE, "
                f"got {len(edges)}"
            )
        return self.records.source_trees[edges[0].to_ref.identity["id"]]

    def tree_id(self, revision_id: str) -> str:
        return self.tree(revision_id).id

    def set_tree(self, revision_id: str, tree_id: str) -> None:
        label, _ = self.resolve(revision_id)
        self.store.replace_single_edge(
            node_ref(label, id=revision_id),
            "HAS_SOURCE_TREE",
            node_ref("SourceTree", id=tree_id),
        )

    def action_revisions(self, application_revision_id: str) -> list[ActionRevision]:
        return self._targets(
            "ApplicationRevision",
            application_revision_id,
            "HAS_ACTION_REVISION",
            self.records.action_revisions,
        )

    def action_revision(
        self, application_revision_id: str, action_id: str
    ) -> ActionRevision:
        matches = [
            item
            for item in self.action_revisions(application_revision_id)
            if item.action_id == action_id
        ]
        if len(matches) != 1:
            raise KeyError(
                f"action {action_id} on {application_revision_id}: "
                f"expected 1 binding, got {len(matches)}"
            )
        return matches[0]

    def object_type_revisions(
        self, application_revision_id: str
    ) -> list[ObjectTypeRevision]:
        return self._targets(
            "ApplicationRevision",
            application_revision_id,
            "HAS_OBJECT_TYPE_REVISION",
            self.records.object_type_revisions,
        )

    def relation_type_revisions(
        self, application_revision_id: str
    ) -> list[RelationTypeRevision]:
        return self._targets(
            "ApplicationRevision",
            application_revision_id,
            "HAS_RELATION_TYPE_REVISION",
            self.records.relation_type_revisions,
        )

    def trigger_revisions(self, application_revision_id: str) -> list[TriggerRevision]:
        return self._targets(
            "ApplicationRevision",
            application_revision_id,
            "HAS_TRIGGER_REVISION",
            self.records.trigger_revisions,
        )

    def test_cases(self, application_revision_id: str) -> list[TestCase]:
        return self._targets(
            "ApplicationRevision",
            application_revision_id,
            "HAS_TEST",
            self.records.test_cases,
        )

    def dependencies(self, revision_id: str) -> list[RuntimeDependency]:
        label, _ = self.resolve(revision_id)
        return self._targets(
            label, revision_id, "DECLARES_DEPENDENCY", self.records.runtime_dependencies
        )

    def surfaces(self, experience_revision_id: str) -> list[ExperienceSurface]:
        return self._targets(
            "ExperienceRevision",
            experience_revision_id,
            "DECLARES_SURFACE",
            self.records.experience_surfaces,
        )

    def surface(
        self, experience_revision_id: str, surface_id: str
    ) -> ExperienceSurface:
        matches = [
            item
            for item in self.surfaces(experience_revision_id)
            if item.surface_id == surface_id
        ]
        if len(matches) != 1:
            raise KeyError(
                f"surface {surface_id} on {experience_revision_id}: "
                f"expected 1 binding, got {len(matches)}"
            )
        return matches[0]

    def copy_edges(self, parent_id: str, draft_id: str, edge_types: tuple[str, ...]) -> None:
        parent_label, _ = self.resolve(parent_id)
        draft_label, _ = self.resolve(draft_id)
        if parent_label != draft_label:
            raise ValueError("cannot copy edges between different revision kinds")
        draft_ref = node_ref(draft_label, id=draft_id)
        for edge_type in edge_types:
            for edge in self.store.list_edges(
                node_ref(parent_label, id=parent_id), edge_type
            ):
                self.store.create_edge(draft_ref, edge_type, edge.to_ref)

    def payload(self, revision_id: str) -> dict[str, Any]:
        _, revision = self.resolve(revision_id)
        tree = self.tree(revision_id)
        data = revision.model_dump(mode="json")
        data["source_tree_id"] = tree.id
        data["tree_status"] = tree.status
        return data

    def named(
        self,
        revision_id: str,
        edge_type: str,
        collection: Any,
        name_field: str,
        name: str,
    ) -> Any | None:
        label, _ = self.resolve(revision_id)
        matches = [
            item
            for item in self._targets(label, revision_id, edge_type, collection)
            if getattr(item, name_field) == name
        ]
        if len(matches) > 1:
            raise ValueError(
                f"duplicate {name_field} {name} on {revision_id}"
            )
        return matches[0] if matches else None

    def replace_named(
        self,
        revision_id: str,
        edge_type: str,
        current_id: str | None,
        next_id: str,
        target_label: str,
    ) -> None:
        label, _ = self.resolve(revision_id)
        revision_ref = node_ref(label, id=revision_id)
        if current_id is not None and current_id != next_id:
            self.store.delete_edge(
                revision_ref,
                edge_type,
                node_ref(target_label, id=current_id),
            )
        if current_id != next_id:
            self.store.create_edge(
                revision_ref,
                edge_type,
                node_ref(target_label, id=next_id),
            )
            delete_interned_orphans(self.uow)

    def remove_named(
        self, revision_id: str, edge_type: str, target_label: str, target_id: str
    ) -> None:
        label, _ = self.resolve(revision_id)
        self.store.delete_edge(
            node_ref(label, id=revision_id),
            edge_type,
            node_ref(target_label, id=target_id),
        )
        delete_interned_orphans(self.uow)

    def _targets(
        self,
        from_label: str,
        from_id: str,
        edge_type: str,
        collection: Any,
    ) -> list[ModelT]:
        items = [
            collection[edge.to_ref.identity["id"]]
            for edge in self.store.list_edges(
                node_ref(from_label, id=from_id), edge_type
            )
        ]
        return items
