from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any

from pydantic import BaseModel

from n4x.graph.store import EdgeRecord, GraphShapeReport, NodeRef
from n4x.kernel.errors import ConcurrentGraphUpdateError
from n4x.kernel.models import ApplicationRelation, now_utc


@dataclass
class _TransactionState:
    depth: int
    snapshot: tuple[
        dict[str, dict[tuple[tuple[str, Any], ...], dict[str, Any]]],
        list[EdgeRecord],
        dict[tuple[str, str, str], ApplicationRelation],
    ]
    rollback_only: bool = False


class InMemoryGraphStore:
    """Transactional GraphStore adapter for contract and kernel tests only."""

    def __init__(self) -> None:
        self.nodes: dict[
            str, dict[tuple[tuple[str, Any], ...], dict[str, Any]]
        ] = {}
        self.edges: list[EdgeRecord] = []
        self.app_relations: dict[
            tuple[str, str, str], ApplicationRelation
        ] = {}
        self.reset_count = 0
        self._transaction_state: ContextVar[_TransactionState | None] = ContextVar(
            f"n4x_memory_transaction_{id(self)}", default=None
        )
        self._transaction_lock = RLock()
        self.bootstrap_schema()

    def bootstrap_schema(self) -> None:
        root = self.get_node("N4XRoot", {"id": "n4x"}) or {"id": "n4x"}
        self.upsert_node("N4XRoot", {"id": "n4x"}, root)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        state = self._transaction_state.get()
        if state is not None:
            state.depth += 1
            try:
                yield
            except BaseException:
                state.rollback_only = True
                raise
            finally:
                state.depth -= 1
            return

        with self._transaction_lock:
            state = _TransactionState(
                depth=1,
                snapshot=deepcopy((self.nodes, self.edges, self.app_relations)),
            )
            token = self._transaction_state.set(state)
            try:
                yield
            except BaseException:
                state.rollback_only = True
                raise
            finally:
                if state.rollback_only:
                    self.nodes, self.edges, self.app_relations = state.snapshot
                self._transaction_state.reset(token)

    def acquire_write_lock(self, ref: NodeRef) -> None:
        if self._transaction_state.get() is None:
            raise ConcurrentGraphUpdateError(
                "write locks require an active transaction"
            )

    def reset_dev_graph(self) -> None:
        self.nodes.clear()
        self.edges.clear()
        self.app_relations.clear()
        self.reset_count += 1
        self.bootstrap_schema()

    def node_count(self) -> int:
        return sum(len(nodes) for nodes in self.nodes.values())

    def upsert_node(self, label: str, identity: dict[str, Any], model: Any) -> None:
        values = (
            model.model_dump(mode="json") if isinstance(model, BaseModel) else dict(model)
        )
        values.update(identity)
        self.nodes.setdefault(label, {})[_identity_key(identity)] = deepcopy(values)

    def create_node(self, label: str, identity: dict[str, Any], model: Any) -> None:
        key = _identity_key(identity)
        if key in self.nodes.get(label, {}):
            raise ConcurrentGraphUpdateError(
                f"{label} already exists: {identity}"
            )
        self.upsert_node(label, identity, model)

    def get_node(
        self, label: str, identity: dict[str, Any]
    ) -> dict[str, Any] | None:
        value = self.nodes.get(label, {}).get(_identity_key(identity))
        return deepcopy(value) if value is not None else None

    def list_nodes(
        self, label: str, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        return [
            deepcopy(value)
            for value in self.nodes.get(label, {}).values()
            if all(value.get(key) == expected for key, expected in filters.items())
        ]

    def delete_node(self, label: str, identity: dict[str, Any]) -> None:
        self.nodes.get(label, {}).pop(_identity_key(identity), None)
        ref = NodeRef(label, identity)
        self.edges = [
            edge
            for edge in self.edges
            if edge.from_ref != ref and edge.to_ref != ref
        ]
        relation_ids = {
            (
                edge.props["application_id"],
                edge.props["data_space_id"],
                edge.props["id"],
            )
            for edge in self.edges
            if (
                edge.props.get("application_id"),
                edge.props.get("data_space_id"),
                edge.props.get("id"),
            )
            in self.app_relations
        }
        self.app_relations = {
            relation_id: relation
            for relation_id, relation in self.app_relations.items()
            if relation_id in relation_ids
        }

    def create_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef,
        props: dict[str, Any] | None = None,
    ) -> None:
        values = deepcopy(props or {})
        if edge_type == "HAS_FILE":
            existing = next(
                (
                    edge
                    for edge in self.edges
                    if edge.from_ref == from_ref
                    and edge.type == edge_type
                    and edge.props.get("path") == values.get("path")
                ),
                None,
            )
            if existing is None:
                self.edges.append(EdgeRecord(from_ref, edge_type, to_ref, values))
            else:
                existing.to_ref = to_ref
                existing.props.update(values)
            return
        existing = self._find_edge(from_ref, edge_type, to_ref)
        if existing is None:
            self.edges.append(EdgeRecord(from_ref, edge_type, to_ref, values))
        else:
            existing.props.update(values)

    def delete_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef | None = None,
        props: dict[str, Any] | None = None,
    ) -> None:
        self.edges = [
            edge
            for edge in self.edges
            if not (
                edge.from_ref == from_ref
                and edge.type == edge_type
                and (to_ref is None or edge.to_ref == to_ref)
                and (
                    props is None
                    or all(edge.props.get(key) == value for key, value in props.items())
                )
            )
        ]

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
        current = self.list_edges(from_ref=from_ref, edge_type=edge_type)
        if require_current_match and (
            (expected_to_ref is None and current)
            or (
                expected_to_ref is not None
                and (
                    len(current) != 1
                    or current[0].to_ref != expected_to_ref
                )
            )
        ):
            raise ConcurrentGraphUpdateError(
                f"stale {edge_type} replacement for {from_ref.label} "
                f"{from_ref.identity}"
            )
        self.delete_edge(from_ref, edge_type)
        self.create_edge(from_ref, edge_type, to_ref, props)

    def clear_single_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        *,
        expected_to_ref: NodeRef | None,
    ) -> None:
        current = self.list_edges(from_ref=from_ref, edge_type=edge_type)
        if (
            (expected_to_ref is None and current)
            or (
                expected_to_ref is not None
                and (
                    len(current) != 1
                    or current[0].to_ref != expected_to_ref
                )
            )
        ):
            raise ConcurrentGraphUpdateError(
                f"stale {edge_type} clear for {from_ref.label} "
                f"{from_ref.identity}"
            )
        self.delete_edge(from_ref, edge_type)

    def list_edges(
        self,
        from_ref: NodeRef | None = None,
        edge_type: str | None = None,
        to_ref: NodeRef | None = None,
    ) -> list[EdgeRecord]:
        return deepcopy(
            [
                edge
                for edge in self.edges
                if (from_ref is None or edge.from_ref == from_ref)
                and (edge_type is None or edge.type == edge_type)
                and (to_ref is None or edge.to_ref == to_ref)
            ]
        )

    def create_app_relation(self, relation: ApplicationRelation) -> None:
        if relation.physical_type is None:
            raise ValueError("application relation requires physical_type")
        key = (
            relation.application_id,
            relation.data_space_id,
            relation.id,
        )
        self.app_relations[key] = relation.model_copy(deep=True)
        from_ref = NodeRef(
            "ApplicationObject",
            {
                "application_id": relation.application_id,
                "data_space_id": relation.data_space_id,
                "id": relation.from_object_id,
            },
        )
        to_ref = NodeRef(
            "ApplicationObject",
            {
                "application_id": relation.application_id,
                "data_space_id": relation.data_space_id,
                "id": relation.to_object_id,
            },
        )
        props = relation.model_dump(mode="json")
        existing = next(
            (
                edge
                for edge in self.edges
                if edge.from_ref == from_ref
                and edge.type == relation.physical_type
                and edge.to_ref == to_ref
                and edge.props.get("id") == relation.id
            ),
            None,
        )
        if existing is None:
            self.edges.append(
                EdgeRecord(from_ref, relation.physical_type, to_ref, props)
            )
        else:
            existing.props = props

    def delete_app_relation(self, relation: ApplicationRelation) -> None:
        self.app_relations.pop(
            (relation.application_id, relation.data_space_id, relation.id),
            None,
        )
        self.edges = [
            edge
            for edge in self.edges
            if edge.props.get("id") != relation.id
            or edge.type != relation.physical_type
            or edge.props.get("application_id") != relation.application_id
            or edge.props.get("data_space_id") != relation.data_space_id
        ]

    def get_app_relation(
        self,
        application_id: str,
        data_space_id: str,
        relation_id: str,
    ) -> ApplicationRelation | None:
        relation = self.app_relations.get(
            (application_id, data_space_id, relation_id)
        )
        return None if relation is None else relation.model_copy(deep=True)

    def list_all_app_relations(self) -> list[ApplicationRelation]:
        return [
            relation.model_copy(deep=True)
            for relation in self.app_relations.values()
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
        relations = [
            relation.model_copy(deep=True)
            for relation in self.app_relations.values()
            if relation.application_id == application_id
            and relation.data_space_id == data_space_id
            and (
                relation_type_id is None
                or relation.relation_type_id == relation_type_id
            )
            and (
                from_object_id is None
                or relation.from_object_id == from_object_id
            )
            and (to_object_id is None or relation.to_object_id == to_object_id)
        ]
        return sorted(relations, key=lambda relation: relation.created_at)

    def run_cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        params = dict(params or {})
        compact = " ".join(query.split())
        returned = re.fullmatch(r"RETURN 1(?: AS (\w+))?", compact, flags=re.I)
        if returned:
            return [{returned.group(1) or "1": 1}]
        if re.search(r":ApplicationObject\b", compact):
            app_rows = self._cypher_application_object(compact, params)
            rel_type = re.search(r"\[(?:\w+:)?(APP_REL_[A-Z0-9_]+)\]", compact)
            if rel_type is not None:
                return self._cypher_app_rel(compact, params, rel_type.group(1))
            return app_rows
        rel_type = re.search(r"\[(?:\w+:)?(APP_REL_[A-Z0-9_]+)\]", compact)
        if rel_type is not None:
            return self._cypher_app_rel(compact, params, rel_type.group(1))
        if re.search(r"DETACH\s+DELETE", compact, flags=re.I):
            return []
        node = re.search(r"\((\w+):(\w+)", compact)
        label = params.pop("_label", None) or (node.group(2) if node else "CypherProbe")
        node_id = params.get("id")
        writes = bool(
            re.search(
                r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP)\b", compact, flags=re.I
            )
        )
        if writes and node_id:
            _reject_non_neo4j_property_params(params)
            existing = self.get_node(label, {"id": node_id}) or {"id": node_id}
            existing.update(params)
            self.upsert_node(label, {"id": node_id}, existing)
            return [_project_cypher_return(compact, existing)]
        if node_id:
            found = self.get_node(label, {"id": node_id})
            return [] if found is None else [_project_cypher_return(compact, found)]
        raise NotImplementedError(
            f"test memory adapter does not parse Cypher: {compact}"
        )

    def _cypher_application_object(
        self, compact: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        writes = bool(
            re.search(r"\b(CREATE|MERGE|SET|DELETE|REMOVE)\b", compact, flags=re.I)
        )
        identity = {
            key: params[key]
            for key in ("application_id", "data_space_id", "id")
            if key in params and params[key] is not None
        }
        if re.search(r"DETACH\s+DELETE", compact, flags=re.I):
            if {"application_id", "data_space_id", "id"} <= identity.keys():
                self.delete_node("ApplicationObject", identity)
            else:
                filters = {
                    key: value
                    for key, value in identity.items()
                    if key in {"application_id", "data_space_id"}
                }
                for node in self.list_nodes("ApplicationObject", filters):
                    self.delete_node(
                        "ApplicationObject",
                        {
                            "application_id": node["application_id"],
                            "data_space_id": node["data_space_id"],
                            "id": node["id"],
                        },
                    )
            return []
        if writes and {"application_id", "data_space_id", "id"} <= identity.keys():
            _reject_non_neo4j_property_params(params)
            existing = self.get_node("ApplicationObject", identity) or {}
            existing.update(identity)
            if "object_type_id" in params:
                existing["object_type_id"] = params["object_type_id"]
            if "values" in params:
                existing["values"] = params["values"]
            if "object_type_revision_id" in params:
                existing["object_type_revision_id"] = params["object_type_revision_id"]
            now = now_utc()
            existing.setdefault("created_at", now)
            existing["updated_at"] = now
            self.upsert_node("ApplicationObject", identity, existing)
            if "OWNS_OBJECT" in compact.upper():
                self.create_edge(
                    NodeRef(
                        "DataSpace",
                        {
                            "application_id": identity["application_id"],
                            "id": identity["data_space_id"],
                        },
                    ),
                    "OWNS_OBJECT",
                    NodeRef("ApplicationObject", identity),
                )
            if "INSTANCE_OF" in compact.upper() and existing.get("object_type_id"):
                self.create_edge(
                    NodeRef("ApplicationObject", identity),
                    "INSTANCE_OF",
                    NodeRef("ObjectType", {"id": existing["object_type_id"]}),
                )
            return [_project_cypher_return(compact, existing)]
        filters = {
            key: params[key]
            for key in ("application_id", "data_space_id", "id")
            if key in params and params[key] is not None
        }
        nodes = self.list_nodes("ApplicationObject", filters)
        if params.get("object_type_id") is not None and (
            "$object_type_id" in compact or "object_type_id" in compact
        ):
            nodes = [
                node
                for node in nodes
                if node.get("object_type_id") == params["object_type_id"]
            ]
        return [_project_cypher_return(compact, node) for node in nodes]

    def _cypher_app_rel(
        self, compact: str, params: dict[str, Any], physical_type: str
    ) -> list[dict[str, Any]]:
        application_id = params.get("application_id")
        data_space_id = params.get("data_space_id", "production")
        from_id = params.get("from_object_id") or params.get("from_id")
        to_id = params.get("to_object_id") or params.get("to_id")
        relation_id = params.get("id") or params.get("relation_id")
        writes = bool(
            re.search(r"\b(CREATE|MERGE|SET)\b", compact, flags=re.I)
        )
        deletes = bool(re.search(r"\bDELETE\b", compact, flags=re.I))
        if deletes and not writes:
            if relation_id and application_id:
                existing = self.get_app_relation(
                    application_id, data_space_id, relation_id
                )
                if existing is not None:
                    self.delete_app_relation(existing)
            return []
        if writes:
            if application_id is None or from_id is None or to_id is None:
                raise NotImplementedError(
                    f"test memory adapter needs relation endpoints: {compact}"
                )
            _reject_non_neo4j_property_params(params)
            relation = ApplicationRelation(
                id=relation_id or str(uuid.uuid4()),
                application_id=application_id,
                data_space_id=data_space_id,
                relation_type_id=params.get("relation_type_id")
                or "unknown.relation",
                relation_type_revision_id=params.get(
                    "relation_type_revision_id"
                ),
                physical_type=physical_type,
                from_object_id=from_id,
                to_object_id=to_id,
                values=_relation_values(params.get("values")),
            )
            self.create_app_relation(relation)
            return [
                {
                    "id": relation.id,
                    "relation_type_id": relation.relation_type_id,
                    "from_object_id": relation.from_object_id,
                    "to_object_id": relation.to_object_id,
                    "values": relation.values,
                }
            ]
        if application_id is None:
            return []
        return [
            {
                "id": relation.id,
                "relation_type_id": relation.relation_type_id,
                "from_object_id": relation.from_object_id,
                "to_object_id": relation.to_object_id,
                "values": relation.values,
            }
            for relation in self.list_app_relations(
                application_id,
                relation_type_id=params.get("relation_type_id"),
                from_object_id=from_id,
                to_object_id=to_id,
                data_space_id=data_space_id,
            )
        ]

    def validate_graph_shape(self) -> GraphShapeReport:
        return GraphShapeReport(
            errors=[
                f"legacy relationship type is not allowed: {edge.type}"
                for edge in self.edges
                if edge.type in {"N4X_KERNEL", "N4X_RELATION"}
            ]
        )

    def repair_graph_edges(self) -> GraphShapeReport:
        return self.validate_graph_shape()

    def has_edge(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef | None = None,
    ) -> bool:
        return bool(self.list_edges(from_ref, edge_type, to_ref))

    def edge_count(self, edge_type: str | None = None) -> int:
        return len(self.list_edges(edge_type=edge_type))

    def _find_edge(
        self, from_ref: NodeRef, edge_type: str, to_ref: NodeRef
    ) -> EdgeRecord | None:
        return next(
            (
                edge
                for edge in self.edges
                if edge.from_ref == from_ref
                and edge.type == edge_type
                and edge.to_ref == to_ref
            ),
            None,
        )


def _reject_non_neo4j_property(value: Any, name: str) -> None:
    if isinstance(value, dict):
        raise TypeError(
            "Property values can only be of primitive types or arrays thereof. "
            f"Encountered: Map for {name}."
        )
    if isinstance(value, list) and any(
        isinstance(item, dict | list) for item in value
    ):
        raise TypeError(
            "Property values can only be of primitive types or arrays thereof. "
            f"Encountered: nested list for {name}."
        )


def _reject_non_neo4j_property_params(params: dict[str, Any]) -> None:
    for key, value in params.items():
        _reject_non_neo4j_property(value, key)


def _relation_values(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    _reject_non_neo4j_property(raw, "values")
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _project_cypher_return(
    query: str, node: dict[str, Any]
) -> dict[str, Any]:
    returned = re.search(r"RETURN\s+(.+)$", query, flags=re.I)
    if returned is None:
        return dict(node)
    projected: dict[str, Any] = {}
    for part in returned.group(1).split(","):
        part = part.strip()
        aliased = re.match(r"(.+?)\s+AS\s+(\w+)$", part, flags=re.I)
        if aliased:
            expr, alias = aliased.group(1).strip(), aliased.group(2)
        else:
            expr, alias = part, part.split(".")[-1]
        if expr == "n":
            projected[alias] = dict(node)
        elif "." in expr:
            projected[alias] = node.get(expr.split(".")[-1])
        else:
            projected[alias] = node.get(expr, node)
    return projected


def _identity_key(identity: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted(identity.items()))
