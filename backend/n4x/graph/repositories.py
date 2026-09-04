from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from types import UnionType
from typing import TYPE_CHECKING, Any, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel

from n4x.graph.store import NodeRef, node_ref
from n4x.kernel.models import (
    Application,
    ApplicationObject,
    ApplicationRelation,
    ApplicationRevision,
    Action,
    ActionRevision,
    AppBlueprint,
    AuthoringGuide,
    AuthoringGuideRevision,
    BlueprintRevision,
    BuildArtifact,
    BuildInvocation,
    CallbackRoute,
    CheckpointBlob,
    CheckpointSnapshot,
    CredentialRecord,
    CypherAuditRecord,
    DataSpace,
    DevelopmentDeployment,
    Experience,
    ExperienceRevision,
    ExperienceSurface,
    ExperienceValidationReport,
    GraphCheckpoint,
    Invocation,
    JavaScriptEnvironment,
    JobAttempt,
    JobRecord,
    ObjectType,
    ObjectTypeRevision,
    PackageImportAttempt,
    PlatformSystem,
    PythonEnvironment,
    RelationType,
    RelationTypeRevision,
    RuntimeDependency,
    SecretReference,
    SourceChange,
    SourceFile,
    SourceTree,
    SystemRevision,
    TestCase,
    Trigger,
    TriggerRevision,
    UiTheme,
    UiThemeRevision,
    ValidationReport,
)

if TYPE_CHECKING:
    from n4x.graph.uow import GraphUnitOfWork


ModelT = TypeVar("ModelT", bound=BaseModel)
KeyT = TypeVar("KeyT")


class RecordCollection(Mapping[KeyT, ModelT]):
    """Live repository view over one durable node label; it never caches."""

    def __init__(
        self,
        uow: GraphUnitOfWork,
        label: str,
        model_type: type[ModelT],
        key_fields: tuple[str, ...] = ("id",),
    ) -> None:
        self.uow = uow
        self.label = label
        self.model_type = model_type
        self.key_fields = key_fields

    def __getitem__(self, key: KeyT) -> ModelT:
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __iter__(self) -> Iterator[KeyT]:
        for value in self.values():
            yield self.key_for(value)

    def __len__(self) -> int:
        with self._transaction():
            return len(self.uow.store.list_nodes(self.label))

    def __setitem__(self, key: KeyT, value: ModelT) -> None:
        if self.identity_for(key) != {
            field: getattr(value, field) for field in self.key_fields
        }:
            raise ValueError(f"record key does not match {self.label} identity")
        self.save(value)

    def __delitem__(self, key: KeyT) -> None:
        self.delete(key)

    def get(self, key: KeyT, default: Any = None) -> ModelT | Any:
        identity = self.identity_for(key)
        with self._transaction():
            values = self.uow.store.get_node(self.label, identity)
        if values is None:
            return default
        return self.model_type.model_validate(
            _decode_json_values(values, self.model_type)
        )

    def values(self) -> list[ModelT]:
        with self._transaction():
            rows = self.uow.store.list_nodes(self.label)
        return [
            self.model_type.model_validate(
                _decode_json_values(row, self.model_type)
            )
            for row in rows
        ]

    def items(self) -> list[tuple[KeyT, ModelT]]:
        values = self.values()
        return [(self.key_for(value), value) for value in values]

    def save(self, value: ModelT) -> None:
        identity = {
            field: getattr(value, field)
            for field in self.key_fields
        }
        with self._transaction():
            self.uow.store.acquire_write_lock(node_ref("N4XRoot", id="n4x"))
            self.uow.store.upsert_node(self.label, identity, value)

    def delete(self, key: KeyT) -> None:
        with self._transaction():
            self.uow.store.delete_node(self.label, self.identity_for(key))

    def append(self, value: ModelT) -> None:
        self.save(value)

    def clear(self) -> None:
        for key in list(self):
            self.delete(key)

    def identity_for(self, key: KeyT) -> dict[str, Any]:
        values = key if isinstance(key, tuple) else (key,)
        if len(values) != len(self.key_fields):
            raise KeyError(key)
        return dict(zip(self.key_fields, values, strict=True))

    def key_for(self, value: ModelT) -> KeyT:
        values = tuple(getattr(value, field) for field in self.key_fields)
        return (values[0] if len(values) == 1 else values)  # type: ignore[return-value]

    @contextmanager
    def _transaction(self):
        if self.uow.is_active:
            yield
        else:
            with self.uow:
                yield


class RepositoryRecords:
    """Typed, store-backed record repositories shared by composed services."""

    def __init__(self, uow: GraphUnitOfWork) -> None:
        self.applications = RecordCollection(uow, "Application", Application)
        self.systems = RecordCollection(uow, "System", PlatformSystem)
        self.system_revisions = RecordCollection(
            uow, "SystemRevision", SystemRevision
        )
        self.data_spaces = RecordCollection(
            uow,
            "DataSpace",
            DataSpace,
            ("application_id", "id"),
        )
        self.development_deployments = RecordCollection(
            uow,
            "DevelopmentDeployment",
            DevelopmentDeployment,
        )
        self.revisions = RecordCollection(
            uow, "ApplicationRevision", ApplicationRevision
        )
        self.experiences = RecordCollection(uow, "Experience", Experience)
        self.experience_revisions = RecordCollection(
            uow, "ExperienceRevision", ExperienceRevision
        )
        self.experience_surfaces = RecordCollection(
            uow,
            "ExperienceSurface",
            ExperienceSurface,
            ("experience_revision_id", "surface_id"),
        )
        self.experience_validation_reports = RecordCollection(
            uow, "ExperienceValidationReport", ExperienceValidationReport
        )
        self.authoring_guides = RecordCollection(
            uow, "AuthoringGuide", AuthoringGuide
        )
        self.authoring_guide_revisions = RecordCollection(
            uow, "AuthoringGuideRevision", AuthoringGuideRevision
        )
        self.ui_themes = RecordCollection(uow, "UiTheme", UiTheme)
        self.ui_theme_revisions = RecordCollection(
            uow, "UiThemeRevision", UiThemeRevision
        )
        self.app_blueprints = RecordCollection(uow, "AppBlueprint", AppBlueprint)
        self.blueprint_revisions = RecordCollection(
            uow, "BlueprintRevision", BlueprintRevision
        )
        self.source_trees = RecordCollection(uow, "SourceTree", SourceTree)
        self.source_files = RecordCollection(
            uow,
            "SourceFile",
            SourceFile,
            ("source_tree_id", "path"),
        )
        self.source_changes = RecordCollection(uow, "SourceChange", SourceChange)
        self.objects = RecordCollection(
            uow,
            "ApplicationObject",
            ApplicationObject,
            ("application_id", "data_space_id", "id"),
        )
        self.object_types = RecordCollection(uow, "ObjectType", ObjectType)
        self.object_type_revisions = RecordCollection(
            uow, "ObjectTypeRevision", ObjectTypeRevision
        )
        self.relation_types = RecordCollection(uow, "RelationType", RelationType)
        self.relation_type_revisions = RecordCollection(
            uow, "RelationTypeRevision", RelationTypeRevision
        )
        self.secret_references = RecordCollection(
            uow, "SecretReference", SecretReference
        )
        self.credential_records = RecordCollection(
            uow, "CredentialRecord", CredentialRecord
        )
        self.callback_routes = RecordCollection(
            uow, "CallbackRoute", CallbackRoute
        )
        self.runtime_dependencies = RecordCollection(
            uow, "RuntimeDependency", RuntimeDependency
        )
        self.python_environments = RecordCollection(
            uow, "PythonEnvironment", PythonEnvironment
        )
        self.javascript_environments = RecordCollection(
            uow, "JavaScriptEnvironment", JavaScriptEnvironment
        )
        self.build_invocations = RecordCollection(
            uow, "BuildInvocation", BuildInvocation
        )
        self.build_artifacts = RecordCollection(
            uow, "BuildArtifact", BuildArtifact
        )
        self.actions = RecordCollection(uow, "Action", Action)
        self.action_revisions = RecordCollection(
            uow, "ActionRevision", ActionRevision
        )
        self.triggers = RecordCollection(uow, "Trigger", Trigger)
        self.trigger_revisions = RecordCollection(
            uow, "TriggerRevision", TriggerRevision
        )
        self.job_records = RecordCollection(uow, "JobRecord", JobRecord)
        self.job_attempts = RecordCollection(uow, "JobAttempt", JobAttempt)
        self.cypher_audits = RecordCollection(
            uow, "CypherAuditRecord", CypherAuditRecord
        )
        self.invocations = RecordCollection(uow, "Invocation", Invocation)
        self.test_cases = RecordCollection(uow, "TestCase", TestCase)
        self.checkpoints = RecordCollection(
            uow, "GraphCheckpoint", GraphCheckpoint
        )
        self.checkpoint_snapshots = RecordCollection(
            uow, "CheckpointSnapshot", CheckpointSnapshot
        )
        self.checkpoint_blobs = RecordCollection(
            uow, "CheckpointBlob", CheckpointBlob
        )
        self.validation_reports = RecordCollection(
            uow, "ValidationReport", ValidationReport
        )
        self.package_import_attempts = RecordCollection(
            uow, "PackageImportAttempt", PackageImportAttempt
        )


class _BoundRepository:
    def __init__(self, uow: GraphUnitOfWork) -> None:
        self.uow = uow

    @property
    def store(self):
        self.uow.require_active()
        return self.uow.store

    @contextmanager
    def _transaction(self):
        if self.uow.is_active:
            yield
        else:
            with self.uow:
                yield

    def _save(
        self, label: str, identity: dict[str, Any], record: BaseModel
    ) -> None:
        with self._transaction():
            self.store.acquire_write_lock(node_ref("N4XRoot", id="n4x"))
            self.store.upsert_node(label, identity, record)

    def _get(
        self,
        label: str,
        identity: dict[str, Any],
        model_type: type[ModelT],
    ) -> ModelT | None:
        with self._transaction():
            values = self.store.get_node(label, identity)
        return (
            None
            if values is None
            else model_type.model_validate(
                _decode_json_values(values, model_type)
            )
        )

    def _list(
        self,
        label: str,
        model_type: type[ModelT],
        filters: dict[str, Any] | None = None,
    ) -> list[ModelT]:
        with self._transaction():
            rows = self.store.list_nodes(label, filters)
        return [
            model_type.model_validate(_decode_json_values(values, model_type))
            for values in rows
        ]


class ApplicationRepository(_BoundRepository):
    def save(self, application: Application) -> None:
        self._save("Application", {"id": application.id}, application)

    def get(self, application_id: str) -> Application | None:
        return self._get("Application", {"id": application_id}, Application)

    def list(self) -> list[Application]:
        return self._list("Application", Application)

    def delete(self, application_id: str) -> None:
        with self._transaction():
            self.store.delete_node("Application", {"id": application_id})

    def save_revision(self, revision: ApplicationRevision) -> None:
        self._save("ApplicationRevision", {"id": revision.id}, revision)

    def get_revision(self, revision_id: str) -> ApplicationRevision | None:
        return self._get(
            "ApplicationRevision", {"id": revision_id}, ApplicationRevision
        )

    def list_revisions(self, application_id: str) -> list[ApplicationRevision]:
        return self._list(
            "ApplicationRevision",
            ApplicationRevision,
            {"application_id": application_id},
        )

    def attach_revision(self, application_id: str, revision_id: str) -> None:
        with self._transaction():
            self.store.create_edge(
                node_ref("Application", id=application_id),
                "HAS_REVISION",
                node_ref("ApplicationRevision", id=revision_id),
            )

    def attach_to_root(self, application_id: str) -> None:
        with self._transaction():
            self.store.create_edge(
                node_ref("N4XRoot", id="n4x"),
                "HAS_APPLICATION",
                node_ref("Application", id=application_id),
            )

    def replace_active_revision(
        self,
        application_id: str,
        revision_id: str,
        *,
        expected_revision_id: str | None,
    ) -> None:
        with self._transaction():
            self.store.replace_single_edge(
                node_ref("Application", id=application_id),
                "ACTIVE_REVISION",
                node_ref("ApplicationRevision", id=revision_id),
                expected_to_ref=(
                    None
                    if expected_revision_id is None
                    else node_ref("ApplicationRevision", id=expected_revision_id)
                ),
                require_current_match=True,
            )


class SystemRepository(_BoundRepository):
    def save(self, system: PlatformSystem) -> None:
        self._save("System", {"id": system.id}, system)

    def get(self, system_id: str) -> PlatformSystem | None:
        return self._get("System", {"id": system_id}, PlatformSystem)

    def save_revision(self, revision: SystemRevision) -> None:
        self._save("SystemRevision", {"id": revision.id}, revision)

    def get_revision(self, revision_id: str) -> SystemRevision | None:
        return self._get("SystemRevision", {"id": revision_id}, SystemRevision)

    def list_revisions(self, system_id: str) -> list[SystemRevision]:
        return self._list(
            "SystemRevision",
            SystemRevision,
            {"system_id": system_id},
        )

    def attach_to_root(self, system_id: str) -> None:
        with self._transaction():
            self.store.create_edge(
                node_ref("N4XRoot", id="n4x"),
                "HAS_SYSTEM",
                node_ref("System", id=system_id),
            )

    def attach_revision(self, system_id: str, revision_id: str) -> None:
        with self._transaction():
            self.store.create_edge(
                node_ref("System", id=system_id),
                "HAS_REVISION",
                node_ref("SystemRevision", id=revision_id),
            )

    def replace_active_revision(
        self,
        system_id: str,
        revision_id: str,
        *,
        expected_revision_id: str | None,
    ) -> None:
        with self._transaction():
            self.store.replace_single_edge(
                node_ref("System", id=system_id),
                "ACTIVE_REVISION",
                node_ref("SystemRevision", id=revision_id),
                expected_to_ref=(
                    None
                    if expected_revision_id is None
                    else node_ref("SystemRevision", id=expected_revision_id)
                ),
                require_current_match=True,
            )


class ExperienceRepository(_BoundRepository):
    def save(self, experience: Experience) -> None:
        self._save("Experience", {"id": experience.id}, experience)

    def get(self, experience_id: str) -> Experience | None:
        return self._get("Experience", {"id": experience_id}, Experience)

    def list(self) -> list[Experience]:
        return self._list("Experience", Experience)

    def delete(self, experience_id: str) -> None:
        with self._transaction():
            self.store.delete_node("Experience", {"id": experience_id})

    def save_revision(self, revision: ExperienceRevision) -> None:
        self._save("ExperienceRevision", {"id": revision.id}, revision)

    def get_revision(self, revision_id: str) -> ExperienceRevision | None:
        return self._get(
            "ExperienceRevision", {"id": revision_id}, ExperienceRevision
        )

    def list_revisions(self, experience_id: str) -> list[ExperienceRevision]:
        return self._list(
            "ExperienceRevision",
            ExperienceRevision,
            {"experience_id": experience_id},
        )

    def attach_to_root(self, experience_id: str) -> None:
        with self._transaction():
            self.store.create_edge(
                node_ref("N4XRoot", id="n4x"),
                "HAS_EXPERIENCE",
                node_ref("Experience", id=experience_id),
            )

    def attach_revision(self, experience_id: str, revision_id: str) -> None:
        with self._transaction():
            self.store.create_edge(
                node_ref("Experience", id=experience_id),
                "HAS_REVISION",
                node_ref("ExperienceRevision", id=revision_id),
            )

    def replace_active_revision(
        self,
        experience_id: str,
        revision_id: str,
        *,
        expected_revision_id: str | None,
    ) -> None:
        with self._transaction():
            self.store.replace_single_edge(
                node_ref("Experience", id=experience_id),
                "ACTIVE_REVISION",
                node_ref("ExperienceRevision", id=revision_id),
                expected_to_ref=(
                    None
                    if expected_revision_id is None
                    else node_ref(
                        "ExperienceRevision", id=expected_revision_id
                    )
                ),
                require_current_match=True,
            )

    def clear_active_revision(
        self,
        experience_id: str,
        *,
        expected_revision_id: str | None,
    ) -> None:
        with self._transaction():
            self.store.clear_single_edge(
                node_ref("Experience", id=experience_id),
                "ACTIVE_REVISION",
                expected_to_ref=(
                    None
                    if expected_revision_id is None
                    else node_ref(
                        "ExperienceRevision", id=expected_revision_id
                    )
                ),
            )


class DefinitionRepository(_BoundRepository):
    LABELS = {
        "ObjectType",
        "ObjectTypeRevision",
        "RelationType",
        "RelationTypeRevision",
        "Action",
        "ActionRevision",
        "Trigger",
        "TriggerRevision",
        "TestCase",
        "RuntimeDependency",
        "ValidationReport",
    }

    def save(self, label: str, record: BaseModel) -> None:
        self._validate_label(label)
        self._save(label, {"id": str(record.id)}, record)

    def get(self, label: str, record_id: str) -> dict[str, Any] | None:
        self._validate_label(label)
        with self._transaction():
            return self.store.get_node(label, {"id": record_id})

    def list(
        self, label: str, filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self._validate_label(label)
        with self._transaction():
            return self.store.list_nodes(label, filters)

    def delete(self, label: str, record_id: str) -> None:
        self._validate_label(label)
        with self._transaction():
            self.store.delete_node(label, {"id": record_id})

    def add_revision(
        self,
        stable_label: str,
        stable_id: str,
        revision_label: str,
        revision_id: str,
    ) -> None:
        self._validate_label(stable_label)
        self._validate_label(revision_label)
        with self._transaction():
            self.store.create_edge(
                node_ref(stable_label, id=stable_id),
                "HAS_REVISION",
                node_ref(revision_label, id=revision_id),
            )

    def replace_active(
        self,
        stable_label: str,
        stable_id: str,
        revision_label: str,
        revision_id: str,
        *,
        expected_revision_id: str | None,
    ) -> None:
        self._validate_label(stable_label)
        self._validate_label(revision_label)
        with self._transaction():
            self.store.replace_single_edge(
                node_ref(stable_label, id=stable_id),
                "ACTIVE_REVISION",
                node_ref(revision_label, id=revision_id),
                expected_to_ref=(
                    None
                    if expected_revision_id is None
                    else node_ref(revision_label, id=expected_revision_id)
                ),
                require_current_match=True,
            )

    def _validate_label(self, label: str) -> None:
        if label not in self.LABELS:
            raise ValueError(f"unsupported definition label: {label}")


class SourceRepository(_BoundRepository):
    def save_tree(self, tree: SourceTree) -> None:
        self._save("SourceTree", {"id": tree.id}, tree)

    def get_tree(self, tree_id: str) -> SourceTree | None:
        return self._get("SourceTree", {"id": tree_id}, SourceTree)

    def save_file(self, source_file: SourceFile) -> None:
        identity = {
            "source_tree_id": source_file.source_tree_id,
            "path": source_file.path,
        }
        self._save("SourceFile", identity, source_file)

    def get_file(self, tree_id: str, path: str) -> SourceFile | None:
        return self._get(
            "SourceFile",
            {"source_tree_id": tree_id, "path": path},
            SourceFile,
        )

    def list_files(self, tree_id: str) -> list[SourceFile]:
        return self._list("SourceFile", SourceFile, {"source_tree_id": tree_id})

    def save_change(self, change: SourceChange) -> None:
        self._save("SourceChange", {"id": change.id}, change)

    def link_file(self, tree_id: str, path: str) -> None:
        with self._transaction():
            self.store.create_edge(
                node_ref("SourceTree", id=tree_id),
                "HAS_FILE",
                node_ref("SourceFile", source_tree_id=tree_id, path=path),
            )


class ObjectRepository(_BoundRepository):
    def save(self, application_object: ApplicationObject) -> None:
        self._save(
            "ApplicationObject",
            {
                "application_id": application_object.application_id,
                "data_space_id": application_object.data_space_id,
                "id": application_object.id,
            },
            application_object,
        )

    def get(
        self,
        application_id: str,
        object_id: str,
        data_space_id: str = "production",
    ) -> ApplicationObject | None:
        return self._get(
            "ApplicationObject",
            {
                "application_id": application_id,
                "data_space_id": data_space_id,
                "id": object_id,
            },
            ApplicationObject,
        )

    def list(
        self,
        application_id: str,
        object_type_id: str | None = None,
        *,
        data_space_id: str = "production",
    ) -> list[ApplicationObject]:
        filters = {
            "application_id": application_id,
            "data_space_id": data_space_id,
        }
        if object_type_id is not None:
            filters["object_type_id"] = object_type_id
        return self._list("ApplicationObject", ApplicationObject, filters)

    def delete(
        self,
        application_id: str,
        object_id: str,
        data_space_id: str = "production",
    ) -> None:
        with self._transaction():
            self.store.delete_node(
                "ApplicationObject",
                {
                    "application_id": application_id,
                    "data_space_id": data_space_id,
                    "id": object_id,
                },
            )

    def attach(
        self,
        application_object: ApplicationObject,
        object_type_revision_id: str | None = None,
    ) -> None:
        object_ref = node_ref(
            "ApplicationObject",
            application_id=application_object.application_id,
            data_space_id=application_object.data_space_id,
            id=application_object.id,
        )
        with self._transaction():
            self.store.create_edge(
                node_ref(
                    "DataSpace",
                    application_id=application_object.application_id,
                    id=application_object.data_space_id,
                ),
                "OWNS_OBJECT",
                object_ref,
            )
            self.store.create_edge(
                object_ref,
                "INSTANCE_OF",
                node_ref("ObjectType", id=application_object.object_type_id),
            )
            revision_id = (
                object_type_revision_id or application_object.object_type_revision_id
            )
            if revision_id is not None:
                self.store.create_edge(
                    object_ref,
                    "CONFORMS_TO",
                    node_ref("ObjectTypeRevision", id=revision_id),
                )


class RelationRepository(_BoundRepository):
    def save(self, relation: ApplicationRelation) -> None:
        with self._transaction():
            self.store.acquire_write_lock(node_ref("N4XRoot", id="n4x"))
            self.store.create_app_relation(relation)

    def get(
        self,
        application_id: str,
        relation_id: str,
        data_space_id: str = "production",
    ) -> ApplicationRelation | None:
        with self._transaction():
            return self.store.get_app_relation(
                application_id, data_space_id, relation_id
            )

    def list_all(self) -> list[ApplicationRelation]:
        with self._transaction():
            return self.store.list_all_app_relations()

    def delete(self, relation: ApplicationRelation) -> None:
        with self._transaction():
            self.store.delete_app_relation(relation)

    def list(
        self,
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
        *,
        data_space_id: str = "production",
    ) -> list[ApplicationRelation]:
        with self._transaction():
            return self.store.list_app_relations(
                application_id,
                relation_type_id,
                from_object_id,
                to_object_id,
                data_space_id=data_space_id,
            )

    def create_structural(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef,
        props: dict[str, Any] | None = None,
    ) -> None:
        with self._transaction():
            self.store.create_edge(from_ref, edge_type, to_ref, props)

    def delete_structural(
        self,
        from_ref: NodeRef,
        edge_type: str,
        to_ref: NodeRef | None = None,
    ) -> None:
        with self._transaction():
            self.store.delete_edge(from_ref, edge_type, to_ref)


class JobRepository(_BoundRepository):
    def save(self, job: JobRecord) -> None:
        self._save("JobRecord", {"id": job.id}, job)

    def get(self, job_id: str) -> JobRecord | None:
        return self._get("JobRecord", {"id": job_id}, JobRecord)

    def list(
        self, application_id: str, status: str | None = None
    ) -> list[JobRecord]:
        filters = {"application_id": application_id}
        if status is not None:
            filters["status"] = status
        return self._list("JobRecord", JobRecord, filters)

    def save_attempt(self, attempt: JobAttempt) -> None:
        self._save("JobAttempt", {"id": attempt.id}, attempt)

    def list_attempts(self, job_id: str) -> list[JobAttempt]:
        return self._list("JobAttempt", JobAttempt, {"job_id": job_id})

    def due_retries(self, now) -> list[JobRecord]:
        return [
            job
            for job in self._list("JobRecord", JobRecord)
            if job.status == "retry_wait"
            and job.next_retry_at is not None
            and job.next_retry_at <= now
        ]

    def expired_leases(self, now) -> list[JobRecord]:
        return [
            job
            for job in self._list("JobRecord", JobRecord)
            if job.status in {"leased", "running"}
            and job.lease_expires_at is not None
            and job.lease_expires_at <= now
        ]


class ArtifactRepository(_BoundRepository):
    def save_artifact(self, artifact: BuildArtifact) -> None:
        self._save("BuildArtifact", {"id": artifact.id}, artifact)

    def get_artifact(self, artifact_id: str) -> BuildArtifact | None:
        return self._get("BuildArtifact", {"id": artifact_id}, BuildArtifact)

    def list_artifacts(
        self,
        application_revision_id: str,
        *,
        owner_kind: str = "ApplicationRevision",
    ) -> list[BuildArtifact]:
        return self._list(
            "BuildArtifact",
            BuildArtifact,
            {
                "owner_kind": owner_kind,
                "owner_id": application_revision_id,
            },
        )

    def save_invocation(self, invocation: BuildInvocation) -> None:
        self._save("BuildInvocation", {"id": invocation.id}, invocation)


def _annotation_is_json_encoded(annotation: Any) -> bool:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {Union, UnionType}:
        return any(
            _annotation_is_json_encoded(arg)
            for arg in args
            if arg is not type(None)
        )
    if origin is dict:
        return True
    if origin is list:
        if not args:
            return True
        inner = args[0]
        inner_origin = get_origin(inner) or inner
        if inner_origin is dict:
            return True
        return isinstance(inner_origin, type) and issubclass(
            inner_origin, BaseModel
        )
    if annotation is Any:
        return True
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _decode_json_values(
    values: dict[str, Any], model_type: type[BaseModel]
) -> dict[str, Any]:
    decoded = dict(values)
    for name, field in model_type.model_fields.items():
        value = decoded.get(name)
        if not isinstance(value, str) or not value.startswith(("{", "[")):
            continue
        if not _annotation_is_json_encoded(field.annotation):
            continue
        try:
            decoded[name] = json.loads(value)
        except json.JSONDecodeError:
            pass
    return decoded
