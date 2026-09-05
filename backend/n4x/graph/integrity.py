from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from n4x.contracts.graph_metamodel import GRAPH_METAMODEL_VERSION
from n4x.graph.store import (
    EdgeRecord,
    GraphShapeReport,
    GraphStore,
    NodeRef,
    node_ref,
    relation_physical_type,
)
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import GraphMetamodelVersionError


@dataclass(frozen=True)
class NodeDeclaration:
    collection: str
    label: str
    identity_fields: tuple[str, ...] = ("id",)


@dataclass(frozen=True)
class OwnershipDeclaration:
    child_collection: str
    child_label: str
    edge_type: str
    owner_label: str
    owner_field: str | None = None
    owner_constant: str | None = None
    child_identity_fields: tuple[str, ...] = ("id",)
    owner_identity_fields: tuple[tuple[str, str], ...] = ()
    optional: bool = False
    owner_kind_field: str | None = None


@dataclass(frozen=True)
class RevisionDeclaration:
    stable_collection: str
    stable_label: str
    revision_collection: str
    revision_label: str
    revision_owner_field: str


NODE_DECLARATIONS = (
    NodeDeclaration("systems", "System"),
    NodeDeclaration("system_revisions", "SystemRevision"),
    NodeDeclaration("applications", "Application"),
    NodeDeclaration(
        "data_spaces",
        "DataSpace",
        ("application_id", "id"),
    ),
    NodeDeclaration("development_deployments", "DevelopmentDeployment"),
    NodeDeclaration("revisions", "ApplicationRevision"),
    NodeDeclaration("experiences", "Experience"),
    NodeDeclaration("experience_revisions", "ExperienceRevision"),
    NodeDeclaration("experience_surfaces", "ExperienceSurface"),
    NodeDeclaration("experience_validation_reports", "ExperienceValidationReport"),
    NodeDeclaration("authoring_guides", "AuthoringGuide"),
    NodeDeclaration("authoring_guide_revisions", "AuthoringGuideRevision"),
    NodeDeclaration("ui_themes", "UiTheme"),
    NodeDeclaration("ui_theme_revisions", "UiThemeRevision"),
    NodeDeclaration("app_blueprints", "AppBlueprint"),
    NodeDeclaration("blueprint_revisions", "BlueprintRevision"),
    NodeDeclaration("source_trees", "SourceTree"),
    NodeDeclaration("source_contents", "SourceContent"),
    NodeDeclaration(
        "objects",
        "ApplicationObject",
        ("application_id", "data_space_id", "id"),
    ),
    NodeDeclaration("object_types", "ObjectType"),
    NodeDeclaration("object_type_revisions", "ObjectTypeRevision"),
    NodeDeclaration("relation_types", "RelationType"),
    NodeDeclaration("relation_type_revisions", "RelationTypeRevision"),
    NodeDeclaration("secret_references", "SecretReference"),
    NodeDeclaration("credential_records", "CredentialRecord"),
    NodeDeclaration("callback_routes", "CallbackRoute"),
    NodeDeclaration("runtime_dependencies", "RuntimeDependency"),
    NodeDeclaration("python_environments", "PythonEnvironment"),
    NodeDeclaration("javascript_environments", "JavaScriptEnvironment"),
    NodeDeclaration("build_invocations", "BuildInvocation"),
    NodeDeclaration("build_artifacts", "BuildArtifact"),
    NodeDeclaration("actions", "Action"),
    NodeDeclaration("action_revisions", "ActionRevision"),
    NodeDeclaration("triggers", "Trigger"),
    NodeDeclaration("trigger_revisions", "TriggerRevision"),
    NodeDeclaration("job_records", "JobRecord"),
    NodeDeclaration("job_attempts", "JobAttempt"),
    NodeDeclaration("cypher_audits", "CypherAuditRecord"),
    NodeDeclaration("invocations", "Invocation"),
    NodeDeclaration("test_cases", "TestCase"),
    NodeDeclaration("checkpoints", "GraphCheckpoint"),
    NodeDeclaration("checkpoint_snapshots", "CheckpointSnapshot"),
    NodeDeclaration("checkpoint_blobs", "CheckpointBlob"),
    NodeDeclaration("validation_reports", "ValidationReport"),
    NodeDeclaration("package_import_attempts", "PackageImportAttempt"),
)


OWNERSHIP_DECLARATIONS = (
    OwnershipDeclaration(
        "systems",
        "System",
        "HAS_SYSTEM",
        "N4XRoot",
        owner_constant="n4x",
    ),
    OwnershipDeclaration(
        "applications",
        "Application",
        "HAS_APPLICATION",
        "N4XRoot",
        owner_constant="n4x",
    ),
    OwnershipDeclaration(
        "data_spaces",
        "DataSpace",
        "HAS_DATA_SPACE",
        "Application",
        owner_field="application_id",
        child_identity_fields=("application_id", "id"),
    ),
    OwnershipDeclaration(
        "development_deployments",
        "DevelopmentDeployment",
        "HAS_DEVELOPMENT_DEPLOYMENT",
        "N4XRoot",
        owner_constant="n4x",
    ),
    OwnershipDeclaration(
        "experiences",
        "Experience",
        "HAS_EXPERIENCE",
        "N4XRoot",
        owner_constant="n4x",
    ),
    OwnershipDeclaration(
        "authoring_guides",
        "AuthoringGuide",
        "HAS_AUTHORING_GUIDE",
        "N4XRoot",
        owner_constant="n4x",
    ),
    OwnershipDeclaration(
        "ui_themes",
        "UiTheme",
        "HAS_UI_THEME",
        "N4XRoot",
        owner_constant="n4x",
    ),
    OwnershipDeclaration(
        "app_blueprints",
        "AppBlueprint",
        "HAS_BLUEPRINT",
        "N4XRoot",
        owner_constant="n4x",
    ),
    OwnershipDeclaration(
        "object_types",
        "ObjectType",
        "DEFINES_OBJECT_TYPE",
        "Application",
        owner_field="application_id",
    ),
    OwnershipDeclaration(
        "relation_types",
        "RelationType",
        "DEFINES_RELATION_TYPE",
        "Application",
        owner_field="application_id",
    ),
    OwnershipDeclaration(
        "actions",
        "Action",
        "DEFINES_ACTION",
        "Application",
        owner_field="application_id",
    ),
    OwnershipDeclaration(
        "triggers",
        "Trigger",
        "DEFINES_TRIGGER",
        "Application",
        owner_field="application_id",
    ),
    OwnershipDeclaration(
        "objects",
        "ApplicationObject",
        "OWNS_OBJECT",
        "DataSpace",
        child_identity_fields=("application_id", "data_space_id", "id"),
        owner_identity_fields=(
            ("application_id", "application_id"),
            ("id", "data_space_id"),
        ),
    ),
    OwnershipDeclaration(
        "secret_references",
        "SecretReference",
        "HAS_SECRET_REFERENCE",
        "Application",
        owner_field="application_id",
    ),
    OwnershipDeclaration(
        "credential_records",
        "CredentialRecord",
        "HAS_CREDENTIAL",
        "Application",
        owner_field="application_id",
    ),
    OwnershipDeclaration(
        "callback_routes",
        "CallbackRoute",
        "HAS_CALLBACK_ROUTE",
        "Application",
        owner_field="application_id",
    ),
    OwnershipDeclaration(
        "validation_reports",
        "ValidationReport",
        "HAS_VALIDATION_REPORT",
        "ApplicationRevision",
        owner_field="application_revision_id",
    ),
    OwnershipDeclaration(
        "experience_validation_reports",
        "ExperienceValidationReport",
        "HAS_VALIDATION_REPORT",
        "ExperienceRevision",
        owner_field="experience_revision_id",
    ),
    OwnershipDeclaration(
        "checkpoints",
        "GraphCheckpoint",
        "HAS_CHECKPOINT",
        "Application",
        owner_field="application_id",
    ),
    OwnershipDeclaration(
        "checkpoint_snapshots",
        "CheckpointSnapshot",
        "HAS_SNAPSHOT",
        "GraphCheckpoint",
        owner_field="checkpoint_id",
    ),
    OwnershipDeclaration(
        "checkpoint_blobs",
        "CheckpointBlob",
        "HAS_BLOB",
        "CheckpointSnapshot",
        owner_field="snapshot_id",
    ),
    OwnershipDeclaration(
        "package_import_attempts",
        "PackageImportAttempt",
        "HAS_PACKAGE_IMPORT_ATTEMPT",
        "N4XRoot",
        owner_constant="n4x",
    ),
    OwnershipDeclaration(
        "invocations",
        "Invocation",
        "HAS_INVOCATION",
        "ActionRevision",
        owner_field="action_revision_id",
    ),
    OwnershipDeclaration(
        "job_records",
        "JobRecord",
        "HAS_JOB",
        "Application",
        owner_field="application_id",
    ),
    OwnershipDeclaration(
        "job_records",
        "JobRecord",
        "HAS_JOB",
        "TriggerRevision",
        owner_field="trigger_revision_id",
    ),
    OwnershipDeclaration(
        "job_attempts",
        "JobAttempt",
        "HAS_ATTEMPT",
        "JobRecord",
        owner_field="job_id",
    ),
    OwnershipDeclaration(
        "cypher_audits",
        "CypherAuditRecord",
        "HAS_CYPHER_AUDIT",
        "Application",
        owner_field="application_id",
    ),
    OwnershipDeclaration(
        "python_environments",
        "PythonEnvironment",
        "USES_PYTHON_ENV",
        "ApplicationRevision",
        owner_field="application_revision_id",
    ),
    OwnershipDeclaration(
        "javascript_environments",
        "JavaScriptEnvironment",
        "USES_JAVASCRIPT_ENV",
        "",
        owner_field="owner_id",
        owner_kind_field="owner_kind",
    ),
    OwnershipDeclaration(
        "build_invocations",
        "BuildInvocation",
        "HAS_BUILD_INVOCATION",
        "",
        owner_field="owner_id",
        owner_kind_field="owner_kind",
    ),
    OwnershipDeclaration(
        "build_artifacts",
        "BuildArtifact",
        "HAS_BUILD_ARTIFACT",
        "",
        owner_field="owner_id",
        owner_kind_field="owner_kind",
    ),
    OwnershipDeclaration(
        "build_artifacts",
        "BuildArtifact",
        "PRODUCED",
        "BuildInvocation",
        owner_field="build_invocation_id",
    ),
)


REVISION_DECLARATIONS = (
    RevisionDeclaration(
        "systems",
        "System",
        "system_revisions",
        "SystemRevision",
        "system_id",
    ),
    RevisionDeclaration(
        "applications",
        "Application",
        "revisions",
        "ApplicationRevision",
        "application_id",
    ),
    RevisionDeclaration(
        "experiences",
        "Experience",
        "experience_revisions",
        "ExperienceRevision",
        "experience_id",
    ),
    RevisionDeclaration(
        "authoring_guides",
        "AuthoringGuide",
        "authoring_guide_revisions",
        "AuthoringGuideRevision",
        "guide_id",
    ),
    RevisionDeclaration(
        "ui_themes",
        "UiTheme",
        "ui_theme_revisions",
        "UiThemeRevision",
        "theme_id",
    ),
    RevisionDeclaration(
        "app_blueprints",
        "AppBlueprint",
        "blueprint_revisions",
        "BlueprintRevision",
        "blueprint_id",
    ),
    RevisionDeclaration(
        "object_types",
        "ObjectType",
        "object_type_revisions",
        "ObjectTypeRevision",
        "object_type_id",
    ),
    RevisionDeclaration(
        "relation_types",
        "RelationType",
        "relation_type_revisions",
        "RelationTypeRevision",
        "relation_type_id",
    ),
    RevisionDeclaration(
        "actions",
        "Action",
        "action_revisions",
        "ActionRevision",
        "action_id",
    ),
    RevisionDeclaration(
        "triggers",
        "Trigger",
        "trigger_revisions",
        "TriggerRevision",
        "trigger_id",
    ),
)


SINGLETON_EDGE_TYPES = {
    "HAS_SOURCE_TREE",
    "FROM_TYPE",
    "TO_TYPE",
    "INVOKES",
    "TARGETS",
    "TESTS",
    "RAN",
    "INSTANCE_OF",
    "CONFORMS_TO",
    "CAPTURES_REVISION",
    "HAS_SNAPSHOT",
    "HAS_BLOB",
}


@dataclass
class IntegrityPlan:
    nodes: list[NodeRef]
    edges: list[tuple[NodeRef, str, NodeRef]]
    active_edges: list[tuple[NodeRef, str, NodeRef | None]]


class GraphIntegrityService:
    """Validates and repairs graph shape from durable repository records."""

    def __init__(self, store: GraphStore, uow: GraphUnitOfWork) -> None:
        self.store = store
        self.uow = uow
        self.records = uow.records

    def bootstrap_schema(self) -> None:
        """Apply store constraints and stamp a fresh graph root.

        Does not scan the graph. Call ``validate_graph_shape`` to inspect
        ownership and revision edges; that report is not a boot gate.
        """
        self.store.bootstrap_schema()
        self.initialize_graph_root()

    def reset_dev_graph(self) -> GraphShapeReport:
        self.store.reset_dev_graph()
        self.store.bootstrap_schema()
        self.initialize_graph_root()
        return self.validate_graph_shape()

    def initialize_graph_root(self) -> bool:
        root = self.store.get_node("N4XRoot", {"id": "n4x"})
        version = None if root is None else root.get("graph_metamodel_version")
        if version == GRAPH_METAMODEL_VERSION:
            return False

        expected_fresh_node_count = 0 if root is None else 1
        if version is None and self.store.node_count() == expected_fresh_node_count:
            with self.uow:
                values = dict(root or {"id": "n4x"})
                values["graph_metamodel_version"] = GRAPH_METAMODEL_VERSION
                self.store.upsert_node("N4XRoot", {"id": "n4x"}, values)
            return True

        found = "unversioned" if version is None else repr(version)
        raise GraphMetamodelVersionError(
            "graph reset required: expected graph metamodel "
            f"{GRAPH_METAMODEL_VERSION!r}, found {found}; "
            "run reset_dev_graph against a dedicated development database"
        )

    def validate_graph_shape(self) -> GraphShapeReport:
        with self.uow:
            plan = self._build_plan()
            report = self.store.validate_graph_shape()
            errors = list(report.errors)
            warnings = list(report.warnings)
            actual_edges = self.store.list_edges()
            actual_nodes = {_ref_key(ref) for ref in plan.nodes}

            for from_ref, edge_type, to_ref in plan.edges:
                if _ref_key(from_ref) not in actual_nodes:
                    errors.append(f"missing edge source node: {_format_ref(from_ref)}")
                    continue
                if _ref_key(to_ref) not in actual_nodes:
                    errors.append(f"missing edge target node: {_format_ref(to_ref)}")
                    continue
                matching = _matching_edges(actual_edges, from_ref, edge_type, to_ref)
                if not matching:
                    errors.append(
                        f"missing edge: {_format_ref(from_ref)}"
                        f"-[:{edge_type}]->{_format_ref(to_ref)}"
                    )
                if edge_type in SINGLETON_EDGE_TYPES:
                    outgoing = _matching_edges(actual_edges, from_ref, edge_type, None)
                    if len(outgoing) != 1:
                        errors.append(
                            f"expected exactly one {edge_type} edge from "
                            f"{_format_ref(from_ref)}, got {len(outgoing)}"
                        )

            for from_ref, edge_type, expected_to in plan.active_edges:
                outgoing = _matching_edges(actual_edges, from_ref, edge_type, None)
                expected_count = 0 if expected_to is None else 1
                if len(outgoing) != expected_count:
                    errors.append(
                        f"expected {expected_count} {edge_type} edge from "
                        f"{_format_ref(from_ref)}, got {len(outgoing)}"
                    )
                elif expected_to is not None and outgoing[0].to_ref != expected_to:
                    errors.append(
                        f"{edge_type} points at wrong target: "
                        f"{_format_ref(from_ref)}->{_format_ref(outgoing[0].to_ref)}"
                    )

            unreachable = self._validate_reachability(plan.nodes, actual_edges)
            errors.extend(unreachable["errors"])
            warnings.extend(unreachable["warnings"])
            errors.extend(self._validate_app_relations(actual_edges))
            errors.extend(self._validate_experience_references())
            errors.extend(self._validate_revision_source_ownership(actual_edges))
            errors.extend(self._validate_surfaces(actual_edges))
            return GraphShapeReport(errors=_dedupe(errors), warnings=_dedupe(warnings))

    def repair_graph_edges(self) -> GraphShapeReport:
        self.store.bootstrap_schema()
        with self.uow:
            plan = self._build_plan()
            node_keys = {_ref_key(ref) for ref in plan.nodes}
            for edge in self.store.list_edges():
                if edge.type in {"N4X_KERNEL", "N4X_RELATION"}:
                    self.store.delete_edge(edge.from_ref, edge.type, edge.to_ref)
            for from_ref, edge_type, to_ref in plan.edges:
                if (
                    _ref_key(from_ref) not in node_keys
                    or _ref_key(to_ref) not in node_keys
                ):
                    continue
                if edge_type in SINGLETON_EDGE_TYPES:
                    self.store.delete_edge(from_ref, edge_type)
                self.store.create_edge(from_ref, edge_type, to_ref)
            for from_ref, edge_type, to_ref in plan.active_edges:
                self.store.delete_edge(from_ref, edge_type)
                if to_ref is not None and _ref_key(to_ref) in node_keys:
                    self.store.create_edge(from_ref, edge_type, to_ref)
        return self.validate_graph_shape()

    def _build_plan(self) -> IntegrityPlan:
        nodes = [node_ref("N4XRoot", id="n4x")]
        for declaration in NODE_DECLARATIONS:
            collection = getattr(self.records, declaration.collection)
            nodes.extend(
                NodeRef(
                    declaration.label,
                    {
                        field: getattr(record, field)
                        for field in declaration.identity_fields
                    },
                )
                for record in collection.values()
            )

        edges: list[tuple[NodeRef, str, NodeRef]] = []
        for declaration in OWNERSHIP_DECLARATIONS:
            collection = getattr(self.records, declaration.child_collection)
            for record in collection.values():
                owner_id = (
                    declaration.owner_constant
                    if declaration.owner_constant is not None
                    else getattr(record, declaration.owner_field or "")
                    if not declaration.owner_identity_fields
                    else None
                )
                if owner_id is None and declaration.optional:
                    continue
                owner_label = (
                    getattr(record, declaration.owner_kind_field)
                    if declaration.owner_kind_field is not None
                    else declaration.owner_label
                )
                child_identity = {
                    field: getattr(record, field)
                    for field in declaration.child_identity_fields
                }
                owner_identity = (
                    {
                        property_name: getattr(record, field_name)
                        for property_name, field_name in (
                            declaration.owner_identity_fields
                        )
                    }
                    if declaration.owner_identity_fields
                    else {"id": owner_id}
                )
                edges.append(
                    (
                        NodeRef(owner_label, owner_identity),
                        declaration.edge_type,
                        NodeRef(declaration.child_label, child_identity),
                    )
                )

        active_edges: list[tuple[NodeRef, str, NodeRef | None]] = []
        for declaration in REVISION_DECLARATIONS:
            revisions = getattr(self.records, declaration.revision_collection).values()
            stable_collection = getattr(self.records, declaration.stable_collection)
            for revision in revisions:
                stable_id = getattr(revision, declaration.revision_owner_field)
                if stable_collection.get(stable_id) is None:
                    continue
                edges.append(
                    (
                        node_ref(declaration.stable_label, id=stable_id),
                        "HAS_REVISION",
                        node_ref(declaration.revision_label, id=revision.id),
                    )
                )
            stable_records = getattr(
                self.records, declaration.stable_collection
            ).values()
            for stable in stable_records:
                active_id = getattr(stable, "active_revision_id", None)
                active_edges.append(
                    (
                        node_ref(declaration.stable_label, id=stable.id),
                        "ACTIVE_REVISION",
                        (
                            None
                            if active_id is None
                            else node_ref(declaration.revision_label, id=active_id)
                        ),
                    )
                )

        edges.extend(self._reference_edges())
        return IntegrityPlan(
            nodes=_dedupe_refs(nodes),
            edges=_dedupe_edges(edges),
            active_edges=active_edges,
        )

    def _reference_edges(self) -> list[tuple[NodeRef, str, NodeRef]]:
        edges: list[tuple[NodeRef, str, NodeRef]] = []
        for label, collection in (
            ("SystemRevision", self.records.system_revisions),
            ("ApplicationRevision", self.records.revisions),
            ("ExperienceRevision", self.records.experience_revisions),
        ):
            for revision in collection.values():
                revision_ref = node_ref(label, id=revision.id)
                for edge in self.store.list_edges(revision_ref, "HAS_SOURCE_TREE"):
                    edges.append((revision_ref, "HAS_SOURCE_TREE", edge.to_ref))
                for edge_type in (
                    "HAS_ACTION_REVISION",
                    "HAS_OBJECT_TYPE_REVISION",
                    "HAS_RELATION_TYPE_REVISION",
                    "HAS_TRIGGER_REVISION",
                    "HAS_TEST",
                    "DECLARES_DEPENDENCY",
                    "DECLARES_SURFACE",
                ):
                    for edge in self.store.list_edges(revision_ref, edge_type):
                        edges.append((revision_ref, edge_type, edge.to_ref))
        for revision in self.records.revisions.values():
            if revision.parent_revision_id is not None:
                edges.append(
                    (
                        node_ref("ApplicationRevision", id=revision.id),
                        "PARENT_REVISION",
                        node_ref("ApplicationRevision", id=revision.parent_revision_id),
                    )
                )
        for revision in self.records.experience_revisions.values():
            revision_ref = node_ref("ExperienceRevision", id=revision.id)
            if revision.parent_revision_id is not None:
                edges.append(
                    (
                        revision_ref,
                        "PARENT_REVISION",
                        node_ref(
                            "ExperienceRevision",
                            id=revision.parent_revision_id,
                        ),
                    )
                )
            edges.extend(
                (
                    revision_ref,
                    "USES_APPLICATION",
                    node_ref("Application", id=access.application_id),
                )
                for access in revision.application_access
            )
        # HAS_FILE is store-authoritative (path/role/language live on the
        # edge). It is validated separately and must not be reconstructed
        # without those properties.
        for revision in self.records.relation_type_revisions.values():
            revision_ref = node_ref("RelationTypeRevision", id=revision.id)
            if self.records.object_types.get(revision.from_object_type_id) is not None:
                edges.append(
                    (
                        revision_ref,
                        "FROM_TYPE",
                        node_ref("ObjectType", id=revision.from_object_type_id),
                    )
                )
            if self.records.object_types.get(revision.to_object_type_id) is not None:
                edges.append(
                    (
                        revision_ref,
                        "TO_TYPE",
                        node_ref("ObjectType", id=revision.to_object_type_id),
                    )
                )
        for revision in self.records.action_revisions.values():
            revision_ref = node_ref("ActionRevision", id=revision.id)
            edges.extend(
                (
                    revision_ref,
                    "DEPENDS_ON",
                    node_ref("RuntimeDependency", id=dependency_id),
                )
                for dependency_id in revision.runtime_dependency_ids
                if self.records.runtime_dependencies.get(dependency_id) is not None
            )
            edges.extend(
                (
                    revision_ref,
                    "USES_SECRET",
                    node_ref("SecretReference", id=secret_id),
                )
                for secret_id in revision.secret_refs
                if self.records.secret_references.get(secret_id) is not None
            )
        for revision in self.records.trigger_revisions.values():
            if self.records.actions.get(revision.action_id) is None:
                continue
            edges.append(
                (
                    node_ref("TriggerRevision", id=revision.id),
                    "INVOKES",
                    node_ref("Action", id=revision.action_id),
                )
            )
        for obj in self.records.objects.values():
            object_ref = node_ref(
                "ApplicationObject",
                application_id=obj.application_id,
                data_space_id=obj.data_space_id,
                id=obj.id,
            )
            edges.append(
                (
                    object_ref,
                    "INSTANCE_OF",
                    node_ref("ObjectType", id=obj.object_type_id),
                )
            )
            if obj.object_type_revision_id is not None:
                edges.append(
                    (
                        object_ref,
                        "CONFORMS_TO",
                        node_ref(
                            "ObjectTypeRevision",
                            id=obj.object_type_revision_id,
                        ),
                    )
                )
        for credential in self.records.credential_records.values():
            edges.extend(
                (
                    node_ref("CredentialRecord", id=credential.id),
                    "USES_SECRET",
                    node_ref("SecretReference", id=secret_id),
                )
                for secret_id in credential.secret_reference_ids
            )
        for route in self.records.callback_routes.values():
            route_ref = node_ref("CallbackRoute", id=route.id)
            edges.append(
                (
                    route_ref,
                    "TARGETS",
                    node_ref("ActionRevision", id=route.target_action_revision_id),
                )
            )
            invocation_id = route.metadata.get("invocation_id")
            if isinstance(invocation_id, str):
                edges.append(
                    (
                        route_ref,
                        "INVOKED",
                        node_ref("Invocation", id=invocation_id),
                    )
                )
        for test in self.records.test_cases.values():
            if self.records.actions.get(test.action_id) is None:
                continue
            edges.append(
                (
                    node_ref("TestCase", id=test.id),
                    "TESTS",
                    node_ref("Action", id=test.action_id),
                )
            )
        for checkpoint in self.records.checkpoints.values():
            if checkpoint.application_revision_id is not None:
                edges.append(
                    (
                        node_ref("GraphCheckpoint", id=checkpoint.id),
                        "CAPTURES_REVISION",
                        node_ref(
                            "ApplicationRevision",
                            id=checkpoint.application_revision_id,
                        ),
                    )
                )
        for invocation in self.records.invocations.values():
            edges.append(
                (
                    node_ref("Invocation", id=invocation.id),
                    "RAN",
                    node_ref("ActionRevision", id=invocation.action_revision_id),
                )
            )
        for job in self.records.job_records.values():
            if job.invocation_id is not None:
                edges.append(
                    (
                        node_ref("JobRecord", id=job.id),
                        "INVOKED",
                        node_ref("Invocation", id=job.invocation_id),
                    )
                )
            if job.current_attempt_id is not None:
                edges.append(
                    (
                        node_ref("JobRecord", id=job.id),
                        "CURRENT_ATTEMPT",
                        node_ref("JobAttempt", id=job.current_attempt_id),
                    )
                )
        for attempt in self.records.job_attempts.values():
            if attempt.invocation_id is not None:
                edges.append(
                    (
                        node_ref("JobAttempt", id=attempt.id),
                        "INVOKED",
                        node_ref("Invocation", id=attempt.invocation_id),
                    )
                )
        for audit in self.records.cypher_audits.values():
            if self.records.invocations.get(audit.invocation_id) is not None:
                edges.append(
                    (
                        node_ref("Invocation", id=audit.invocation_id),
                        "HAS_CYPHER_AUDIT",
                        node_ref("CypherAuditRecord", id=audit.id),
                    )
                )
            if audit.checkpoint_id is not None:
                edges.append(
                    (
                        node_ref("CypherAuditRecord", id=audit.id),
                        "AT_CHECKPOINT",
                        node_ref("GraphCheckpoint", id=audit.checkpoint_id),
                    )
                )
        return edges

    def _validate_experience_references(self) -> list[str]:
        errors: list[str] = []
        for revision in self.records.experience_revisions.values():
            for access in revision.application_access:
                application = self.records.applications.get(access.application_id)
                if application is None:
                    errors.append(
                        "missing application access target: "
                        f"{revision.id}->{access.application_id}"
                    )
                    continue
                references = (
                    ("object type", access.object_type_ids, self.records.object_types),
                    (
                        "relation type",
                        access.relation_type_ids,
                        self.records.relation_types,
                    ),
                    ("action", access.action_ids, self.records.actions),
                    (
                        "secret reference",
                        access.secret_reference_ids,
                        self.records.secret_references,
                    ),
                )
                for kind, identifiers, collection in references:
                    if identifiers is None:
                        continue
                    for identifier in identifiers:
                        target = collection.get(identifier)
                        if target is None or target.application_id != application.id:
                            errors.append(
                                f"invalid {kind} access reference: "
                                f"{revision.id}->{identifier}"
                            )
        return errors

    def _validate_revision_source_ownership(
        self, actual_edges: list[EdgeRecord]
    ) -> list[str]:
        errors: list[str] = []
        seen_paths: dict[str, set[str]] = {}
        for label, collection in (
            ("SystemRevision", self.records.system_revisions),
            ("ApplicationRevision", self.records.revisions),
            ("ExperienceRevision", self.records.experience_revisions),
        ):
            for revision in collection.values():
                trees = _matching_edges(
                    actual_edges,
                    node_ref(label, id=revision.id),
                    "HAS_SOURCE_TREE",
                    None,
                )
                if len(trees) != 1:
                    errors.append(
                        f"{label} {revision.id} must have exactly one HAS_SOURCE_TREE"
                    )
                    continue
                tree = self.records.source_trees.get(trees[0].to_ref.identity["id"])
                if tree is None:
                    errors.append(
                        f"missing SourceTree for {label} {revision.id}"
                    )
                    continue
                if tree.status == "draft":
                    if tree.id != f"{revision.id}.source" or tree.owner_id != revision.id:
                        errors.append(
                            f"invalid working SourceTree for {label} {revision.id}: "
                            f"{tree.id}"
                        )
                elif tree.status != "interned" or tree.id != tree.tree_hash:
                    errors.append(
                        f"invalid interned SourceTree for {label} {revision.id}: "
                        f"{tree.id}"
                    )
        for tree in self.records.source_trees.values():
            paths: set[str] = set()
            for edge in _matching_edges(
                actual_edges,
                node_ref("SourceTree", id=tree.id),
                "HAS_FILE",
                None,
            ):
                path = str(edge.props.get("path") or "")
                if not path or path in paths:
                    errors.append(
                        f"invalid HAS_FILE path on {tree.id}: {path or '<empty>'}"
                    )
                paths.add(path)
            seen_paths[tree.id] = paths
        return errors

    def _validate_surfaces(self, actual_edges: list[EdgeRecord]) -> list[str]:
        errors: list[str] = []
        browser_mounts: dict[str, dict[str, str]] = {}
        for revision in self.records.experience_revisions.values():
            revision_ref = node_ref("ExperienceRevision", id=revision.id)
            declared = _matching_edges(
                actual_edges, revision_ref, "DECLARES_SURFACE", None
            )
            surface_ids: set[str] = set()
            for edge in declared:
                surface = self.records.experience_surfaces.get(
                    edge.to_ref.identity["id"]
                )
                if surface is None:
                    errors.append(
                        f"missing ExperienceSurface declared by {revision.id}"
                    )
                    continue
                if surface.surface_id in surface_ids:
                    errors.append(
                        f"duplicate surface_id {surface.surface_id} on {revision.id}"
                    )
                surface_ids.add(surface.surface_id)
                if surface.surface_type == "browser":
                    mount_path = surface.config["mount_path"]
                    mounts = browser_mounts.setdefault(revision.id, {})
                    existing = mounts.get(mount_path)
                    if existing is not None:
                        errors.append(
                            "duplicate browser Surface mount_path: "
                            f"{revision.id}:{mount_path} "
                            f"({existing}, {surface.surface_id})"
                        )
                    mounts[mount_path] = surface.surface_id
                forbidden = [
                    item
                    for item in actual_edges
                    if (
                        item.from_ref == edge.to_ref or item.to_ref == edge.to_ref
                    )
                    and item.type in {"HAS_REVISION", "ACTIVE_REVISION"}
                ]
                if forbidden:
                    errors.append(
                        "ExperienceSurface must not have revision or active edges: "
                        f"{surface.surface_id}"
                    )
        return errors

    def _validate_reachability(
        self, nodes: list[NodeRef], edges: list[EdgeRecord]
    ) -> dict[str, list[str]]:
        interned_labels = {
            "SourceContent",
            "ActionRevision",
            "ObjectTypeRevision",
            "RelationTypeRevision",
            "TriggerRevision",
            "TestCase",
            "RuntimeDependency",
            "ExperienceSurface",
        }
        root_key = _ref_key(node_ref("N4XRoot", id="n4x"))
        node_keys = {_ref_key(ref) for ref in nodes}
        if (
            root_key not in node_keys
            or self.store.get_node("N4XRoot", {"id": "n4x"}) is None
        ):
            return {"errors": ["missing N4XRoot node"], "warnings": []}
        adjacency: dict[tuple[Any, ...], set[tuple[Any, ...]]] = {}
        for edge in edges:
            adjacency.setdefault(_ref_key(edge.from_ref), set()).add(
                _ref_key(edge.to_ref)
            )
        reachable = {root_key}
        pending = [root_key]
        while pending:
            current = pending.pop()
            for target in adjacency.get(current, set()):
                if target not in reachable:
                    reachable.add(target)
                    pending.append(target)
        errors: list[str] = []
        warnings: list[str] = []
        for ref in nodes:
            if _ref_key(ref) == root_key or _ref_key(ref) in reachable:
                continue
            message = f"unreachable node: {_format_ref(ref)}"
            interned_tree = False
            if ref.label == "SourceTree":
                tree = self.records.source_trees.get(ref.identity.get("id", ""))
                interned_tree = tree is not None and tree.status == "interned"
            if ref.label in interned_labels or interned_tree:
                warnings.append(message)
            else:
                errors.append(message)
        return {"errors": errors, "warnings": warnings}

    def _validate_app_relations(self, edges: list[EdgeRecord]) -> list[str]:
        errors: list[str] = []
        relations = self.uow.relations.list_all()
        relations_by_identity = {
            (
                relation.application_id,
                relation.data_space_id,
                relation.id,
            ): relation
            for relation in relations
        }
        physical_edges = [edge for edge in edges if edge.type.startswith("APP_REL_")]
        for edge in physical_edges:
            identity = (
                edge.props.get("application_id"),
                edge.props.get("data_space_id"),
                edge.props.get("id"),
            )
            if identity not in relations_by_identity:
                errors.append(f"unrecognized physical app relation edge: {edge.type}")
        for relation in relations:
            relation_type = self.records.relation_types.get(relation.relation_type_id)
            relation_revision = (
                None
                if relation.relation_type_revision_id is None
                else self.records.relation_type_revisions.get(
                    relation.relation_type_revision_id
                )
            )
            from_object = self.uow.objects.get(
                relation.application_id,
                relation.from_object_id,
                relation.data_space_id,
            )
            to_object = self.uow.objects.get(
                relation.application_id,
                relation.to_object_id,
                relation.data_space_id,
            )
            if (
                relation_type is None
                or relation_type.application_id != relation.application_id
            ):
                errors.append(f"invalid app relation type ownership: {relation.id}")
                continue
            if relation_revision is None:
                errors.append(f"missing app relation revision: {relation.id}")
                continue
            expected_physical = relation_physical_type(relation.relation_type_id)
            if (
                relation.physical_type != expected_physical
                or relation_revision.physical_type != expected_physical
            ):
                errors.append(f"invalid physical relation type: {relation.id}")
            if (
                from_object is None
                or to_object is None
                or from_object.application_id != relation.application_id
                or to_object.application_id != relation.application_id
            ):
                errors.append(f"invalid app relation endpoints: {relation.id}")
                continue
            if (
                from_object.object_type_id != relation_revision.from_object_type_id
                or to_object.object_type_id != relation_revision.to_object_type_id
            ):
                errors.append(f"app relation endpoint type mismatch: {relation.id}")
        return errors


def _matching_edges(
    edges: list[EdgeRecord],
    from_ref: NodeRef,
    edge_type: str,
    to_ref: NodeRef | None,
) -> list[EdgeRecord]:
    return [
        edge
        for edge in edges
        if edge.from_ref == from_ref
        and edge.type == edge_type
        and (to_ref is None or edge.to_ref == to_ref)
    ]


def _ref_key(ref: NodeRef) -> tuple[Any, ...]:
    return (
        ref.label,
        tuple(sorted(ref.identity.items())),
    )


def _format_ref(ref: NodeRef) -> str:
    return f"({ref.label} {ref.identity})"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _dedupe_refs(values: list[NodeRef]) -> list[NodeRef]:
    seen: set[tuple[Any, ...]] = set()
    result = []
    for value in values:
        key = _ref_key(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _dedupe_edges(
    values: list[tuple[NodeRef, str, NodeRef]],
) -> list[tuple[NodeRef, str, NodeRef]]:
    seen: set[tuple[Any, ...]] = set()
    result = []
    for from_ref, edge_type, to_ref in values:
        key = (_ref_key(from_ref), edge_type, _ref_key(to_ref))
        if key not in seen:
            seen.add(key)
            result.append((from_ref, edge_type, to_ref))
    return result
