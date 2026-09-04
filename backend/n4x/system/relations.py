"""Application relations owned by the System."""

from __future__ import annotations

import uuid
from typing import Any

from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import ApplicationRelation, now_utc
from n4x.graph.service_base import transactional
from n4x.system.schema import Schema


class Relations:
    def __init__(self, uow: GraphUnitOfWork, schema: Schema) -> None:
        self.uow = uow
        self.records = uow.records
        self.schema = schema

    @transactional
    def create(
        self,
        application_id: str,
        relation_type_id: str,
        from_object_id: str,
        to_object_id: str,
        *,
        values: dict[str, Any] | None = None,
        relation_id: str | None = None,
        data_space_id: str = "production",
    ) -> ApplicationRelation:
        relation_type = self.records.relation_types.get(relation_type_id)
        if relation_type is None or relation_type.application_id != application_id:
            raise ValidationFailure(
                f"relation type does not belong to application: {relation_type_id}"
            )
        from_object = self.uow.objects.get(
            application_id, from_object_id, data_space_id
        )
        to_object = self.uow.objects.get(
            application_id, to_object_id, data_space_id
        )
        if from_object is None or from_object.application_id != application_id:
            raise ValidationFailure(
                f"from object does not belong to application: {from_object_id}"
            )
        if to_object is None or to_object.application_id != application_id:
            raise ValidationFailure(
                f"to object does not belong to application: {to_object_id}"
            )
        revision = self.schema.active_or_latest_relation_revision(
            application_id, relation_type_id
        )
        if from_object.object_type_id != revision.from_object_type_id:
            raise ValidationFailure(
                f"relation source type mismatch: expected "
                f"{revision.from_object_type_id}, got {from_object.object_type_id}"
            )
        if to_object.object_type_id != revision.to_object_type_id:
            raise ValidationFailure(
                f"relation target type mismatch: expected "
                f"{revision.to_object_type_id}, got {to_object.object_type_id}"
            )
        relation_id = relation_id or str(uuid.uuid4())
        if self.uow.relations.get(application_id, relation_id, data_space_id) is not None:
            raise ValidationFailure(f"relation already exists: {relation_id}")
        now = now_utc()
        relation = ApplicationRelation(
            id=relation_id,
            application_id=application_id,
            data_space_id=data_space_id,
            relation_type_id=relation_type_id,
            relation_type_revision_id=revision.id,
            physical_type=revision.physical_type,
            from_object_id=from_object_id,
            to_object_id=to_object_id,
            values=values or {},
            created_at=now,
            updated_at=now,
        )
        self.uow.relations.save(relation)
        return relation

    @transactional
    def list(
        self,
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
        *,
        data_space_id: str = "production",
    ) -> list[ApplicationRelation]:
        return sorted(
            self.uow.relations.list(
                application_id,
                relation_type_id,
                from_object_id,
                to_object_id,
                data_space_id=data_space_id,
            ),
            key=lambda item: item.created_at,
        )

    @transactional
    def delete(
        self,
        application_id: str,
        relation_id: str,
        *,
        data_space_id: str = "production",
    ) -> None:
        relation = self.uow.relations.get(
            application_id, relation_id, data_space_id
        )
        if relation is None:
            raise KeyError(relation_id)
        if relation.application_id != application_id:
            raise ValidationFailure(
                f"relation does not belong to application: {relation_id}"
            )
        self.uow.relations.delete(relation)
