"""Application data-space identity owned by the System."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import (
    ApplicationObject,
    DataSpace,
    DataSpaceCloneSpec,
)
from n4x.graph.service_base import transactional

_WRITE_CLAUSE = re.compile(
    r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b",
    re.IGNORECASE,
)


class DataSpaceService:
    """Owns generic Application data-scope identity and lifecycle."""

    def __init__(self, uow: GraphUnitOfWork) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records

    @transactional
    def create_development(
        self,
        application_id: str,
        *,
        data_space_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> DataSpace:
        self.records.applications[application_id]
        data_space = DataSpace(
            id=data_space_id or str(uuid.uuid4()),
            application_id=application_id,
            kind="development",
            expires_at=expires_at,
        )
        key = (application_id, data_space.id)
        if self.records.data_spaces.get(key) is not None:
            raise ValueError(f"DataSpace already exists: {key}")
        self.records.data_spaces.save(data_space)
        self.store.create_edge(
            node_ref("Application", id=application_id),
            "HAS_DATA_SPACE",
            node_ref(
                "DataSpace",
                application_id=application_id,
                id=data_space.id,
            ),
        )
        return data_space

    def get(self, application_id: str, data_space_id: str) -> DataSpace:
        data_space = self.records.data_spaces.get(
            (application_id, data_space_id)
        )
        if data_space is None:
            raise KeyError((application_id, data_space_id))
        return data_space

    def production(self, application_id: str) -> DataSpace:
        data_space = self.get(application_id, "production")
        if data_space.kind != "production":
            raise ValueError(
                f"Application production DataSpace is invalid: {application_id}"
            )
        return data_space

    def list(self, application_id: str) -> list[DataSpace]:
        return [
            item
            for item in self.records.data_spaces.values()
            if item.application_id == application_id
        ]

    @transactional
    def clone_from_production(
        self,
        application_id: str,
        target_data_space_id: str,
        spec: DataSpaceCloneSpec | dict[str, Any] | None = None,
    ) -> dict[str, int]:
        clone_spec = DataSpaceCloneSpec.model_validate(spec or {})
        target = self.get(application_id, target_data_space_id)
        if target.kind != "development":
            raise ValidationFailure(
                "clone target must be a development DataSpace"
            )
        if self.uow.objects.list(
            application_id,
            data_space_id=target_data_space_id,
        ):
            raise ValidationFailure("clone target DataSpace must be empty")

        selected = self._select_production_objects(
            application_id, clone_spec
        )
        selected_ids = {item.id for item in selected}
        relations = [
            relation
            for relation in self.uow.relations.list(
                application_id,
                data_space_id="production",
            )
            if relation.from_object_id in selected_ids
            and relation.to_object_id in selected_ids
        ]
        if len(relations) > clone_spec.max_relations:
            raise ValidationFailure(
                "clone relation limit exceeded; narrow the selection query"
            )

        for source in selected:
            cloned = source.model_copy(
                update={"data_space_id": target_data_space_id}
            )
            self.uow.objects.save(cloned)
            self.uow.objects.attach(cloned)
        for source in relations:
            self.uow.relations.save(
                source.model_copy(
                    update={"data_space_id": target_data_space_id}
                )
            )
        return {
            "object_count": len(selected),
            "relation_count": len(relations),
        }

    @transactional
    def purge_development(
        self, application_id: str, data_space_id: str
    ) -> None:
        data_space = self.get(application_id, data_space_id)
        if data_space.kind != "development":
            raise ValidationFailure(
                "only development DataSpaces may be purged"
            )
        for relation in self.uow.relations.list(
            application_id,
            data_space_id=data_space_id,
        ):
            self.uow.relations.delete(relation)
        for item in self.uow.objects.list(
            application_id,
            data_space_id=data_space_id,
        ):
            self.uow.objects.delete(
                application_id,
                item.id,
                data_space_id,
            )
        self.records.data_spaces.delete((application_id, data_space_id))

    def _select_production_objects(
        self,
        application_id: str,
        spec: DataSpaceCloneSpec,
    ) -> list[ApplicationObject]:
        production = self.uow.objects.list(
            application_id,
            data_space_id="production",
        )
        by_id = {item.id: item for item in production}
        if spec.selection_query is None:
            return sorted(
                production,
                key=lambda item: item.updated_at,
                reverse=True,
            )[: spec.max_nodes]
        query = spec.selection_query.strip().rstrip(";")
        if not query or _WRITE_CLAUSE.search(query):
            raise ValidationFailure(
                "clone selection_query must be non-empty read-only Cypher"
            )
        wrapped = (
            "CALL {\n"
            f"{query}\n"
            "}\n"
            "RETURN object_id LIMIT $n4x_clone_limit"
        )
        rows = self.store.run_cypher(
            wrapped,
            {
                **spec.params,
                "application_id": application_id,
                "data_space_id": "production",
                "n4x_clone_limit": spec.max_nodes + 1,
            },
        )
        if len(rows) > spec.max_nodes:
            raise ValidationFailure(
                "clone object limit exceeded; narrow the selection query"
            )
        selected = []
        seen = set()
        for row in rows:
            object_id = row.get("object_id")
            if not isinstance(object_id, str):
                raise ValidationFailure(
                    "clone selection_query must return object_id strings"
                )
            if object_id in seen:
                continue
            source = by_id.get(object_id)
            if source is None:
                raise ValidationFailure(
                    "clone selection_query returned an object outside the "
                    "production Application DataSpace"
                )
            seen.add(object_id)
            selected.append(source)
        return selected
