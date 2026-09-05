from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, ContextManager, Protocol

from n4x.kernel.models import ApplicationRelation


@dataclass(frozen=True)
class NodeRef:
    label: str
    identity: dict[str, Any]


@dataclass
class EdgeRecord:
    from_ref: NodeRef
    type: str
    to_ref: NodeRef
    props: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphShapeReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class GraphStore(Protocol):
    def transaction(self) -> ContextManager[None]: ...

    def acquire_write_lock(self, ref: NodeRef) -> None: ...

    def bootstrap_schema(self) -> None: ...

    def reset_dev_graph(self) -> None: ...

    def node_count(self) -> int: ...

    def upsert_node(self, label: str, identity: dict[str, Any], model: Any) -> None: ...

    def create_node(self, label: str, identity: dict[str, Any], model: Any) -> None: ...

    def get_node(
        self, label: str, identity: dict[str, Any]
    ) -> dict[str, Any] | None: ...

    def list_nodes(
        self, label: str, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...

    def delete_node(self, label: str, identity: dict[str, Any]) -> None: ...

    def create_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef,
        props: dict[str, Any] | None = None,
    ) -> None: ...

    def delete_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef | None = None,
        props: dict[str, Any] | None = None,
    ) -> None: ...

    def replace_single_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef,
        props: dict[str, Any] | None = None,
        *,
        expected_to_ref: NodeRef | None = None,
        require_current_match: bool = False,
    ) -> None: ...

    def clear_single_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        *,
        expected_to_ref: NodeRef | None,
    ) -> None: ...

    def list_edges(
        self,
        from_ref: NodeRef | None = None,
        edge_type: str | None = None,
        to_ref: NodeRef | None = None,
    ) -> list[EdgeRecord]: ...

    def create_app_relation(self, relation: ApplicationRelation) -> None: ...

    def delete_app_relation(self, relation: ApplicationRelation) -> None: ...

    def get_app_relation(
        self,
        application_id: str,
        data_space_id: str,
        relation_id: str,
    ) -> ApplicationRelation | None: ...

    def list_all_app_relations(self) -> list[ApplicationRelation]: ...

    def list_app_relations(
        self,
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
        *,
        data_space_id: str = "production",
    ) -> list[ApplicationRelation]: ...

    def run_cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    def validate_graph_shape(self) -> GraphShapeReport: ...

    def repair_graph_edges(self) -> GraphShapeReport: ...


def relation_physical_type(relation_type_id: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", relation_type_id).strip("_").upper()
    if not sanitized:
        sanitized = "RELATION"
    digest = hashlib.sha256(relation_type_id.encode("utf-8")).hexdigest()[:8].upper()
    return f"APP_REL_{sanitized}_{digest}"


def node_ref(label: str, **identity: Any) -> NodeRef:
    return NodeRef(label, identity)


class Neo4jGraphStore:
    def __init__(self, graph: Any) -> None:
        self.graph = graph

    def bootstrap_schema(self) -> None:
        self.graph.bootstrap_schema()

    def transaction(self) -> ContextManager[None]:
        return self.graph.transaction()

    def acquire_write_lock(self, ref: NodeRef) -> None:
        self.graph.acquire_write_lock(ref.label, ref.identity)

    def reset_dev_graph(self) -> None:
        self.graph.reset_dev_graph()

    def node_count(self) -> int:
        rows = self.graph.run_cypher("MATCH (n) RETURN count(n) AS count")
        return int(rows[0]["count"])

    def upsert_node(self, label: str, identity: dict[str, Any], model: Any) -> None:
        self.graph.upsert_model(label, identity, model)

    def create_node(self, label: str, identity: dict[str, Any], model: Any) -> None:
        self.graph.create_model(label, identity, model)

    def get_node(
        self, label: str, identity: dict[str, Any]
    ) -> dict[str, Any] | None:
        return self.graph.fetch_node(label, identity)

    def list_nodes(
        self, label: str, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return self.graph.fetch_nodes(label, filters)

    def delete_node(self, label: str, identity: dict[str, Any]) -> None:
        self.graph.delete_model(label, identity)

    def create_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef,
        props: dict[str, Any] | None = None,
    ) -> None:
        self.graph.create_edge(
            from_ref.label,
            from_ref.identity,
            edge_type,
            to_ref.label,
            to_ref.identity,
            props,
        )

    def delete_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef | None = None,
        props: dict[str, Any] | None = None,
    ) -> None:
        self.graph.delete_edge(
            from_ref.label,
            from_ref.identity,
            edge_type,
            None if to_ref is None else to_ref.label,
            None if to_ref is None else to_ref.identity,
            props,
        )

    def replace_single_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef,
        props: dict[str, Any] | None = None,
        *,
        expected_to_ref: NodeRef | None = None,
        require_current_match: bool = False,
    ) -> None:
        self.graph.replace_single_edge(
            from_ref.label,
            from_ref.identity,
            edge_type,
            to_ref.label,
            to_ref.identity,
            props,
            expected_to_label=None if expected_to_ref is None else expected_to_ref.label,
            expected_to_identity=(
                None if expected_to_ref is None else expected_to_ref.identity
            ),
            require_current_match=require_current_match,
        )

    def clear_single_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        *,
        expected_to_ref: NodeRef | None,
    ) -> None:
        self.graph.clear_single_edge(
            from_ref.label,
            from_ref.identity,
            edge_type,
            expected_to_label=(
                None if expected_to_ref is None else expected_to_ref.label
            ),
            expected_to_identity=(
                None if expected_to_ref is None else expected_to_ref.identity
            ),
        )

    def list_edges(
        self,
        from_ref: NodeRef | None = None,
        edge_type: str | None = None,
        to_ref: NodeRef | None = None,
    ) -> list[EdgeRecord]:
        return [
            EdgeRecord(
                from_ref=NodeRef(row["from_label"], row["from_identity"]),
                type=row["type"],
                to_ref=NodeRef(row["to_label"], row["to_identity"]),
                props=row["props"],
            )
            for row in self.graph.fetch_edges(from_ref, edge_type, to_ref)
        ]

    def create_app_relation(self, relation: ApplicationRelation) -> None:
        self.graph.create_app_relation(relation)

    def delete_app_relation(self, relation: ApplicationRelation) -> None:
        self.graph.delete_app_relation(relation)

    def get_app_relation(
        self,
        application_id: str,
        data_space_id: str,
        relation_id: str,
    ) -> ApplicationRelation | None:
        row = self.graph.fetch_app_relation(
            application_id, data_space_id, relation_id
        )
        return (
            None
            if row is None
            else ApplicationRelation.model_validate(_decode_relation_row(row))
        )

    def list_all_app_relations(self) -> list[ApplicationRelation]:
        return [
            ApplicationRelation.model_validate(_decode_relation_row(row))
            for row in self.graph.fetch_all_app_relations()
        ]

    def list_app_relations(
        self,
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
        *,
        data_space_id: str = "production",
    ) -> list[ApplicationRelation]:
        return [
            ApplicationRelation.model_validate(_decode_relation_row(row))
            for row in self.graph.fetch_app_relations(
                application_id,
                data_space_id=data_space_id,
                relation_type_id=relation_type_id,
                from_object_id=from_object_id,
                to_object_id=to_object_id,
            )
        ]

    def run_cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self.graph.run_cypher(query, params)

    def validate_graph_shape(self) -> GraphShapeReport:
        errors: list[str] = []
        rows = self.graph.run_cypher(
            """
            MATCH ()-[r]->()
            WHERE type(r) IN ['N4X_KERNEL', 'N4X_RELATION']
            RETURN type(r) AS type, count(r) AS count
            """
        )
        for row in rows:
            errors.append(
                f"legacy relationship type is not allowed: {row['type']} ({row['count']})"
            )
        return GraphShapeReport(errors=errors)

    def repair_graph_edges(self) -> GraphShapeReport:
        return self.validate_graph_shape()


def _decode_relation_row(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    values = decoded.get("values")
    if isinstance(values, str):
        try:
            decoded["values"] = json.loads(values)
        except json.JSONDecodeError:
            pass
    return decoded
