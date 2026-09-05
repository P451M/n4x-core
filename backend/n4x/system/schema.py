"""Application schema owned by the System, not the host."""

from __future__ import annotations

from typing import Any

from n4x.graph.bindings import RevisionBindings
from n4x.graph.store import node_ref, relation_physical_type
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.intern import object_type_revision_id, relation_type_revision_id
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
        self.bindings = RevisionBindings(uow)

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
        payload = {
            "object_type_id": object_type_id,
            "name": name,
            "properties": properties or {},
            "required": required or [],
        }
        interned_id = object_type_revision_id(payload)
        revision = self.records.object_type_revisions.get(interned_id)
        if revision is None:
            revision = ObjectTypeRevision(
                id=interned_id,
                object_type_id=object_type_id,
                name=name,
                properties=properties or {},
                required=required or [],
                content_hash=interned_id,
            )
            self.records.object_type_revisions.save(revision)
        stable = self.records.object_types.get(object_type_id) or ObjectType(
            id=object_type_id, application_id=app_revision.application_id
        )
        self.records.object_types.save(stable)
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
        current = self.bindings.named(
            app_revision.id,
            "HAS_OBJECT_TYPE_REVISION",
            self.records.object_type_revisions,
            "object_type_id",
            object_type_id,
        )
        self.bindings.replace_named(
            app_revision.id,
            "HAS_OBJECT_TYPE_REVISION",
            None if current is None else current.id,
            revision.id,
            "ObjectTypeRevision",
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
        physical_type = relation_physical_type(relation_type_id)
        payload = {
            "relation_type_id": relation_type_id,
            "name": name,
            "from_object_type_id": from_object_type_id,
            "to_object_type_id": to_object_type_id,
            "physical_type": physical_type,
            "properties": properties or {},
        }
        interned_id = relation_type_revision_id(payload)
        revision = self.records.relation_type_revisions.get(interned_id)
        if revision is None:
            revision = RelationTypeRevision(
                id=interned_id,
                relation_type_id=relation_type_id,
                name=name,
                from_object_type_id=from_object_type_id,
                to_object_type_id=to_object_type_id,
                physical_type=physical_type,
                properties=properties or {},
                content_hash=interned_id,
            )
            self.records.relation_type_revisions.save(revision)
        stable = self.records.relation_types.get(relation_type_id) or RelationType(
            id=relation_type_id, application_id=app_revision.application_id
        )
        self.records.relation_types.save(stable)
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
        current = self.bindings.named(
            app_revision.id,
            "HAS_RELATION_TYPE_REVISION",
            self.records.relation_type_revisions,
            "relation_type_id",
            relation_type_id,
        )
        self.bindings.replace_named(
            app_revision.id,
            "HAS_RELATION_TYPE_REVISION",
            None if current is None else current.id,
            revision.id,
            "RelationTypeRevision",
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
        application = self.records.applications[application_id]
        if application.active_revision_id is not None:
            matches = [
                item
                for item in self.bindings.relation_type_revisions(
                    application.active_revision_id
                )
                if item.relation_type_id == relation_type_id
            ]
            if matches:
                return matches[0]
        candidates = [
            revision
            for revision in self.records.relation_type_revisions.values()
            if revision.relation_type_id == relation_type_id
        ]
        if not candidates:
            raise KeyError(relation_type_id)
        return sorted(candidates, key=lambda item: item.created_at)[-1]
