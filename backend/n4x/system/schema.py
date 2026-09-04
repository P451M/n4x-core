"""Application schema owned by the System, not the host."""

from __future__ import annotations

from typing import Any

from n4x.graph.store import node_ref, relation_physical_type
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.hash import sha256_json
from n4x.kernel.models import (
    ObjectType,
    ObjectTypeRevision,
    RelationType,
    RelationTypeRevision,
)
from n4x.graph.service_base import transactional
from n4x.system.drafts import Drafts


class Schema:
    def __init__(self, uow: GraphUnitOfWork) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.drafts = Drafts(self.records)

    @transactional
    def create_object_type(
        self,
        application_revision_id: str,
        object_type_id: str,
        *,
        name: str,
        properties: dict[str, Any] | None = None,
        required: list[str] | None = None,
    ) -> ObjectTypeRevision:
        app_revision = self.drafts.require_application(application_revision_id)
        stable = self.records.object_types.get(object_type_id) or ObjectType(
            id=object_type_id, application_id=app_revision.application_id
        )
        self.records.object_types.save(stable)
        existing = self._draft_revision(
            self.records.object_type_revisions.values(),
            application_revision_id,
            "object_type_id",
            object_type_id,
        )
        revision = ObjectTypeRevision(
            id=(
                existing.id
                if existing is not None
                else f"{object_type_id}@{self._next_revision(object_type_id)}"
            ),
            object_type_id=object_type_id,
            application_revision_id=application_revision_id,
            name=name,
            properties=properties or {},
            required=required or [],
            content_hash=sha256_json(
                {
                    "name": name,
                    "properties": properties or {},
                    "required": required or [],
                }
            ),
        )
        self.records.object_type_revisions.save(revision)
        self.store.create_edge(
            node_ref("Application", id=app_revision.application_id),
            "DEFINES_OBJECT_TYPE",
            node_ref("ObjectType", id=stable.id),
        )
        self.store.create_edge(
            node_ref("ObjectType", id=stable.id),
            "HAS_REVISION",
            node_ref("ObjectTypeRevision", id=revision.id),
        )
        return revision

    @transactional
    def create_relation_type(
        self,
        application_revision_id: str,
        relation_type_id: str,
        *,
        name: str,
        from_object_type_id: str,
        to_object_type_id: str,
        properties: dict[str, Any] | None = None,
    ) -> RelationTypeRevision:
        app_revision = self.drafts.require_application(application_revision_id)
        stable = self.records.relation_types.get(relation_type_id) or RelationType(
            id=relation_type_id, application_id=app_revision.application_id
        )
        self.records.relation_types.save(stable)
        physical_type = relation_physical_type(relation_type_id)
        existing = self._draft_revision(
            self.records.relation_type_revisions.values(),
            application_revision_id,
            "relation_type_id",
            relation_type_id,
        )
        revision = RelationTypeRevision(
            id=(
                existing.id
                if existing is not None
                else (
                    f"{relation_type_id}@"
                    f"{self._next_relation_revision(relation_type_id)}"
                )
            ),
            relation_type_id=relation_type_id,
            application_revision_id=application_revision_id,
            name=name,
            from_object_type_id=from_object_type_id,
            to_object_type_id=to_object_type_id,
            physical_type=physical_type,
            properties=properties or {},
            content_hash=sha256_json(
                {
                    "name": name,
                    "from_object_type_id": from_object_type_id,
                    "to_object_type_id": to_object_type_id,
                    "physical_type": physical_type,
                    "properties": properties or {},
                }
            ),
        )
        self.records.relation_type_revisions.save(revision)
        self.store.create_edge(
            node_ref("Application", id=app_revision.application_id),
            "DEFINES_RELATION_TYPE",
            node_ref("RelationType", id=stable.id),
        )
        self.store.create_edge(
            node_ref("RelationType", id=stable.id),
            "HAS_REVISION",
            node_ref("RelationTypeRevision", id=revision.id),
        )
        self.store.replace_single_edge(
            node_ref("RelationTypeRevision", id=revision.id),
            "FROM_TYPE",
            node_ref("ObjectType", id=from_object_type_id),
        )
        self.store.replace_single_edge(
            node_ref("RelationTypeRevision", id=revision.id),
            "TO_TYPE",
            node_ref("ObjectType", id=to_object_type_id),
        )
        return revision

    def active_or_latest_relation_revision(
        self, application_id: str, relation_type_id: str
    ) -> RelationTypeRevision:
        stable = self.records.relation_types[relation_type_id]
        if stable.application_id != application_id:
            raise ValueError("relation type belongs to a different application")
        if stable.active_revision_id is not None:
            return self.records.relation_type_revisions[stable.active_revision_id]
        candidates = [
            revision
            for revision in self.records.relation_type_revisions.values()
            if revision.relation_type_id == relation_type_id
        ]
        if not candidates:
            raise KeyError(relation_type_id)
        return sorted(candidates, key=lambda item: item.created_at)[-1]

    def _next_revision(self, object_type_id: str) -> int:
        return (
            len(
                [
                    item
                    for item in self.records.object_type_revisions.values()
                    if item.object_type_id == object_type_id
                ]
            )
            + 1
        )

    def _next_relation_revision(self, relation_type_id: str) -> int:
        return (
            len(
                [
                    item
                    for item in self.records.relation_type_revisions.values()
                    if item.relation_type_id == relation_type_id
                ]
            )
            + 1
        )

    @staticmethod
    def _draft_revision(
        revisions: list,
        application_revision_id: str,
        owner_field: str,
        stable_id: str,
    ):
        matches = [
            revision
            for revision in revisions
            if revision.application_revision_id == application_revision_id
            and getattr(revision, owner_field) == stable_id
        ]
        if len(matches) > 1:
            raise ValueError(
                f"duplicate {owner_field} revisions on draft "
                f"{application_revision_id}: {stable_id}"
            )
        return matches[0] if matches else None
