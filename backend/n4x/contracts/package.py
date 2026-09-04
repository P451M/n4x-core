from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from n4x.kernel.hash import sha256_json


PACKAGE_FORMAT_VERSION = "n4x.package.v2"
PACKAGE_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
PACKAGE_MAX_MEMBER_BYTES = 192 * 1024 * 1024
PACKAGE_MAX_MEMBERS = 8


class PackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackageEntry(PackageModel):
    path: str
    size: int
    content_hash: str


class PackageManifest(PackageModel):
    format_version: Literal["n4x.package.v2"] = PACKAGE_FORMAT_VERSION
    schema_fingerprint: str
    graph_metamodel_version: str
    graph_schema_fingerprint: str
    action_context_version: str
    subprocess_version: str
    experience_bridge_version: str
    root_kind: Literal["application", "experience"]
    root_id: str
    include_data: bool = False
    omitted_bindings: list[dict[str, Any]] = Field(default_factory=list)
    entries: list[PackageEntry] = Field(default_factory=list)
    package_hash: str = ""


class PackageApplication(PackageModel):
    application: dict[str, Any]
    revision: dict[str, Any]
    source_files: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[dict[str, Any]] = Field(default_factory=list)
    object_types: list[dict[str, Any]] = Field(default_factory=list)
    relation_types: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    tests: list[dict[str, Any]] = Field(default_factory=list)
    secret_requirements: list[dict[str, Any]] = Field(default_factory=list)
    objects: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)


class PackageExperience(PackageModel):
    experience: dict[str, Any]
    revision: dict[str, Any]
    source_files: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[dict[str, Any]] = Field(default_factory=list)
    surfaces: list[dict[str, Any]] = Field(default_factory=list)


class PackagePayload(PackageModel):
    applications: list[PackageApplication]
    experience: PackageExperience | None = None


PACKAGE_SCHEMA_FINGERPRINT = sha256_json(
    {
        "manifest": PackageManifest.model_json_schema(),
        "payload": PackagePayload.model_json_schema(),
    }
)


PACKAGE_CONTRACT_SCHEMA = {
    "version": PACKAGE_FORMAT_VERSION,
    "schema_fingerprint": PACKAGE_SCHEMA_FINGERPRINT,
    "roots": ["application", "experience"],
    "active_working_sets_only": True,
    "optional_data": True,
    "secret_values": False,
    "experience_members": ["source_files", "dependencies", "surfaces"],
    "rejects": ["n4x.package.v1"],
    "collision_policy": "reject_unrelated_resume_same_hash",
}
