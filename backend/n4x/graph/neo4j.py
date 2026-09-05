from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import TransientError
from pydantic import BaseModel

from n4x.kernel.errors import ConcurrentGraphUpdateError, TransientGraphConflictError


class Neo4jConfigError(ValueError):
    """Raised when required production Neo4j settings are missing."""


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    user: str
    password: str
    database: str = "neo4j"

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> Neo4jConfig:
        source = os.environ if environ is None else environ
        names = {
            "uri": "N4X_NEO4J_URI",
            "user": "N4X_NEO4J_USER",
            "password": "N4X_NEO4J_PASSWORD",
            "database": "N4X_NEO4J_DATABASE",
        }
        missing = [
            env_name
            for env_name in names.values()
            if not source.get(env_name, "").strip()
        ]
        if missing:
            raise Neo4jConfigError(
                "missing required Neo4j configuration: "
                + ", ".join(missing)
            )
        return cls(**{field: source[env_name] for field, env_name in names.items()})


class Neo4jGraph:
    """Thin Neo4j adapter for runtime connectivity and kernel schema bootstrap."""

    def __init__(self, config: Neo4jConfig) -> None:
        self.config = config
        self.driver = GraphDatabase.driver(
            config.uri, auth=(config.user, config.password)
        )
        self._transaction_context: ContextVar[tuple[Any, Any] | None] = ContextVar(
            f"n4x_neo4j_transaction_{id(self)}", default=None
        )

    def close(self) -> None:
        self.driver.close()

    def verify_connectivity(self) -> None:
        self.driver.verify_connectivity()

    @staticmethod
    def schema_statements() -> list[str]:
        return [
            "CREATE CONSTRAINT n4x_root_id IF NOT EXISTS FOR (n:N4XRoot) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_system_id IF NOT EXISTS FOR (n:System) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_system_revision_id IF NOT EXISTS FOR (n:SystemRevision) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_application_id IF NOT EXISTS FOR (n:Application) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_data_space_identity IF NOT EXISTS FOR (n:DataSpace) REQUIRE (n.application_id, n.id) IS UNIQUE",
            "CREATE CONSTRAINT n4x_development_deployment_id IF NOT EXISTS FOR (n:DevelopmentDeployment) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_application_revision_id IF NOT EXISTS FOR (n:ApplicationRevision) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_experience_id IF NOT EXISTS FOR (n:Experience) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_experience_revision_id IF NOT EXISTS FOR (n:ExperienceRevision) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_experience_surface_id IF NOT EXISTS FOR (n:ExperienceSurface) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_experience_validation_report_id IF NOT EXISTS FOR (n:ExperienceValidationReport) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_authoring_guide_id IF NOT EXISTS FOR (n:AuthoringGuide) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_authoring_guide_revision_id IF NOT EXISTS FOR (n:AuthoringGuideRevision) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_ui_theme_id IF NOT EXISTS FOR (n:UiTheme) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_ui_theme_revision_id IF NOT EXISTS FOR (n:UiThemeRevision) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_app_blueprint_id IF NOT EXISTS FOR (n:AppBlueprint) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_blueprint_revision_id IF NOT EXISTS FOR (n:BlueprintRevision) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_source_tree_id IF NOT EXISTS FOR (n:SourceTree) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_source_content_id IF NOT EXISTS FOR (n:SourceContent) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_application_object_identity IF NOT EXISTS FOR (n:ApplicationObject) REQUIRE (n.application_id, n.data_space_id, n.id) IS UNIQUE",
            "CREATE CONSTRAINT n4x_object_type_id IF NOT EXISTS FOR (n:ObjectType) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_object_type_revision_id IF NOT EXISTS FOR (n:ObjectTypeRevision) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_relation_type_id IF NOT EXISTS FOR (n:RelationType) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_relation_type_revision_id IF NOT EXISTS FOR (n:RelationTypeRevision) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_secret_reference_id IF NOT EXISTS FOR (n:SecretReference) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_secret_reference_uri IF NOT EXISTS FOR (n:SecretReference) REQUIRE n.uri IS UNIQUE",
            "CREATE CONSTRAINT n4x_credential_record_id IF NOT EXISTS FOR (n:CredentialRecord) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_callback_route_id IF NOT EXISTS FOR (n:CallbackRoute) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_action_id IF NOT EXISTS FOR (n:Action) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_action_revision_id IF NOT EXISTS FOR (n:ActionRevision) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_trigger_id IF NOT EXISTS FOR (n:Trigger) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_trigger_revision_id IF NOT EXISTS FOR (n:TriggerRevision) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_job_record_id IF NOT EXISTS FOR (n:JobRecord) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_job_attempt_id IF NOT EXISTS FOR (n:JobAttempt) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_cypher_audit_record_id IF NOT EXISTS FOR (n:CypherAuditRecord) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_invocation_id IF NOT EXISTS FOR (n:Invocation) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_test_case_id IF NOT EXISTS FOR (n:TestCase) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_graph_checkpoint_id IF NOT EXISTS FOR (n:GraphCheckpoint) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_checkpoint_snapshot_id IF NOT EXISTS FOR (n:CheckpointSnapshot) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_checkpoint_blob_id IF NOT EXISTS FOR (n:CheckpointBlob) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_runtime_dependency_id IF NOT EXISTS FOR (n:RuntimeDependency) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_python_environment_id IF NOT EXISTS FOR (n:PythonEnvironment) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_javascript_environment_id IF NOT EXISTS FOR (n:JavaScriptEnvironment) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_build_invocation_id IF NOT EXISTS FOR (n:BuildInvocation) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_build_artifact_id IF NOT EXISTS FOR (n:BuildArtifact) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_validation_report_id IF NOT EXISTS FOR (n:ValidationReport) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT n4x_package_import_attempt_id IF NOT EXISTS FOR (n:PackageImportAttempt) REQUIRE n.id IS UNIQUE",
            "CREATE INDEX n4x_source_content_hash IF NOT EXISTS FOR (n:SourceContent) ON (n.id)",
            "CREATE INDEX n4x_authoring_guide_revision_scope IF NOT EXISTS FOR (n:AuthoringGuideRevision) ON (n.guide_id, n.release_version)",
            "CREATE INDEX n4x_ui_theme_revision_scope IF NOT EXISTS FOR (n:UiThemeRevision) ON (n.theme_id, n.release_version)",
            "CREATE INDEX n4x_blueprint_revision_scope IF NOT EXISTS FOR (n:BlueprintRevision) ON (n.blueprint_id, n.status)",
            "CREATE INDEX n4x_source_tree_status IF NOT EXISTS FOR (n:SourceTree) ON (n.status, n.tree_hash)",
            "CREATE INDEX n4x_data_space_kind IF NOT EXISTS FOR (n:DataSpace) ON (n.application_id, n.kind)",
            "CREATE INDEX n4x_development_deployment_status IF NOT EXISTS FOR (n:DevelopmentDeployment) ON (n.status, n.expires_at)",
            "CREATE INDEX n4x_application_object_scope IF NOT EXISTS FOR (n:ApplicationObject) ON (n.application_id, n.data_space_id, n.object_type_id)",
            "CREATE INDEX n4x_package_import_attempt_hash IF NOT EXISTS FOR (n:PackageImportAttempt) ON (n.package_hash)",
            "CREATE INDEX n4x_object_type_revision_type IF NOT EXISTS FOR (n:ObjectTypeRevision) ON (n.object_type_id)",
            "CREATE INDEX n4x_relation_type_revision_type IF NOT EXISTS FOR (n:RelationTypeRevision) ON (n.relation_type_id)",
            "CREATE INDEX n4x_credential_record_scope IF NOT EXISTS FOR (n:CredentialRecord) ON (n.application_id, n.provider)",
            "CREATE INDEX n4x_callback_route_state IF NOT EXISTS FOR (n:CallbackRoute) ON (n.state, n.status)",
            "CREATE INDEX n4x_experience_revision_scope IF NOT EXISTS FOR (n:ExperienceRevision) ON (n.experience_id, n.status)",
            "CREATE INDEX n4x_experience_surface_type IF NOT EXISTS FOR (n:ExperienceSurface) ON (n.surface_id, n.surface_type, n.surface_type_version)",
            "CREATE INDEX n4x_dependency_spec IF NOT EXISTS FOR (n:RuntimeDependency) ON (n.ecosystem, n.package, n.spec)",
            "CREATE INDEX n4x_javascript_environment_owner IF NOT EXISTS FOR (n:JavaScriptEnvironment) ON (n.owner_kind, n.owner_id)",
            "CREATE INDEX n4x_build_invocation_owner IF NOT EXISTS FOR (n:BuildInvocation) ON (n.owner_kind, n.owner_id, n.kind)",
            "CREATE INDEX n4x_build_artifact_owner IF NOT EXISTS FOR (n:BuildArtifact) ON (n.owner_kind, n.owner_id, n.artifact_type)",
            "CREATE INDEX n4x_trigger_revision_action IF NOT EXISTS FOR (n:TriggerRevision) ON (n.trigger_id, n.action_id)",
            "CREATE INDEX n4x_job_record_trigger IF NOT EXISTS FOR (n:JobRecord) ON (n.trigger_revision_id, n.status)",
            "CREATE INDEX n4x_job_attempt_job IF NOT EXISTS FOR (n:JobAttempt) ON (n.job_id, n.attempt)",
            "CREATE INDEX n4x_cypher_audit_invocation IF NOT EXISTS FOR (n:CypherAuditRecord) ON (n.invocation_id, n.created_at)",
        ]

    def bootstrap_schema(self) -> None:
        with self.driver.session(database=self.config.database) as session:
            for statement in self.schema_statements():
                session.run(statement).consume()
            session.run(
                "MERGE (root:N4XRoot {id: 'n4x'}) "
                "SET root.created_at = coalesce(root.created_at, datetime())"
            ).consume()

    def reset_dev_graph(self) -> None:
        with self.driver.session(database=self.config.database) as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
            while True:
                constraint_names = [
                    record["name"]
                    for record in session.run(
                        "SHOW CONSTRAINTS YIELD name "
                        "WHERE name STARTS WITH 'n4x_' RETURN name"
                    )
                ]
                if not constraint_names:
                    break
                for name in constraint_names:
                    session.run(
                        f"DROP CONSTRAINT {_quote_schema_name(name)} IF EXISTS"
                    ).consume()
            index_names = [
                record["name"]
                for record in session.run(
                    "SHOW INDEXES YIELD name, owningConstraint "
                    "WHERE name STARTS WITH 'n4x_' "
                    "AND owningConstraint IS NULL "
                    "RETURN name"
                )
            ]
            for name in index_names:
                session.run(
                    f"DROP INDEX {_quote_schema_name(name)} IF EXISTS"
                ).consume()
        self.bootstrap_schema()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._transaction_context.get() is not None:
            yield
            return
        with self.driver.session(database=self.config.database) as session:
            tx = session.begin_transaction()
            token = self._transaction_context.set((session, tx))
            try:
                yield
                tx.commit()
            except Exception:
                tx.rollback()
                raise
            finally:
                self._transaction_context.reset(token)

    def upsert_model(
        self, label: str, identity: dict[str, Any], model: BaseModel | dict[str, Any]
    ) -> None:
        props = _encode_props(
            model.model_dump(mode="json") if isinstance(model, BaseModel) else model
        )
        self._consume(
            f"MERGE (n:{label} {{{_cypher_identity(identity)}}}) SET n += $props",
            **identity,
            props=props,
        )

    def acquire_write_lock(
        self, label: str, identity: dict[str, Any]
    ) -> None:
        label = _safe_label(label)
        self._consume(
            (
                f"MATCH (n:{label} {{{_cypher_identity(identity)}}}) "
                "SET n.__n4x_write_lock = "
                "coalesce(n.__n4x_write_lock, 0) + 1 "
                "REMOVE n.__n4x_write_lock"
            ),
            **identity,
        )

    def create_model(
        self, label: str, identity: dict[str, Any], model: BaseModel | dict[str, Any]
    ) -> None:
        label = _safe_label(label)
        props = _encode_props(
            model.model_dump(mode="json") if isinstance(model, BaseModel) else model
        )
        self._consume(
            f"CREATE (n:{label} {{{_cypher_identity(identity)}}}) SET n += $props",
            **identity,
            props=props,
        )

    def delete_model(self, label: str, identity: dict[str, Any]) -> None:
        self._consume(
            f"MATCH (n:{label} {{{_cypher_identity(identity)}}}) DETACH DELETE n",
            **identity,
        )

    def create_edge(
        self,
        from_label: str,
        from_identity: dict[str, Any],
        edge_type: str,
        to_label: str,
        to_identity: dict[str, Any],
        props: dict[str, Any] | None = None,
    ) -> None:
        from_label = _safe_label(from_label)
        to_label = _safe_label(to_label)
        edge_type = _safe_relationship_type(edge_type)
        props = _encode_props(props or {})
        path = props.get("path")
        merge = (
            f"MERGE (from)-[r:{edge_type} {{path: $path}}]->(to)"
            if edge_type == "HAS_FILE" and path is not None
            else f"MERGE (from)-[r:{edge_type}]->(to)"
        )
        self._consume(
            f"""
            MATCH (from:{from_label} {{{_cypher_identity_prefixed("from", from_identity)}}})
            MATCH (to:{to_label} {{{_cypher_identity_prefixed("to", to_identity)}}})
            {merge}
            SET r += $props
            """,
            **{f"from_{key}": value for key, value in from_identity.items()},
            **{f"to_{key}": value for key, value in to_identity.items()},
            props=props,
            path=path,
        )

    def delete_edge(
        self,
        from_label: str,
        from_identity: dict[str, Any],
        edge_type: str,
        to_label: str | None = None,
        to_identity: dict[str, Any] | None = None,
        props: dict[str, Any] | None = None,
    ) -> None:
        from_label = _safe_label(from_label)
        edge_type = _safe_relationship_type(edge_type)
        to_match = ""
        params = {f"from_{key}": value for key, value in from_identity.items()}
        if to_label is not None and to_identity is not None:
            to_label = _safe_label(to_label)
            to_match = (
                f"->(to:{to_label} {{{_cypher_identity_prefixed('to', to_identity)}}})"
            )
            params.update({f"to_{key}": value for key, value in to_identity.items()})
        else:
            to_match = "->()"
        where = ""
        if props:
            clauses = []
            for key, value in props.items():
                param = f"prop_{key}"
                clauses.append(f"r.{key} = ${param}")
                params[param] = value
            where = " WHERE " + " AND ".join(clauses)
        self._consume(
            f"""
            MATCH (from:{from_label} {{{_cypher_identity_prefixed("from", from_identity)}}})-[r:{edge_type}]{to_match}
            {where}
            DELETE r
            """,
            **params,
        )

    def replace_single_edge(
        self,
        from_label: str,
        from_identity: dict[str, Any],
        edge_type: str,
        to_label: str,
        to_identity: dict[str, Any],
        props: dict[str, Any] | None = None,
        *,
        expected_to_label: str | None = None,
        expected_to_identity: dict[str, Any] | None = None,
        require_current_match: bool = False,
    ) -> None:
        if require_current_match:
            from_label = _safe_label(from_label)
            to_label = _safe_label(to_label)
            edge_type = _safe_relationship_type(edge_type)
            params = {
                **{
                    f"from_{key}": value
                    for key, value in from_identity.items()
                },
                **{f"to_{key}": value for key, value in to_identity.items()},
                "props": _encode_props(props or {}),
            }
            if expected_to_label is None or expected_to_identity is None:
                expected_match = "size(current_relationships) = 0"
            else:
                expected_to_label = _safe_label(expected_to_label)
                predicates = [f"target:{expected_to_label}"]
                for index, (key, value) in enumerate(
                    expected_to_identity.items()
                ):
                    params[f"expected_key_{index}"] = key
                    params[f"expected_value_{index}"] = value
                    predicates.append(
                        f"target[$expected_key_{index}] = $expected_value_{index}"
                    )
                expected_match = (
                    "size(current_relationships) = 1 AND "
                    "size([target IN current_targets WHERE "
                    + " AND ".join(predicates)
                    + "]) = 1"
                )
            try:
                rows = self._records(
                    f"""
                    MATCH (from:{from_label}
                           {{{_cypher_identity_prefixed("from", from_identity)}}})
                    MATCH (to:{to_label}
                           {{{_cypher_identity_prefixed("to", to_identity)}}})
                    SET from.__n4x_uow_lock =
                        coalesce(from.__n4x_uow_lock, 0) + 1
                    WITH from, to
                    OPTIONAL MATCH (from)-[current:{edge_type}]->(current_to)
                    WITH from, to,
                         collect(current) AS current_relationships,
                         collect(current_to) AS current_targets
                    WITH from, to, current_relationships,
                         ({expected_match}) AS matches
                    FOREACH (
                        relationship IN
                        CASE
                            WHEN matches THEN current_relationships
                            ELSE []
                        END |
                        DELETE relationship
                    )
                    FOREACH (
                        ignored IN CASE WHEN matches THEN [1] ELSE [] END |
                        CREATE (from)-[replacement:{edge_type}]->(to)
                        SET replacement += $props
                    )
                    REMOVE from.__n4x_uow_lock
                    RETURN matches
                    """,
                    **params,
                )
            except TransientError as error:
                if (
                    error.code
                    == "Neo.TransientError.Transaction.DeadlockDetected"
                ):
                    raise ConcurrentGraphUpdateError(
                        f"concurrent {edge_type} replacement for "
                        f"{from_label} {from_identity}"
                    ) from error
                raise
            if not rows or not rows[0]["matches"]:
                raise ConcurrentGraphUpdateError(
                    f"stale {edge_type} replacement for {from_label} {from_identity}"
                )
            return
        self.delete_edge(from_label, from_identity, edge_type)
        self.create_edge(
            from_label, from_identity, edge_type, to_label, to_identity, props
        )

    def clear_single_edge(
        self,
        from_label: str,
        from_identity: dict[str, Any],
        edge_type: str,
        *,
        expected_to_label: str | None,
        expected_to_identity: dict[str, Any] | None,
    ) -> None:
        from_label = _safe_label(from_label)
        edge_type = _safe_relationship_type(edge_type)
        params = {
            f"from_{key}": value for key, value in from_identity.items()
        }
        if expected_to_label is None or expected_to_identity is None:
            expected_match = "size(current_relationships) = 0"
        else:
            expected_to_label = _safe_label(expected_to_label)
            predicates = [f"target:{expected_to_label}"]
            for index, (key, value) in enumerate(expected_to_identity.items()):
                params[f"expected_key_{index}"] = key
                params[f"expected_value_{index}"] = value
                predicates.append(
                    f"target[$expected_key_{index}] = $expected_value_{index}"
                )
            expected_match = (
                "size(current_relationships) = 1 AND "
                "size([target IN current_targets WHERE "
                + " AND ".join(predicates)
                + "]) = 1"
            )
        try:
            rows = self._records(
                f"""
                MATCH (from:{from_label}
                       {{{_cypher_identity_prefixed("from", from_identity)}}})
                SET from.__n4x_uow_lock =
                    coalesce(from.__n4x_uow_lock, 0) + 1
                WITH from
                OPTIONAL MATCH (from)-[current:{edge_type}]->(current_to)
                WITH from, collect(current) AS current_relationships,
                     collect(current_to) AS current_targets
                WITH from, current_relationships,
                     ({expected_match}) AS matches
                FOREACH (
                    relationship IN
                    CASE WHEN matches THEN current_relationships ELSE [] END |
                    DELETE relationship
                )
                REMOVE from.__n4x_uow_lock
                RETURN matches
                """,
                **params,
            )
        except TransientError as error:
            if error.code == "Neo.TransientError.Transaction.DeadlockDetected":
                raise ConcurrentGraphUpdateError(
                    f"concurrent {edge_type} clear for "
                    f"{from_label} {from_identity}"
                ) from error
            raise
        if not rows or not rows[0]["matches"]:
            raise ConcurrentGraphUpdateError(
                f"stale {edge_type} clear for {from_label} {from_identity}"
            )

    def create_app_relation(self, relation: BaseModel) -> None:
        physical_type = _safe_relationship_type(getattr(relation, "physical_type"))
        props = _encode_props(relation.model_dump(mode="json"))
        self._consume(
            f"""
            MATCH (from:ApplicationObject {{
                application_id: $application_id,
                data_space_id: $data_space_id,
                id: $from_object_id
            }})
            MATCH (to:ApplicationObject {{
                application_id: $application_id,
                data_space_id: $data_space_id,
                id: $to_object_id
            }})
            MERGE (from)-[r:{physical_type} {{
                application_id: $application_id,
                data_space_id: $data_space_id,
                id: $id
            }}]->(to)
            SET r += $props
            """,
            id=getattr(relation, "id"),
            application_id=getattr(relation, "application_id"),
            data_space_id=getattr(relation, "data_space_id"),
            from_object_id=getattr(relation, "from_object_id"),
            to_object_id=getattr(relation, "to_object_id"),
            props=props,
        )

    def delete_app_relation(self, relation: BaseModel) -> None:
        physical_type = _safe_relationship_type(getattr(relation, "physical_type"))
        self._consume(
            (
                f"MATCH ()-[r:{physical_type} {{"
                "application_id: $application_id, "
                "data_space_id: $data_space_id, id: $id}]->() DELETE r"
            ),
            application_id=getattr(relation, "application_id"),
            data_space_id=getattr(relation, "data_space_id"),
            id=getattr(relation, "id"),
        )

    def fetch_app_relation(
        self,
        application_id: str,
        data_space_id: str,
        relation_id: str,
    ) -> dict[str, Any] | None:
        rows = self._records(
            """
            MATCH (:ApplicationObject)-[r {
                application_id: $application_id,
                data_space_id: $data_space_id,
                id: $id
            }]->(:ApplicationObject)
            RETURN properties(r) AS props, type(r) AS physical_type
            LIMIT 1
            """,
            application_id=application_id,
            data_space_id=data_space_id,
            id=relation_id,
        )
        if not rows:
            return None
        props = dict(rows[0]["props"])
        props.setdefault("physical_type", rows[0]["physical_type"])
        return props

    def fetch_all_app_relations(self) -> list[dict[str, Any]]:
        rows = self._records(
            """
            MATCH (:ApplicationObject)-[r]->(:ApplicationObject)
            WHERE r.application_id IS NOT NULL
              AND r.relation_type_id IS NOT NULL
            RETURN properties(r) AS props, type(r) AS physical_type
            """
        )
        result = []
        for row in rows:
            props = dict(row["props"])
            props.setdefault("physical_type", row["physical_type"])
            result.append(props)
        return result

    def fetch_app_relations(
        self,
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
        *,
        data_space_id: str = "production",
    ) -> list[dict[str, Any]]:
        where = [
            "r.application_id = $application_id",
            "r.data_space_id = $data_space_id",
        ]
        params: dict[str, Any] = {
            "application_id": application_id,
            "data_space_id": data_space_id,
        }
        if relation_type_id is not None:
            where.append("r.relation_type_id = $relation_type_id")
            params["relation_type_id"] = relation_type_id
        if from_object_id is not None:
            where.append("from.id = $from_object_id")
            params["from_object_id"] = from_object_id
        if to_object_id is not None:
            where.append("to.id = $to_object_id")
            params["to_object_id"] = to_object_id
        result = self._records(
            "MATCH (from:ApplicationObject)-[r]->(to:ApplicationObject) "
            f"WHERE {' AND '.join(where)} "
            "RETURN properties(r) AS props, type(r) AS physical_type",
            **params,
        )
        rows = []
        for record in result:
            props = dict(record["props"])
            props.setdefault("physical_type", record["physical_type"])
            rows.append(props)
        return rows

    def run_cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return [record.data() for record in self._records(query, **(params or {}))]

    def fetch_node(
        self, label: str, identity: dict[str, Any]
    ) -> dict[str, Any] | None:
        rows = self.fetch_nodes(label, identity)
        return rows[0] if rows else None

    def fetch_nodes(
        self, label: str, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        label = _safe_label(label)
        filters = filters or {}
        where = ""
        if filters:
            where = f" {{{_cypher_identity(filters)}}}"
        return [
            dict(record["props"])
            for record in self._records(
                f"MATCH (n:{label}{where}) RETURN properties(n) AS props",
                **filters,
            )
        ]

    def fetch_edges(
        self,
        from_ref: Any | None = None,
        edge_type: str | None = None,
        to_ref: Any | None = None,
    ) -> list[dict[str, Any]]:
        from_match = "(from)"
        to_match = "(to)"
        params: dict[str, Any] = {}
        if from_ref is not None:
            from_label = _safe_label(from_ref.label)
            from_match = (
                f"(from:{from_label} "
                f"{{{_cypher_identity_prefixed('from', from_ref.identity)}}})"
            )
            params.update(
                {f"from_{key}": value for key, value in from_ref.identity.items()}
            )
        if to_ref is not None:
            to_label = _safe_label(to_ref.label)
            to_match = (
                f"(to:{to_label} "
                f"{{{_cypher_identity_prefixed('to', to_ref.identity)}}})"
            )
            params.update(
                {f"to_{key}": value for key, value in to_ref.identity.items()}
            )
        relation_match = "[r]"
        if edge_type is not None:
            relation_match = f"[r:{_safe_relationship_type(edge_type)}]"
        records = self._records(
            f"""
            MATCH {from_match}-{relation_match}->{to_match}
            RETURN labels(from) AS from_labels, properties(from) AS from_props,
                   type(r) AS type, properties(r) AS props,
                   labels(to) AS to_labels, properties(to) AS to_props
            """,
            **params,
        )
        return [
            {
                "from_label": (
                    from_ref.label
                    if from_ref is not None
                    else _primary_label(record["from_labels"])
                ),
                "from_identity": (
                    dict(from_ref.identity)
                    if from_ref is not None
                    else _node_identity(
                        _primary_label(record["from_labels"]),
                        record["from_props"],
                    )
                ),
                "type": record["type"],
                "to_label": (
                    to_ref.label
                    if to_ref is not None
                    else _primary_label(record["to_labels"])
                ),
                "to_identity": (
                    dict(to_ref.identity)
                    if to_ref is not None
                    else _node_identity(
                        _primary_label(record["to_labels"]),
                        record["to_props"],
                    )
                ),
                "props": dict(record["props"]),
            }
            for record in records
        ]

    def fetch_edges_for_source(
        self,
        from_label: str,
        from_identity: dict[str, Any],
        edge_type: str,
    ) -> list[dict[str, Any]]:
        records = self._records(
            f"""
            MATCH (from:{_safe_label(from_label)}
                   {{{_cypher_identity_prefixed("from", from_identity)}}})
                  -[r:{_safe_relationship_type(edge_type)}]->(to)
            RETURN labels(to) AS to_labels, properties(to) AS to_props
            """,
            **{f"from_{key}": value for key, value in from_identity.items()},
        )
        return [
            {
                "to_label": _primary_label(record["to_labels"]),
                "to_props": dict(record["to_props"]),
            }
            for record in records
        ]

    def _consume(self, query: str, **params: Any) -> None:
        transaction_context = self._transaction_context.get()
        try:
            if transaction_context is not None:
                _, tx = transaction_context
                tx.run(query, **params).consume()
                return
            with self.driver.session(database=self.config.database) as session:
                session.run(query, **params).consume()
        except TransientError as error:
            _raise_if_deadlock(error)
            raise

    def _records(self, query: str, **params: Any) -> list[Any]:
        transaction_context = self._transaction_context.get()
        try:
            if transaction_context is not None:
                _, tx = transaction_context
                return list(tx.run(query, **params))
            with self.driver.session(database=self.config.database) as session:
                return list(session.run(query, **params))
        except TransientError as error:
            _raise_if_deadlock(error)
            raise


def _cypher_identity(identity: dict[str, Any]) -> str:
    return ", ".join(f"{key}: ${key}" for key in identity)


def _cypher_identity_prefixed(prefix: str, identity: dict[str, Any]) -> str:
    return ", ".join(f"{key}: ${prefix}_{key}" for key in identity)


def _quote_schema_name(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def _safe_label(label: str) -> str:
    if not label.replace("_", "").isalnum():
        raise ValueError(f"invalid Neo4j label: {label}")
    return label


def _safe_relationship_type(rel_type: str) -> str:
    if not rel_type.replace("_", "").isalnum():
        raise ValueError(f"invalid Neo4j relationship type: {rel_type}")
    return rel_type


def _raise_if_deadlock(error: TransientError) -> None:
    if error.code == "Neo.TransientError.Transaction.DeadlockDetected":
        raise TransientGraphConflictError(
            "graph write deadlock; retry the unit of work"
        ) from error


def _encode_props(values: dict[str, Any]) -> dict[str, Any]:
    encoded: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, dict):
            encoded[key] = json.dumps(value, sort_keys=True)
        elif isinstance(value, list):
            if all(not isinstance(item, dict | list) for item in value):
                encoded[key] = value
            else:
                encoded[key] = json.dumps(value, sort_keys=True)
        else:
            encoded[key] = value
    return encoded


def _primary_label(labels: list[str]) -> str:
    return labels[0] if labels else ""


def _node_identity(label: str, props: dict[str, Any]) -> dict[str, Any]:
    if label == "ApplicationObject":
        return {
            "application_id": props["application_id"],
            "data_space_id": props["data_space_id"],
            "id": props["id"],
        }
    if label == "DataSpace":
        return {
            "application_id": props["application_id"],
            "id": props["id"],
        }
    if "id" in props:
        return {"id": props["id"]}
    return dict(props)
