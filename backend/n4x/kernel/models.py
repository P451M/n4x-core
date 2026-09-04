from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def now_utc() -> datetime:
    return datetime.now(UTC)


ApplicationStatus = Literal["active", "disabled", "triggers_paused", "importing"]
DataSpaceKind = Literal["production", "development"]
DevelopmentDeploymentStatus = Literal["active", "expired"]
RevisionStatus = Literal["draft", "validating", "active", "rejected", "superseded"]
UiProfile = Literal["n4x-default", "custom", "none"]
SourceTreeStatus = Literal["draft", "immutable_snapshot"]
SourceRole = Literal["action", "surface", "migration", "test", "helper", "config"]
SourceOperation = Literal["add", "modify", "delete", "rename"]
ActionKind = Literal["normal", "migration", "test_helper"]
InvocationKind = Literal[
    "draft", "active", "callback", "migration", "migration_dry_run", "test"
]
InvocationStatus = Literal[
    "queued", "running", "succeeded", "failed", "cancelled"
]
BuildKind = Literal[
    "python_env",
    "javascript_env",
    "surface_build",
    "source_materialization",
    "validation",
]
BuildStatus = Literal["started", "succeeded", "failed"]
EnvironmentStatus = Literal["pending", "ready", "failed"]
TriggerType = Literal["schedule", "event", "external"]
TriggerOverlapPolicy = Literal[
    "skip_if_running", "queue", "run_concurrently", "replace_running"
]
TriggerMisfirePolicy = Literal["skip", "run_once", "enqueue_all"]
ActionConcurrencyPolicy = Literal["default", "reject_if_running"]
JobStatus = Literal[
    "scheduled",
    "leased",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "missed",
]
JobAttemptStatus = Literal[
    "leased",
    "running",
    "succeeded",
    "failed",
    "expired",
]
CypherAccessMode = Literal["read", "write"]
BlueprintRevisionStatus = Literal["draft", "active", "superseded"]
PlatformRevisionStatus = Literal["active", "superseded"]
CheckpointLevel = Literal["revision", "application_data"]
RevisionOwnerKind = Literal[
    "ApplicationRevision", "ExperienceRevision", "SystemRevision"
]
PackageImportStatus = Literal["running", "succeeded", "failed"]


class Application(BaseModel):
    id: str
    name: str
    description: str = ""
    active_revision_id: str | None = None
    status: ApplicationStatus = "active"
    created_at: datetime = Field(default_factory=now_utc)


class PlatformSystem(BaseModel):
    id: str
    name: str = "N4X"
    active_revision_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)


class SystemRevision(BaseModel):
    id: str
    system_id: str
    source_tree_id: str
    content_root: str
    host_abi: str
    action_context: str
    provenance_kind: Literal["official", "individual"] = "official"
    version: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class DataSpace(BaseModel):
    id: str
    application_id: str
    kind: DataSpaceKind
    created_at: datetime = Field(default_factory=now_utc)
    expires_at: datetime | None = None


class DataSpaceCloneSpec(BaseModel):
    selection_query: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    max_nodes: int = Field(default=500, ge=1, le=5000)
    max_relations: int = Field(default=2000, ge=0, le=10000)


class ExecutionContext(BaseModel):
    mode: Literal["production", "development"] = "production"
    deployment_id: str | None = None
    correlation_id: str
    experience_revision_id: str | None = None
    application_revision_id: str
    application_id: str
    data_space_id: str = "production"


class DevelopmentDeployment(BaseModel):
    id: str
    experience_revision_id: str
    application_revision_ids: dict[str, str] = Field(default_factory=dict)
    data_space_ids: dict[str, str] = Field(default_factory=dict)
    candidate_hashes: dict[str, str] = Field(default_factory=dict)
    status: DevelopmentDeploymentStatus = "active"
    created_at: datetime = Field(default_factory=now_utc)
    expires_at: datetime


class ApplicationRevision(BaseModel):
    id: str
    application_id: str
    source_tree_id: str
    parent_revision_id: str | None = None
    ui_profile: UiProfile = "n4x-default"
    status: RevisionStatus = "draft"
    created_at: datetime = Field(default_factory=now_utc)
    created_by: str = "system"


class ApplicationAccessDeclaration(BaseModel):
    """Revisioned access to one Application; None means unrestricted, [] means none."""

    application_id: str
    object_type_ids: list[str] | None = None
    relation_type_ids: list[str] | None = None
    action_ids: list[str] | None = None
    # Secret management is intentionally default-deny: both None and [] deny.
    secret_reference_ids: list[str] | None = None

    @field_validator(
        "application_id",
        "object_type_ids",
        "relation_type_ids",
        "action_ids",
        "secret_reference_ids",
    )
    @classmethod
    def validate_identifiers(cls, value):
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("application_id must not be empty")
            return value
        if value is None:
            return None
        if any(not item.strip() for item in value):
            raise ValueError("access allowlist identifiers must not be empty")
        if len(value) != len(set(value)):
            raise ValueError("access allowlist identifiers must be unique")
        return value


class Experience(BaseModel):
    id: str
    name: str
    description: str = ""
    active_revision_id: str | None = None
    status: ApplicationStatus = "active"
    created_at: datetime = Field(default_factory=now_utc)


class ExperienceRevision(BaseModel):
    id: str
    experience_id: str
    source_tree_id: str
    parent_revision_id: str | None = None
    ui_profile: UiProfile = "n4x-default"
    status: RevisionStatus = "draft"
    created_at: datetime = Field(default_factory=now_utc)
    created_by: str = "system"
    application_access: list[ApplicationAccessDeclaration] = Field(default_factory=list)

    @field_validator("application_access")
    @classmethod
    def validate_application_access(
        cls, value: list[ApplicationAccessDeclaration]
    ) -> list[ApplicationAccessDeclaration]:
        application_ids = [item.application_id for item in value]
        if len(application_ids) != len(set(application_ids)):
            raise ValueError("application_access application_ids must be unique")
        return value


class ExperienceSurface(BaseModel):
    experience_revision_id: str
    surface_id: str
    surface_type: str
    surface_type_version: int
    entrypoint: str
    source_tree_id: str
    source_paths: list[str]
    title: str = ""
    description: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)
    created_by: str = "system"

    @field_validator(
        "experience_revision_id",
        "surface_id",
        "surface_type",
        "entrypoint",
        "source_tree_id",
    )
    @classmethod
    def validate_non_empty_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Surface identifiers and source fields must not be empty")
        return value

    @field_validator("source_paths")
    @classmethod
    def validate_source_paths(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("Surface source_paths must not be empty")
        if len(value) != len(set(value)):
            raise ValueError("Surface source_paths must be unique")
        for path in value:
            _validate_relative_source_path(path)
        return value

    @model_validator(mode="after")
    def validate_surface_contract(self) -> ExperienceSurface:
        from n4x.kernel.surface_types import SURFACE_TYPES

        _validate_relative_source_path(self.entrypoint)
        if self.entrypoint not in self.source_paths:
            raise ValueError("Surface entrypoint must be included in source_paths")
        self.config = SURFACE_TYPES.validate_config(
            self.surface_type, self.surface_type_version, self.config
        )
        return self


class BlueprintSourceFileDefinition(BaseModel):
    path: str
    content: str
    role: SourceRole
    language: str


class BlueprintFrontendDependencyDefinition(BaseModel):
    id: str | None = None
    package: str
    spec: str = ""


class BlueprintSurfaceDefinition(BaseModel):
    id: str
    surface_type: str
    surface_type_version: int = 1
    entrypoint: str
    source_paths: list[str]
    title: str = ""
    description: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_surface(self) -> BlueprintSurfaceDefinition:
        ExperienceSurface(
            experience_revision_id="blueprint-validation",
            surface_id=self.id,
            surface_type=self.surface_type,
            surface_type_version=self.surface_type_version,
            entrypoint=self.entrypoint,
            source_tree_id="blueprint-validation.source",
            source_paths=self.source_paths,
            title=self.title,
            description=self.description,
            config=self.config,
        )
        return self


class ExperienceBlueprintDefinition(BaseModel):
    """Typed declarative frontend section within an AppBlueprint revision."""

    id: str
    name: str | None = None
    description: str = ""
    ui_profile: UiProfile = "n4x-default"
    application_access: list[ApplicationAccessDeclaration] = Field(default_factory=list)
    source_files: list[BlueprintSourceFileDefinition] = Field(default_factory=list)
    dependencies: list[BlueprintFrontendDependencyDefinition] = Field(
        default_factory=list
    )
    surfaces: list[BlueprintSurfaceDefinition] = Field(default_factory=list)

    @field_validator("application_access")
    @classmethod
    def validate_application_access(
        cls, value: list[ApplicationAccessDeclaration]
    ) -> list[ApplicationAccessDeclaration]:
        application_ids = [item.application_id for item in value]
        if len(application_ids) != len(set(application_ids)):
            raise ValueError("application_access application_ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_surface_ids(self) -> ExperienceBlueprintDefinition:
        surface_ids = [surface.id for surface in self.surfaces]
        if len(surface_ids) != len(set(surface_ids)):
            raise ValueError("blueprint Surface ids must be unique")
        return self


class AuthoringGuide(BaseModel):
    id: str
    title: str
    active_revision_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class AuthoringGuideRevision(BaseModel):
    id: str
    guide_id: str
    release_version: str
    status: PlatformRevisionStatus
    content: str
    content_hash: str
    references: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    license: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)
    created_by: Literal["system"] = "system"


class UiTheme(BaseModel):
    id: str
    active_revision_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class UiThemeRevision(BaseModel):
    id: str
    theme_id: str
    release_version: str
    status: PlatformRevisionStatus
    css_text: str
    content_hash: str
    provenance: dict[str, Any] = Field(default_factory=dict)
    license: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)
    created_by: Literal["system"] = "system"


class AppBlueprint(BaseModel):
    id: str
    name: str
    description: str = ""
    active_revision_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)


class BlueprintRevision(BaseModel):
    id: str
    blueprint_id: str
    status: BlueprintRevisionStatus = "draft"
    instructions: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)
    created_by: str = "system"
    content_hash: str


class SourceTree(BaseModel):
    id: str
    owner_kind: RevisionOwnerKind
    owner_id: str
    draft_or_revision_id: str
    status: SourceTreeStatus
    root_namespace: str
    tree_hash: str
    derived_from_tree_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_application_owner(cls, value: Any) -> Any:
        return _canonicalize_revision_owner(
            value,
            legacy_field="application_id",
            owner_id_field="draft_or_revision_id",
        )

    @property
    def application_id(self) -> str:
        """Legacy runtime compatibility; canonical persistence uses owner_kind/id."""
        return self.root_namespace


class SourceFile(BaseModel):
    source_tree_id: str
    path: str
    role: SourceRole
    language: str
    content: str
    content_hash: str
    size: int
    version: int = 1
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class SourceFileSummary(BaseModel):
    source_tree_id: str
    path: str
    role: SourceRole
    language: str
    content_hash: str
    size: int
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_source_file(cls, source_file: SourceFile) -> SourceFileSummary:
        return cls.model_validate(source_file.model_dump(exclude={"content"}))


class SourceChange(BaseModel):
    id: str
    source_tree_id: str
    change_group_id: str
    operation: SourceOperation
    path: str
    old_path: str | None = None
    old_hash: str | None = None
    new_hash: str | None = None
    actor: str = "system"
    tool: str = "kernel"
    timestamp: datetime = Field(default_factory=now_utc)


class ApplicationObject(BaseModel):
    id: str
    application_id: str
    data_space_id: str = "production"
    object_type_id: str
    object_type_revision_id: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class ApplicationRelation(BaseModel):
    id: str
    application_id: str
    data_space_id: str = "production"
    relation_type_id: str
    relation_type_revision_id: str | None = None
    physical_type: str | None = None
    from_object_id: str
    to_object_id: str
    values: dict[str, Any] = Field(default_factory=dict)
    created_by_invocation_id: str | None = None
    updated_by_invocation_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class ObjectType(BaseModel):
    id: str
    application_id: str
    active_revision_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)


class ObjectTypeRevision(BaseModel):
    id: str
    object_type_id: str
    application_revision_id: str
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)
    content_hash: str


class RelationType(BaseModel):
    id: str
    application_id: str
    active_revision_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)


class RelationTypeRevision(BaseModel):
    id: str
    relation_type_id: str
    application_revision_id: str
    name: str
    from_object_type_id: str
    to_object_type_id: str
    physical_type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)
    content_hash: str


class SecretReference(BaseModel):
    id: str
    application_id: str
    uri: str
    backend: Literal["macos_keychain", "encrypted_local", "memory"]
    name: str = ""
    description: str = ""
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class CredentialRecord(BaseModel):
    id: str
    application_id: str
    provider: str
    account_name: str
    secret_reference_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class CallbackRoute(BaseModel):
    id: str
    application_id: str
    target_action_revision_id: str
    state: str
    status: Literal["pending", "used", "expired", "revoked"] = "pending"
    created_at: datetime = Field(default_factory=now_utc)
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeDependency(BaseModel):
    id: str
    owner_kind: RevisionOwnerKind
    owner_id: str
    ecosystem: Literal["python", "javascript"]
    package: str
    spec: str
    created_at: datetime = Field(default_factory=now_utc)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_application_owner(cls, value: Any) -> Any:
        return _canonicalize_revision_owner(value)

    @property
    def application_revision_id(self) -> str:
        return self.owner_id


class PythonEnvironment(BaseModel):
    id: str
    application_revision_id: str
    dependency_ids: list[str] = Field(default_factory=list)
    env_path: str
    python_version: str
    lock_hash: str
    lock_metadata: dict[str, Any] = Field(default_factory=dict)
    status: EnvironmentStatus = "pending"
    last_resolved_at: datetime | None = None
    error: str | None = None


class JavaScriptEnvironment(BaseModel):
    id: str
    owner_kind: RevisionOwnerKind
    owner_id: str
    dependency_ids: list[str] = Field(default_factory=list)
    install_path: str
    node_version: str | None = None
    lock_hash: str
    lock_metadata: dict[str, Any] = Field(default_factory=dict)
    status: EnvironmentStatus = "pending"
    last_resolved_at: datetime | None = None
    error: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_application_owner(cls, value: Any) -> Any:
        return _canonicalize_revision_owner(value)

    @property
    def application_revision_id(self) -> str:
        return self.owner_id


class BuildInvocation(BaseModel):
    id: str
    owner_kind: RevisionOwnerKind
    owner_id: str
    kind: BuildKind
    status: BuildStatus
    input_hash: str
    command: list[str] = Field(default_factory=list)
    cwd: str | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    started_at: datetime = Field(default_factory=now_utc)
    completed_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_application_owner(cls, value: Any) -> Any:
        return _canonicalize_revision_owner(value)

    @property
    def application_revision_id(self) -> str:
        return self.owner_id


class BuildArtifact(BaseModel):
    id: str
    owner_kind: RevisionOwnerKind
    owner_id: str
    build_invocation_id: str
    artifact_type: Literal[
        "python_environment",
        "javascript_environment",
        "materialized_source",
        "surface_bundle",
        "log",
    ]
    path: str
    content_hash: str
    surface_id: str | None = None
    surface_type: str | None = None
    input_hash: str | None = None
    manifest: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_application_owner(cls, value: Any) -> Any:
        return _canonicalize_revision_owner(value)

    @property
    def application_revision_id(self) -> str:
        return self.owner_id

    @model_validator(mode="after")
    def validate_surface_authority(self) -> BuildArtifact:
        authority = (
            self.surface_id,
            self.surface_type,
            self.input_hash,
            self.manifest,
        )
        if any(value is not None for value in authority):
            if (
                self.owner_kind != "ExperienceRevision"
                or any(value is None for value in authority)
            ):
                raise ValueError(
                    "Surface artifact authority requires ExperienceRevision owner, "
                    "surface_id, surface_type, input_hash, and manifest"
                )
        return self


class Action(BaseModel):
    id: str
    application_id: str
    active_revision_id: str | None = None


class Trigger(BaseModel):
    id: str
    application_id: str
    active_revision_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)


class TriggerRevision(BaseModel):
    id: str
    trigger_id: str
    application_revision_id: str
    trigger_type: TriggerType
    action_revision_id: str
    config: dict[str, Any] = Field(default_factory=dict)
    input_template: dict[str, Any] = Field(default_factory=dict)
    overlap_policy: TriggerOverlapPolicy = "run_concurrently"
    misfire_policy: TriggerMisfirePolicy = "run_once"
    max_attempts: int = 3
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = Field(default_factory=now_utc)
    created_by: str = "system"
    content_hash: str


class ActionRevision(BaseModel):
    id: str
    action_id: str
    application_revision_id: str
    kind: ActionKind
    entrypoint: str
    source_tree_id: str
    source_paths: list[str]
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    runtime_dependency_ids: list[str] = Field(default_factory=list)
    secret_refs: list[str] = Field(default_factory=list)
    callback_refs: list[str] = Field(default_factory=list)
    declared_capabilities: list[str] = Field(default_factory=list)
    timeout_seconds: int = 30
    concurrency_policy: ActionConcurrencyPolicy = "default"
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    idempotency_key_policy: str | None = None
    migration_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)
    created_by: str = "system"
    content_hash: str


class Invocation(BaseModel):
    id: str
    action_revision_id: str
    data_space_id: str = "production"
    deployment_id: str | None = None
    correlation_id: str | None = None
    invocation_kind: InvocationKind
    status: InvocationStatus
    input: dict[str, Any]
    output: Any = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    queued_at: datetime = Field(default_factory=now_utc)
    started_at: datetime | None = Field(default_factory=now_utc)
    completed_at: datetime | None = None
    last_heartbeat_at: datetime | None = None


class JobRecord(BaseModel):
    id: str
    application_id: str
    trigger_revision_id: str
    action_revision_id: str
    status: JobStatus
    input: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: datetime | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    attempt: int = 1
    max_attempts: int = 3
    idempotency_key: str
    invocation_id: str | None = None
    error: str | None = None
    next_retry_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    current_attempt_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobAttempt(BaseModel):
    id: str
    job_id: str
    attempt: int
    status: JobAttemptStatus
    lease_owner: str
    lease_expires_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    invocation_id: str | None = None
    error: str | None = None


class CypherAuditRecord(BaseModel):
    id: str
    invocation_id: str
    action_revision_id: str
    application_id: str
    data_space_id: str = "production"
    deployment_id: str | None = None
    correlation_id: str | None = None
    actor: str
    mode: CypherAccessMode
    query: str
    query_hash: str
    params_hash: str
    checkpoint_id: str | None = None
    row_count: int = 0
    created_at: datetime = Field(default_factory=now_utc)


class TestCase(BaseModel):
    id: str
    application_revision_id: str
    action_revision_id: str
    input: dict[str, Any]
    expected_output: Any


class GraphCheckpoint(BaseModel):
    id: str
    level: CheckpointLevel
    application_id: str
    application_revision_id: str | None = None
    snapshot_id: str
    created_at: datetime = Field(default_factory=now_utc)
    reason: str


class CheckpointSnapshot(BaseModel):
    id: str
    checkpoint_id: str
    level: CheckpointLevel
    format_version: int = 1
    content_hash: str
    blob_id: str
    object_count: int = 0
    relation_count: int = 0
    created_at: datetime = Field(default_factory=now_utc)


class CheckpointBlob(BaseModel):
    id: str
    checkpoint_id: str
    snapshot_id: str
    encoding: Literal["base64_json"] = "base64_json"
    payload: str
    created_at: datetime = Field(default_factory=now_utc)


class ValidationReport(BaseModel):
    id: str
    application_revision_id: str
    status: Literal["passed", "failed"]
    errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)


class ExperienceValidationReport(BaseModel):
    id: str
    experience_revision_id: str
    status: Literal["passed", "failed"]
    errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)


class PackageImportAttempt(BaseModel):
    """Durable orchestration state for one verified Package archive."""

    id: str
    package_hash: str
    status: PackageImportStatus = "running"
    phase: str = "created"
    root_kind: Literal["application", "experience"]
    root_id: str
    include_data: bool = False
    restore_data: bool = False
    source_ids: dict[str, Any] = Field(default_factory=dict)
    id_map: dict[str, dict[str, str]] = Field(default_factory=dict)
    created_ids: dict[str, list[str]] = Field(default_factory=dict)
    completed_members: list[str] = Field(default_factory=list)
    data_counts: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime = Field(default_factory=now_utc)
    completed_at: datetime | None = None


def _canonicalize_legacy_field(value: Any, *, canonical: str, legacy: str) -> Any:
    if not isinstance(value, dict):
        return value
    values = dict(value)
    legacy_value = values.pop(legacy, None)
    if canonical not in values and legacy_value is not None:
        values[canonical] = legacy_value
    elif legacy_value is not None and values.get(canonical) != legacy_value:
        raise ValueError(f"{canonical} conflicts with legacy {legacy}")
    return values


def _canonicalize_revision_owner(
    value: Any,
    *,
    legacy_field: str = "application_revision_id",
    owner_id_field: str | None = None,
) -> Any:
    if not isinstance(value, dict):
        return value
    values = dict(value)
    legacy_value = values.pop(legacy_field, None)
    inferred_owner_id = (
        values.get(owner_id_field) if owner_id_field is not None else legacy_value
    )
    if "owner_kind" not in values and inferred_owner_id is not None:
        values["owner_kind"] = "ApplicationRevision"
    if "owner_id" not in values and inferred_owner_id is not None:
        values["owner_id"] = inferred_owner_id
    if (
        legacy_value is not None
        and owner_id_field is None
        and values.get("owner_kind") == "ApplicationRevision"
        and values.get("owner_id") != legacy_value
    ):
        raise ValueError(f"owner_id conflicts with legacy {legacy_field}")
    return values


def _validate_relative_source_path(path: str) -> None:
    if (
        not path
        or path.startswith(("/", "\\"))
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError("Surface source paths must be traversal-safe relative paths")
