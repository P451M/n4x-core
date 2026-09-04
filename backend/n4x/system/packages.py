"""Application and Experience packages owned by the System."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from n4x.contracts import (
    ACTION_CONTEXT_VERSION,
    EXPERIENCE_BRIDGE_VERSION,
    GRAPH_METAMODEL_SCHEMA_FINGERPRINT,
    GRAPH_METAMODEL_VERSION,
    SUBPROCESS_PROTOCOL_VERSION,
)
from n4x.contracts.package import (
    PACKAGE_FORMAT_VERSION,
    PACKAGE_MAX_ARCHIVE_BYTES,
    PACKAGE_MAX_MEMBER_BYTES,
    PACKAGE_MAX_MEMBERS,
    PACKAGE_SCHEMA_FINGERPRINT,
    PackageApplication,
    PackageEntry,
    PackageExperience,
    PackageManifest,
    PackagePayload,
)
from n4x.graph.store import node_ref, relation_physical_type
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.hash import sha256_text, stable_json
from n4x.kernel.models import (
    Action,
    ApplicationObject,
    ApplicationRelation,
    Experience,
    ObjectType,
    PackageImportAttempt,
    RelationType,
    SecretReference,
    Trigger,
    now_utc,
)


_ARCHIVE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.n4xp$")
_ALLOWED_ENTRIES = {"manifest.json", "payload.json"}
V4_GRAPH_METAMODEL_VERSION = "n4x.graph.metamodel.v4"
V4_GRAPH_SCHEMA_FINGERPRINT = (
    "sha256:e939e862b9dd9d88d1a17b8fe699107c"
    "f5230b2ebebab905624df2d931d8b5d0"
)


class PackageService:
    """Captures, archives, and installs active N4X working sets."""

    def __init__(
        self,
        uow: GraphUnitOfWork,
        *,
        runtime_root: Path,
        source,
        secrets,
        applications,
        schema,
        definitions,
        invocations,
        experiences,
        experience_surfaces,
        activation,
        experience_activation,
        scheduler,
    ) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.runtime_root = runtime_root
        self.package_root = runtime_root / "packages"
        self.source = source
        self.secrets = secrets
        self.applications = applications
        self.schema = schema
        self.definitions = definitions
        self.invocations = invocations
        self.experiences = experiences
        self.experience_surfaces = experience_surfaces
        self.activation = activation
        self.experience_activation = experience_activation
        self.scheduler = scheduler

    def preview(
        self,
        root_kind: str,
        root_id: str,
        *,
        include_data: bool = False,
    ) -> dict[str, Any]:
        payload, omitted = self._capture(root_kind, root_id, include_data)
        return {
            "format_version": PACKAGE_FORMAT_VERSION,
            "schema_fingerprint": PACKAGE_SCHEMA_FINGERPRINT,
            "root_kind": root_kind,
            "root_id": root_id,
            "include_data": include_data,
            "application_ids": [
                item.application["id"] for item in payload.applications
            ],
            "experience_id": (
                None
                if payload.experience is None
                else payload.experience.experience["id"]
            ),
            "object_count": sum(len(item.objects) for item in payload.applications),
            "relation_count": sum(len(item.relations) for item in payload.applications),
            "omitted_bindings": omitted,
        }

    def export(
        self,
        root_kind: str,
        root_id: str,
        archive_name: str,
        *,
        include_data: bool = False,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        path = self._archive_path(archive_name)
        payload, omitted = self._capture(root_kind, root_id, include_data)
        payload_bytes = stable_json(payload.model_dump(mode="json")).encode("utf-8")
        payload_entry = PackageEntry(
            path="payload.json",
            size=len(payload_bytes),
            content_hash=sha256_text(payload_bytes.decode("utf-8")),
        )
        manifest = PackageManifest(
            schema_fingerprint=PACKAGE_SCHEMA_FINGERPRINT,
            graph_metamodel_version=GRAPH_METAMODEL_VERSION,
            graph_schema_fingerprint=GRAPH_METAMODEL_SCHEMA_FINGERPRINT,
            action_context_version=ACTION_CONTEXT_VERSION,
            subprocess_version=SUBPROCESS_PROTOCOL_VERSION,
            experience_bridge_version=EXPERIENCE_BRIDGE_VERSION,
            root_kind=root_kind,  # type: ignore[arg-type]
            root_id=root_id,
            include_data=include_data,
            omitted_bindings=omitted,
            entries=[payload_entry],
        )
        package_hash = self._package_hash(manifest, payload_bytes)
        manifest = manifest.model_copy(update={"package_hash": package_hash})
        manifest_bytes = stable_json(manifest.model_dump(mode="json")).encode("utf-8")

        if path.exists() and not overwrite:
            raise FileExistsError(path)
        self.uow.require_inactive("write Package archive")
        self.package_root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(
                temporary,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                self._write_entry(archive, "manifest.json", manifest_bytes)
                self._write_entry(archive, "payload.json", payload_bytes)
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {
            "format_version": PACKAGE_FORMAT_VERSION,
            "schema_fingerprint": PACKAGE_SCHEMA_FINGERPRINT,
            "root_kind": root_kind,
            "root_id": root_id,
            "include_data": include_data,
            "application_ids": [
                item.application["id"] for item in payload.applications
            ],
            "experience_id": (
                None
                if payload.experience is None
                else payload.experience.experience["id"]
            ),
            "object_count": sum(len(item.objects) for item in payload.applications),
            "relation_count": sum(len(item.relations) for item in payload.applications),
            "omitted_bindings": omitted,
            "archive_name": archive_name,
            "path": str(path),
            "package_hash": package_hash,
        }

    def delete_working_set(
        self,
        root_kind: str,
        root_id: str,
        archive_name: str,
        *,
        confirmation_root_id: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        if confirmation_root_id != root_id:
            raise ValidationFailure(
                f"confirmation_root_id does not match root_id: {root_id}"
            )
        if root_kind == "application":
            self._refuse_if_experience_references(root_id)
        elif root_kind == "experience":
            self._refuse_if_shared_applications(root_id)
        exported = self.export(
            root_kind,
            root_id,
            archive_name,
            include_data=True,
            overwrite=overwrite,
        )
        application_ids = list(exported["application_ids"])
        experience_id = exported["experience_id"]
        self._delete_owned_secret_values(application_ids)
        with self.uow:
            self._delete_pinning_deployments(application_ids, experience_id)
            for application_id in application_ids:
                self._delete_application_subgraph(application_id)
            if experience_id is not None:
                self._delete_experience_subgraph(experience_id)
        self.scheduler.remount()
        return {
            **exported,
            "deleted": {
                "application_ids": application_ids,
                "experience_id": experience_id,
            },
        }

    def stage(
        self,
        archive_name: str,
        archive_bytes: bytes,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        path = self._archive_path(archive_name)
        if len(archive_bytes) > PACKAGE_MAX_ARCHIVE_BYTES:
            raise ValidationFailure("package_corrupt: archive is too large")
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        self.uow.require_inactive("write Package archive")
        self.package_root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(archive_bytes)
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {
            "archive_name": archive_name,
            "path": str(path),
            "size": len(archive_bytes),
            "content_sha256": "sha256:" + hashlib.sha256(archive_bytes).hexdigest(),
        }

    def stage_from_path(
        self,
        archive_name: str,
        source: Path,
        *,
        overwrite: bool = False,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        path = self._archive_path(archive_name)
        if not source.is_file():
            raise FileNotFoundError(source)
        size = source.stat().st_size
        if size > PACKAGE_MAX_ARCHIVE_BYTES:
            raise ValidationFailure("package_corrupt: archive is too large")
        content_sha256 = self.file_sha256(source)
        if expected_sha256 is not None and self.normalize_sha256(
            expected_sha256
        ) != content_sha256:
            raise ValidationFailure("package_corrupt: content hash mismatch")
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        self.uow.require_inactive("write Package archive")
        self.package_root.mkdir(parents=True, exist_ok=True)
        resolved_source = source.resolve()
        if resolved_source == path.resolve():
            return {
                "archive_name": archive_name,
                "path": str(path),
                "size": size,
                "content_sha256": content_sha256,
            }
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            if resolved_source.parent == self.package_root.resolve():
                resolved_source.replace(temporary)
            else:
                with resolved_source.open("rb") as incoming, temporary.open(
                    "wb"
                ) as outgoing:
                    while True:
                        chunk = incoming.read(1024 * 1024)
                        if not chunk:
                            break
                        outgoing.write(chunk)
            if path.exists() and overwrite:
                path.unlink()
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {
            "archive_name": archive_name,
            "path": str(path),
            "size": size,
            "content_sha256": content_sha256,
        }

    def list(self) -> list[dict[str, Any]]:
        if not self.package_root.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for candidate in sorted(self.package_root.iterdir()):
            if not candidate.is_file() or not _ARCHIVE_NAME.fullmatch(candidate.name):
                continue
            items.append(
                {
                    "archive_name": candidate.name,
                    "size": candidate.stat().st_size,
                    "content_sha256": self.file_sha256(candidate),
                }
            )
        return items

    def inspect(self, archive_name: str) -> dict[str, Any]:
        manifest, payload = self._read_archive(archive_name)
        collisions = self._collisions(payload)
        disagreements = self._disagreements(manifest)
        return {
            "manifest": manifest.model_dump(mode="json"),
            "compatible": not disagreements,
            "materializable": self._materializable(manifest),
            "disagreements": disagreements,
            "collisions": collisions,
            "application_ids": [
                item.application["id"] for item in payload.applications
            ],
            "experience_id": (
                None
                if payload.experience is None
                else payload.experience.experience["id"]
            ),
            "object_count": sum(len(item.objects) for item in payload.applications),
            "relation_count": sum(len(item.relations) for item in payload.applications),
        }

    def import_package(
        self,
        archive_name: str,
        *,
        dry_run: bool = False,
        restore_data: bool | None = None,
        allow_incompatible: bool = False,
    ) -> dict[str, Any]:
        manifest, payload = self._read_archive(archive_name)
        disagreements = self._disagreements(manifest)
        compatible = not disagreements
        materializable = self._materializable(manifest)
        if not compatible and not (
            dry_run or (allow_incompatible and materializable)
        ):
            raise ValidationFailure("package_incompatible")
        restore = manifest.include_data if restore_data is None else restore_data
        if not compatible:
            restore = False
        if restore and not manifest.include_data:
            raise ValidationFailure("Package contains no data")

        existing_attempt = self._attempt_by_hash(manifest.package_hash)
        collisions = self._collisions(
            payload,
            owned_ids=(
                {} if existing_attempt is None else existing_attempt.created_ids
            ),
        )
        if dry_run:
            return {
                "dry_run": True,
                "package_hash": manifest.package_hash,
                "compatible": compatible,
                "materializable": materializable,
                "disagreements": disagreements,
                "collisions": collisions,
                "resumable_attempt_id": (
                    None if existing_attempt is None else existing_attempt.id
                ),
            }
        if collisions:
            raise ValidationFailure(
                "package_conflict: "
                + ", ".join(f"{item['kind']}:{item['id']}" for item in collisions)
            )

        attempt = self._materialize_or_resume(
            manifest, payload, existing_attempt, restore
        )
        try:
            if compatible:
                attempt = self._restore_data(attempt, payload, restore)
                attempt = self._activate_applications(attempt, payload)
                attempt = self._activate_experience(attempt, payload)
                phase = "completed"
            else:
                attempt = self._disable_imported_working_set(attempt, payload)
                phase = "completed_disabled"
            with self.uow:
                current = self.records.package_import_attempts[attempt.id]
                attempt = current.model_copy(
                    update={
                        "status": "succeeded",
                        "phase": phase,
                        "error": None,
                        "completed_at": now_utc(),
                    }
                )
                self.records.package_import_attempts.save(attempt)
            if compatible:
                self.scheduler.remount()
            return {
                **self._attempt_result(attempt, manifest),
                "compatible": compatible,
                "activated": compatible,
            }
        except Exception as exc:
            with self.uow:
                current = self.records.package_import_attempts[attempt.id]
                self.records.package_import_attempts.save(
                    current.model_copy(
                        update={
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                            "completed_at": now_utc(),
                        }
                    )
                )
            raise ValidationFailure(
                f"package_import_failed: {type(exc).__name__}: {exc}"
            ) from exc

    def inspect_import(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.records.package_import_attempts[attempt_id]
        return attempt.model_dump(mode="json")

    def _capture(
        self, root_kind: str, root_id: str, include_data: bool
    ) -> tuple[PackagePayload, list[dict[str, Any]]]:
        if root_kind not in {"application", "experience"}:
            raise ValueError("root_kind must be application or experience")
        omitted: list[dict[str, Any]] = []
        with self.uow:
            experience_payload = None
            experience_secret_ids: dict[str, set[str]] = {}
            if root_kind == "experience":
                experience = self.records.experiences.get(root_id)
                if experience is None or experience.active_revision_id is None:
                    raise ValidationFailure("Experience root is not active")
                revision = self.records.experience_revisions[
                    experience.active_revision_id
                ]
                if revision.status != "active":
                    raise ValidationFailure("Experience root revision is not active")
                app_ids = [item.application_id for item in revision.application_access]
                experience_secret_ids = {
                    item.application_id: set(item.secret_reference_ids or [])
                    for item in revision.application_access
                }
                experience_payload = self._capture_experience(experience, revision)
            else:
                app_ids = [root_id]
            applications = [
                self._capture_application(
                    app_id,
                    include_data,
                    omitted,
                    experience_secret_ids.get(app_id, set()),
                )
                for app_id in app_ids
            ]
            if experience_payload is not None:
                self._validate_experience_access(experience_payload, applications)
            return (
                PackagePayload(
                    applications=applications,
                    experience=experience_payload,
                ),
                omitted,
            )

    def _capture_application(
        self,
        application_id: str,
        include_data: bool,
        omitted: list[dict[str, Any]],
        required_secret_ids: set[str] | None = None,
    ) -> PackageApplication:
        application = self.records.applications.get(application_id)
        if application is None or application.active_revision_id is None:
            raise ValidationFailure(f"Application is not active: {application_id}")
        revision = self.records.revisions[application.active_revision_id]
        if revision.status != "active":
            raise ValidationFailure(
                f"Application revision is not active: {revision.id}"
            )
        actions = sorted(
            (
                item
                for item in self.records.action_revisions.values()
                if item.application_revision_id == revision.id
            ),
            key=lambda item: (
                item.action_id,
                item.id
                == self.records.actions[item.action_id].active_revision_id,
            ),
        )
        secret_ids = {
            secret_id for item in actions for secret_id in item.secret_refs
        } | (required_secret_ids or set())
        secrets = []
        for secret_id in sorted(secret_ids):
            secret = self.records.secret_references.get(secret_id)
            if secret is None or secret.application_id != application_id:
                raise ValidationFailure(f"invalid secret reference: {secret_id}")
            secrets.append(
                {
                    "source_id": secret.id,
                    "uri": secret.uri,
                    "name": secret.name,
                    "description": secret.description,
                }
            )
        action_values = []
        for action in actions:
            values = action.model_dump(mode="json")
            if values.get("callback_refs"):
                omitted.append(
                    {
                        "kind": "callback_routes",
                        "application_id": application_id,
                        "action_id": action.action_id,
                    }
                )
            values["callback_refs"] = []
            action_values.append(
                {
                    "stable": self.records.actions[action.action_id].model_dump(
                        mode="json"
                    ),
                    "revision": values,
                }
            )
        if any(
            item.application_id == application_id
            for item in self.records.credential_records.values()
        ):
            omitted.append({"kind": "credentials", "application_id": application_id})
        captured = PackageApplication(
            application=application.model_dump(mode="json"),
            revision=revision.model_dump(mode="json"),
            source_files=[
                item.model_dump(mode="json")
                for item in sorted(
                    self.source.list_source_tree(revision.source_tree_id),
                    key=lambda item: item.path,
                )
            ],
            dependencies=[
                item.model_dump(mode="json")
                for item in sorted(
                    (
                        item
                        for item in self.records.runtime_dependencies.values()
                        if item.owner_kind == "ApplicationRevision"
                        and item.owner_id == revision.id
                    ),
                    key=lambda item: (item.package, item.spec, item.id),
                )
            ],
            object_types=self._definition_pairs(
                "object_types", "object_type_revisions", revision.id, "object_type_id"
            ),
            relation_types=self._definition_pairs(
                "relation_types",
                "relation_type_revisions",
                revision.id,
                "relation_type_id",
            ),
            actions=action_values,
            triggers=self._definition_pairs(
                "triggers", "trigger_revisions", revision.id, "trigger_id"
            ),
            tests=[
                item.model_dump(mode="json")
                for item in sorted(
                    (
                        item
                        for item in self.records.test_cases.values()
                        if item.application_revision_id == revision.id
                    ),
                    key=lambda item: item.id,
                )
            ],
            secret_requirements=secrets,
            objects=(
                [
                    item.model_dump(mode="json")
                    for item in self.uow.objects.list(application_id)
                ]
                if include_data
                else []
            ),
            relations=(
                [
                    item.model_dump(mode="json")
                    for item in self.uow.relations.list(application_id)
                ]
                if include_data
                else []
            ),
        )
        if include_data:
            self._include_referenced_schema(captured)
        self._validate_application_data(captured)
        return captured

    def _capture_experience(self, experience, revision) -> PackageExperience:
        return PackageExperience(
            experience=experience.model_dump(mode="json"),
            revision=revision.model_dump(mode="json"),
            source_files=[
                item.model_dump(mode="json")
                for item in sorted(
                    self.source.list_source_tree(revision.source_tree_id),
                    key=lambda item: item.path,
                )
            ],
            dependencies=[
                item.model_dump(mode="json")
                for item in sorted(
                    (
                        item
                        for item in self.records.runtime_dependencies.values()
                        if item.owner_kind == "ExperienceRevision"
                        and item.owner_id == revision.id
                    ),
                    key=lambda item: (item.package, item.spec, item.id),
                )
            ],
            surfaces=[
                item.model_dump(mode="json")
                for item in sorted(
                    (
                        item
                        for item in self.records.experience_surfaces.values()
                        if item.experience_revision_id == revision.id
                    ),
                    key=lambda item: item.surface_id,
                )
            ],
        )

    def _definition_pairs(
        self,
        stable_collection: str,
        revision_collection: str,
        owner_revision_id: str,
        stable_id_field: str,
        *,
        revision_field: str = "application_revision_id",
    ) -> list[dict[str, Any]]:
        stables = getattr(self.records, stable_collection)
        revisions = getattr(self.records, revision_collection)
        return [
            {
                "stable": stables[getattr(item, stable_id_field)].model_dump(
                    mode="json"
                ),
                "revision": item.model_dump(mode="json"),
            }
            for item in sorted(
                (
                    item
                    for item in revisions.values()
                    if getattr(item, revision_field) == owner_revision_id
                ),
                key=lambda item: (
                    getattr(item, stable_id_field),
                    item.id
                    == stables[
                        getattr(item, stable_id_field)
                    ].active_revision_id,
                ),
            )
        ]

    def _validate_experience_access(
        self,
        experience: PackageExperience,
        applications: list[PackageApplication],
    ) -> None:
        app_by_id = {item.application["id"]: item for item in applications}
        for access in experience.revision["application_access"]:
            app = app_by_id.get(access["application_id"])
            if app is None:
                raise ValidationFailure("declared Application is unavailable")
            available = {
                "object_type_ids": {item["stable"]["id"] for item in app.object_types},
                "relation_type_ids": {
                    item["stable"]["id"] for item in app.relation_types
                },
                "action_ids": {item["stable"]["id"] for item in app.actions},
                "secret_reference_ids": {
                    item["source_id"] for item in app.secret_requirements
                },
            }
            for key, ids in available.items():
                declared = access.get(key)
                if declared is not None and not set(declared) <= ids:
                    raise ValidationFailure(
                        f"Experience access references unavailable {key}"
                    )

    def _include_referenced_schema(self, package: PackageApplication) -> None:
        application_id = package.application["id"]
        self._append_referenced_type_revisions(
            package.object_types,
            (item.get("object_type_revision_id") for item in package.objects),
            self.records.object_types,
            self.records.object_type_revisions,
            "object_type_id",
            application_id,
            "object data references schema outside the active Package closure",
        )
        self._append_referenced_type_revisions(
            package.relation_types,
            (item.get("relation_type_revision_id") for item in package.relations),
            self.records.relation_types,
            self.records.relation_type_revisions,
            "relation_type_id",
            application_id,
            "relation data references schema outside the active Package closure",
        )

    def _append_referenced_type_revisions(
        self,
        pairs: list[dict[str, Any]],
        referenced_ids: Iterable[str | None],
        stables,
        revisions,
        stable_id_field: str,
        application_id: str,
        error: str,
    ) -> None:
        have = {item["revision"]["id"] for item in pairs}
        for revision_id in referenced_ids:
            if revision_id is None or revision_id in have:
                continue
            revision = revisions.get(revision_id)
            stable = (
                None
                if revision is None
                else stables.get(getattr(revision, stable_id_field))
            )
            if (
                revision is None
                or stable is None
                or stable.application_id != application_id
            ):
                raise ValidationFailure(error)
            pairs.append(
                {
                    "stable": stable.model_dump(mode="json"),
                    "revision": revision.model_dump(mode="json"),
                }
            )
            have.add(revision_id)

    @staticmethod
    def _validate_application_data(package: PackageApplication) -> None:
        object_revision_ids = {item["revision"]["id"] for item in package.object_types}
        relation_revision_ids = {
            item["revision"]["id"] for item in package.relation_types
        }
        object_ids = {item["id"] for item in package.objects}
        for item in package.objects:
            revision_id = item.get("object_type_revision_id")
            if revision_id is not None and revision_id not in object_revision_ids:
                raise ValidationFailure(
                    "object data references schema outside the active Package closure"
                )
        for item in package.relations:
            revision_id = item.get("relation_type_revision_id")
            if revision_id is not None and revision_id not in relation_revision_ids:
                raise ValidationFailure(
                    "relation data references schema outside the active Package closure"
                )
            if (
                item["from_object_id"] not in object_ids
                or item["to_object_id"] not in object_ids
            ):
                raise ValidationFailure(
                    "relation data references an object outside the Package"
                )

    def _materialize_or_resume(
        self,
        manifest: PackageManifest,
        payload: PackagePayload,
        existing: PackageImportAttempt | None,
        restore_data: bool,
    ) -> PackageImportAttempt:
        if existing is not None and existing.phase != "created":
            with self.uow:
                resumed = existing.model_copy(
                    update={
                        "status": "running",
                        "error": None,
                        "completed_at": None,
                    }
                )
                self.records.package_import_attempts.save(resumed)
                return resumed

        with self.uow:
            self.store.acquire_write_lock(node_ref("N4XRoot", id="n4x"))
            conflicts = self._collisions(
                payload,
                owned_ids=({} if existing is None else existing.created_ids),
            )
            if conflicts:
                raise ValidationFailure(
                    "package_conflict: "
                    + ", ".join(f"{item['kind']}:{item['id']}" for item in conflicts)
                )
            attempt = existing or PackageImportAttempt(
                id=str(uuid.uuid4()),
                package_hash=manifest.package_hash,
                root_kind=manifest.root_kind,
                root_id=manifest.root_id,
                include_data=manifest.include_data,
                restore_data=restore_data,
                source_ids={},
            )
            if existing is None:
                self.store.create_node(
                    "PackageImportAttempt", {"id": attempt.id}, attempt
                )
                self.store.create_edge(
                    node_ref("N4XRoot", id="n4x"),
                    "HAS_PACKAGE_IMPORT_ATTEMPT",
                    node_ref("PackageImportAttempt", id=attempt.id),
                )
            id_map = {key: dict(value) for key, value in attempt.id_map.items()}
            created_ids = {
                key: list(value) for key, value in attempt.created_ids.items()
            }
            for app in payload.applications:
                self._materialize_application(app, id_map, created_ids)
            if payload.experience is not None:
                self._materialize_experience(payload.experience, id_map, created_ids)
            attempt = attempt.model_copy(
                update={
                    "phase": "materialized",
                    "id_map": id_map,
                    "created_ids": created_ids,
                    "source_ids": {
                        "applications": [
                            item.revision["id"] for item in payload.applications
                        ],
                        "experience": (
                            None
                            if payload.experience is None
                            else payload.experience.revision["id"]
                        ),
                    },
                }
            )
            self.records.package_import_attempts.save(attempt)
            return attempt

    def _materialize_application(
        self,
        package: PackageApplication,
        id_map: dict[str, dict[str, str]],
        created_ids: dict[str, list[str]],
    ) -> None:
        app_values = dict(package.application)
        app_id = app_values["id"]
        application = self.applications.create(
            app_id,
            app_values.get("name", app_id),
            app_values.get("description", ""),
        )
        application = self.applications.set_status(
            app_id,
            "importing",
            expected_status=application.status,
        )
        self._created(created_ids, "Application", app_id)
        revision = self.applications.create_revision(
            app_id,
            created_by=f"package:{package.revision['id']}",
            ui_profile=package.revision.get("ui_profile"),
        )
        self._mapped(id_map, "ApplicationRevision", package.revision["id"], revision.id)
        self._mapped(
            id_map,
            "SourceTree",
            package.revision["source_tree_id"],
            revision.source_tree_id,
        )
        for values in package.source_files:
            self.source.write_source_file(
                revision.source_tree_id,
                values["path"],
                values["content"],
                role=values["role"],
                language=values["language"],
                actor=f"package:{package.revision['id']}",
            )
        for values in package.dependencies:
            dependency = self.applications.create_runtime_dependency(
                revision.id, "python", values["package"], values["spec"]
            )
            self._mapped(id_map, "RuntimeDependency", values["id"], dependency.id)
        for values in package.secret_requirements:
            secret = SecretReference(
                id=str(uuid.uuid4()),
                application_id=app_id,
                uri=values["uri"],
                backend=self.secrets.backend.name,
                name=values.get("name", ""),
                description=values.get("description", ""),
            )
            self.store.create_node("SecretReference", {"id": secret.id}, secret)
            self.store.create_edge(
                node_ref("Application", id=app_id),
                "HAS_SECRET_REFERENCE",
                node_ref("SecretReference", id=secret.id),
            )
            self._mapped(id_map, "SecretReference", values["source_id"], secret.id)
            self._created(created_ids, "SecretReference", secret.id)
            self._created(created_ids, "SecretReferenceUri", secret.uri)
        for pair in package.object_types:
            stable_id = pair["stable"]["id"]
            if stable_id not in created_ids.get("ObjectType", []):
                stable = ObjectType(id=stable_id, application_id=app_id)
                self.store.create_node("ObjectType", {"id": stable_id}, stable)
                self._created(created_ids, "ObjectType", stable_id)
            source_revision = pair["revision"]
            created = self.schema.create_object_type(
                revision.id,
                stable_id,
                name=source_revision["name"],
                properties=source_revision.get("properties", {}),
                required=source_revision.get("required", []),
            )
            self._mapped(
                id_map, "ObjectTypeRevision", source_revision["id"], created.id
            )
        for pair in package.relation_types:
            stable_id = pair["stable"]["id"]
            if stable_id not in created_ids.get("RelationType", []):
                stable = RelationType(id=stable_id, application_id=app_id)
                self.store.create_node("RelationType", {"id": stable_id}, stable)
                self._created(created_ids, "RelationType", stable_id)
            source_revision = pair["revision"]
            created = self.schema.create_relation_type(
                revision.id,
                stable_id,
                name=source_revision["name"],
                from_object_type_id=source_revision["from_object_type_id"],
                to_object_type_id=source_revision["to_object_type_id"],
                properties=source_revision.get("properties", {}),
            )
            self._mapped(
                id_map, "RelationTypeRevision", source_revision["id"], created.id
            )
        for pair in package.actions:
            stable_id = pair["stable"]["id"]
            if stable_id not in created_ids.get("Action", []):
                stable = Action(id=stable_id, application_id=app_id)
                self.store.create_node("Action", {"id": stable_id}, stable)
                self._created(created_ids, "Action", stable_id)
            source_revision = pair["revision"]
            created = self.definitions.create_action(
                revision.id,
                stable_id,
                kind=source_revision["kind"],
                entrypoint=source_revision["entrypoint"],
                source_paths=source_revision["source_paths"],
                input_schema=source_revision.get("input_schema", {}),
                output_schema=source_revision.get("output_schema", {}),
                dependency_ids=[
                    id_map["RuntimeDependency"][item]
                    for item in source_revision.get("runtime_dependency_ids", [])
                ],
                secret_ref_ids=[
                    id_map["SecretReference"][item]
                    for item in source_revision.get("secret_refs", [])
                ],
                migration_metadata=source_revision.get("migration_metadata", {}),
                timeout_seconds=source_revision.get("timeout_seconds", 30),
                declared_capabilities=source_revision.get("declared_capabilities", []),
                concurrency_policy=source_revision.get("concurrency_policy", "default"),
                retry_policy=source_revision.get("retry_policy", {}),
                idempotency_key_policy=source_revision.get("idempotency_key_policy"),
                created_by=f"package:{source_revision['id']}",
            )
            self._mapped(id_map, "ActionRevision", source_revision["id"], created.id)
        for pair in package.triggers:
            stable_id = pair["stable"]["id"]
            if stable_id not in created_ids.get("Trigger", []):
                stable = Trigger(id=stable_id, application_id=app_id)
                self.store.create_node("Trigger", {"id": stable_id}, stable)
                self._created(created_ids, "Trigger", stable_id)
            source_revision = pair["revision"]
            created = self.definitions.create_trigger(
                revision.id,
                stable_id,
                trigger_type=source_revision["trigger_type"],
                action_revision_id=id_map["ActionRevision"][
                    source_revision["action_revision_id"]
                ],
                config=source_revision.get("config", {}),
                input_template=source_revision.get("input_template", {}),
                overlap_policy=source_revision.get("overlap_policy"),
                misfire_policy=source_revision.get("misfire_policy", "run_once"),
                max_attempts=source_revision.get("max_attempts", 3),
                retry_policy=source_revision.get("retry_policy", {}),
                enabled=source_revision.get("enabled", True),
            )
            self._mapped(id_map, "TriggerRevision", source_revision["id"], created.id)
        for values in package.tests:
            created = self.invocations.create_test_case(
                revision.id,
                id_map["ActionRevision"][values["action_revision_id"]],
                values["input"],
                values["expected_output"],
            )
            self._mapped(id_map, "TestCase", values["id"], created.id)

    def _materialize_experience(
        self,
        package: PackageExperience,
        id_map: dict[str, dict[str, str]],
        created_ids: dict[str, list[str]],
    ) -> None:
        values = dict(package.experience)
        experience_id = values["id"]
        values.update(
            {"active_revision_id": None, "status": "active", "created_at": now_utc()}
        )
        experience = Experience.model_validate(values)
        self.store.create_node("Experience", {"id": experience_id}, experience)
        self.store.create_edge(
            node_ref("N4XRoot", id="n4x"),
            "HAS_EXPERIENCE",
            node_ref("Experience", id=experience_id),
        )
        self._created(created_ids, "Experience", experience_id)
        application_access = []
        for source_access in package.revision["application_access"]:
            access = dict(source_access)
            secret_ids = access.get("secret_reference_ids")
            if secret_ids is not None:
                access["secret_reference_ids"] = [
                    id_map["SecretReference"][item] for item in secret_ids
                ]
            application_access.append(access)
        revision = self.experiences.create_revision(
            experience_id,
            created_by=f"package:{package.revision['id']}",
            ui_profile=package.revision["ui_profile"],
            application_access=application_access,
        )
        self._mapped(id_map, "ExperienceRevision", package.revision["id"], revision.id)
        self._mapped(
            id_map,
            "SourceTree",
            package.revision["source_tree_id"],
            revision.source_tree_id,
        )
        for source in package.source_files:
            self.source.write_source_file(
                revision.source_tree_id,
                source["path"],
                source["content"],
                role=source["role"],
                language=source["language"],
                actor=f"package:{package.revision['id']}",
            )
        for values in package.dependencies:
            dependency = self.experiences.create_runtime_dependency(
                revision.id, "javascript", values["package"], values["spec"]
            )
            self._mapped(id_map, "RuntimeDependency", values["id"], dependency.id)
        for source_surface in package.surfaces:
            created = self.experience_surfaces.create(
                revision.id,
                source_surface["surface_id"],
                surface_type=source_surface["surface_type"],
                surface_type_version=source_surface["surface_type_version"],
                entrypoint=source_surface["entrypoint"],
                source_paths=source_surface["source_paths"],
                title=source_surface.get("title", ""),
                description=source_surface.get("description"),
                config=source_surface.get("config", {}),
                created_by=f"package:{package.revision['id']}",
            )
            self._mapped(
                id_map,
                "ExperienceSurface",
                (
                    f"{source_surface['experience_revision_id']}:"
                    f"{source_surface['surface_id']}"
                ),
                f"{created.experience_revision_id}:{created.surface_id}",
            )

    def _restore_data(
        self,
        attempt: PackageImportAttempt,
        payload: PackagePayload,
        restore_data: bool,
    ) -> PackageImportAttempt:
        if not restore_data:
            return attempt
        for package in payload.applications:
            app_id = package.application["id"]
            marker = f"data:{app_id}"
            if marker in attempt.completed_members:
                continue
            with self.uow:
                self.store.acquire_write_lock(node_ref("N4XRoot", id="n4x"))
                current = self.records.package_import_attempts[attempt.id]
                for values in package.objects:
                    if (
                        self.store.get_node(
                            "ApplicationObject",
                            {
                                "application_id": app_id,
                                "data_space_id": "production",
                                "id": values["id"],
                            },
                        )
                        is not None
                    ):
                        raise ValidationFailure(
                            f"package_conflict: ApplicationObject:{values['id']}"
                        )
                for values in package.relations:
                    if (
                        self.store.get_app_relation(
                            app_id, "production", values["id"]
                        )
                        is not None
                    ):
                        raise ValidationFailure(
                            f"package_conflict: ApplicationRelation:{values['id']}"
                        )
                created_ids = {
                    key: list(value) for key, value in current.created_ids.items()
                }
                for values in package.objects:
                    rewritten = dict(values)
                    rewritten["application_id"] = app_id
                    rewritten["data_space_id"] = "production"
                    source_revision_id = rewritten.get("object_type_revision_id")
                    rewritten["object_type_revision_id"] = (
                        None
                        if source_revision_id is None
                        else current.id_map["ObjectTypeRevision"][source_revision_id]
                    )
                    application_object = ApplicationObject.model_validate(rewritten)
                    self.store.create_node(
                        "ApplicationObject",
                        {
                            "application_id": application_object.application_id,
                            "data_space_id": application_object.data_space_id,
                            "id": application_object.id,
                        },
                        application_object,
                    )
                    self.uow.objects.attach(application_object)
                    self._created(
                        created_ids, "ApplicationObject", application_object.id
                    )
                for values in package.relations:
                    rewritten = dict(values)
                    rewritten["application_id"] = app_id
                    rewritten["data_space_id"] = "production"
                    source_revision_id = rewritten.get("relation_type_revision_id")
                    rewritten.update(
                        {
                            "relation_type_revision_id": (
                                None
                                if source_revision_id is None
                                else current.id_map["RelationTypeRevision"][
                                    source_revision_id
                                ]
                            ),
                            "physical_type": relation_physical_type(
                                rewritten["relation_type_id"]
                            ),
                            "created_by_invocation_id": None,
                            "updated_by_invocation_id": None,
                        }
                    )
                    relation = ApplicationRelation.model_validate(rewritten)
                    self.uow.relations.save(relation)
                    self._created(created_ids, "ApplicationRelation", relation.id)
                completed = [*current.completed_members, marker]
                counts = dict(current.data_counts)
                counts[f"{app_id}.objects"] = len(package.objects)
                counts[f"{app_id}.relations"] = len(package.relations)
                attempt = current.model_copy(
                    update={
                        "phase": "data_restored",
                        "completed_members": completed,
                        "data_counts": counts,
                        "created_ids": created_ids,
                    }
                )
                self.records.package_import_attempts.save(attempt)
        return attempt

    def _activate_applications(
        self, attempt: PackageImportAttempt, payload: PackagePayload
    ) -> PackageImportAttempt:
        for package in payload.applications:
            app_id = package.application["id"]
            marker = f"application:{app_id}"
            if marker in attempt.completed_members:
                continue
            revision_id = attempt.id_map["ApplicationRevision"][package.revision["id"]]
            self._reopen_rejected_application(revision_id)
            self.activation.activate(revision_id, policy="package_install")
            with self.uow:
                current = self.records.package_import_attempts[attempt.id]
                self.applications.set_status(
                    app_id,
                    "triggers_paused",
                    expected_status="importing",
                )
                attempt = current.model_copy(
                    update={
                        "phase": "applications_activated",
                        "completed_members": [
                            *current.completed_members,
                            marker,
                        ],
                    }
                )
                self.records.package_import_attempts.save(attempt)
        return attempt

    def _activate_experience(
        self, attempt: PackageImportAttempt, payload: PackagePayload
    ) -> PackageImportAttempt:
        if payload.experience is None or "experience" in attempt.completed_members:
            return attempt
        revision_id = attempt.id_map["ExperienceRevision"][
            payload.experience.revision["id"]
        ]
        self._reopen_rejected_experience(revision_id)
        self.experience_activation.activate(revision_id)
        with self.uow:
            current = self.records.package_import_attempts[attempt.id]
            attempt = current.model_copy(
                update={
                    "phase": "experience_activated",
                    "completed_members": [
                        *current.completed_members,
                        "experience",
                    ],
                }
            )
            self.records.package_import_attempts.save(attempt)
        return attempt

    def _disable_imported_working_set(
        self, attempt: PackageImportAttempt, payload: PackagePayload
    ) -> PackageImportAttempt:
        with self.uow:
            current = self.records.package_import_attempts[attempt.id]
            for package in payload.applications:
                self.applications.set_status(
                    package.application["id"],
                    "disabled",
                    expected_status={"importing", "disabled"},
                )
            if payload.experience is not None:
                experience = self.records.experiences[payload.experience.experience["id"]]
                if experience.status != "disabled":
                    self.uow.experiences.save(
                        experience.model_copy(update={"status": "disabled"})
                    )
            attempt = current.model_copy(update={"phase": "disabled"})
            self.records.package_import_attempts.save(attempt)
            return attempt

    def _reopen_rejected_application(self, revision_id: str) -> None:
        with self.uow:
            revision = self.records.revisions[revision_id]
            if revision.status == "rejected":
                self.records.revisions.save(
                    revision.model_copy(update={"status": "validating"})
                )

    def _reopen_rejected_experience(self, revision_id: str) -> None:
        with self.uow:
            revision = self.records.experience_revisions[revision_id]
            if revision.status == "rejected":
                self.records.experience_revisions.save(
                    revision.model_copy(update={"status": "validating"})
                )

    def _collisions(
        self,
        payload: PackagePayload,
        *,
        owned_ids: dict[str, list[str]] | None = None,
    ) -> list[dict[str, str]]:
        owned_ids = owned_ids or {}
        candidates: set[tuple[str, str]] = set()
        for package in payload.applications:
            candidates.add(("Application", package.application["id"]))
            for collection, label in (
                (package.object_types, "ObjectType"),
                (package.relation_types, "RelationType"),
                (package.actions, "Action"),
                (package.triggers, "Trigger"),
            ):
                candidates.update((label, item["stable"]["id"]) for item in collection)
        if payload.experience is not None:
            candidates.add(("Experience", payload.experience.experience["id"]))
        collisions = []
        for label, identity in candidates:
            if identity in owned_ids.get(label, []):
                continue
            exists = (
                self.store.get_app_relation(identity) is not None
                if label == "ApplicationRelation"
                else self.store.get_node(label, {"id": identity}) is not None
            )
            if exists:
                collisions.append({"kind": label, "id": identity})
        for package in payload.applications:
            application_id = package.application["id"]
            for item in package.objects:
                if item["id"] in owned_ids.get("ApplicationObject", []):
                    continue
                if (
                    self.store.get_node(
                        "ApplicationObject",
                        {
                            "application_id": application_id,
                            "data_space_id": "production",
                            "id": item["id"],
                        },
                    )
                    is not None
                ):
                    collisions.append(
                        {"kind": "ApplicationObject", "id": item["id"]}
                    )
            for item in package.relations:
                if item["id"] in owned_ids.get("ApplicationRelation", []):
                    continue
                if (
                    self.store.get_app_relation(
                        application_id, "production", item["id"]
                    )
                    is not None
                ):
                    collisions.append(
                        {"kind": "ApplicationRelation", "id": item["id"]}
                    )
        existing_uris = {item.uri for item in self.records.secret_references.values()}
        owned_secrets = set(owned_ids.get("SecretReferenceUri", []))
        for package in payload.applications:
            for secret in package.secret_requirements:
                if (
                    secret["uri"] in existing_uris
                    and secret["uri"] not in owned_secrets
                ):
                    collisions.append(
                        {"kind": "SecretReferenceUri", "id": secret["uri"]}
                    )
        return sorted(collisions, key=lambda item: (item["kind"], item["id"]))

    def _attempt_by_hash(self, package_hash: str) -> PackageImportAttempt | None:
        matches = [
            item
            for item in self.records.package_import_attempts.values()
            if item.package_hash == package_hash
        ]
        return max(matches, key=lambda item: item.started_at) if matches else None

    def _materializable(self, manifest: PackageManifest) -> bool:
        return (
            manifest.format_version == PACKAGE_FORMAT_VERSION
            and manifest.schema_fingerprint == PACKAGE_SCHEMA_FINGERPRINT
        )

    def _disagreements(self, manifest: PackageManifest) -> list[dict[str, str]]:
        checks = (
            ("format_version", manifest.format_version, PACKAGE_FORMAT_VERSION),
            (
                "schema_fingerprint",
                manifest.schema_fingerprint,
                PACKAGE_SCHEMA_FINGERPRINT,
            ),
            (
                "action_context_version",
                manifest.action_context_version,
                ACTION_CONTEXT_VERSION,
            ),
            (
                "subprocess_version",
                manifest.subprocess_version,
                SUBPROCESS_PROTOCOL_VERSION,
            ),
            (
                "experience_bridge_version",
                manifest.experience_bridge_version,
                EXPERIENCE_BRIDGE_VERSION,
            ),
            (
                "graph_metamodel_version",
                manifest.graph_metamodel_version,
                GRAPH_METAMODEL_VERSION,
            ),
            (
                "graph_schema_fingerprint",
                manifest.graph_schema_fingerprint,
                GRAPH_METAMODEL_SCHEMA_FINGERPRINT,
            ),
        )
        items = [
            {"contract": name, "package": got, "current": expected}
            for name, got, expected in checks
            if got != expected
        ]
        if self._definition_only_v4(manifest):
            items = [
                item
                for item in items
                if item["contract"]
                not in {"graph_metamodel_version", "graph_schema_fingerprint"}
            ]
        return items

    def _definition_only_v4(self, manifest: PackageManifest) -> bool:
        return (
            GRAPH_METAMODEL_VERSION == "n4x.graph.metamodel.v5"
            and manifest.graph_metamodel_version == V4_GRAPH_METAMODEL_VERSION
            and manifest.graph_schema_fingerprint == V4_GRAPH_SCHEMA_FINGERPRINT
            and not manifest.include_data
        )

    def _read_archive(
        self, archive_name: str
    ) -> tuple[PackageManifest, PackagePayload]:
        path = self._archive_path(archive_name)
        self.uow.require_inactive("read Package archive")
        if path.stat().st_size > PACKAGE_MAX_ARCHIVE_BYTES:
            raise ValidationFailure("package_corrupt: archive is too large")
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if (
                len(infos) > PACKAGE_MAX_MEMBERS
                or len(names) != len(set(names))
                or set(names) != _ALLOWED_ENTRIES
            ):
                raise ValidationFailure("package_corrupt: invalid archive entries")
            for info in infos:
                if (
                    info.file_size > PACKAGE_MAX_MEMBER_BYTES
                    or info.filename.startswith("/")
                    or "\\" in info.filename
                    or ".." in Path(info.filename).parts
                ):
                    raise ValidationFailure("package_corrupt: unsafe archive entry")
            manifest_bytes = archive.read("manifest.json")
            payload_bytes = archive.read("payload.json")
        try:
            manifest_values = json.loads(manifest_bytes)
            if manifest_values.get("format_version") == "n4x.package.v1":
                raise ValidationFailure(
                    "package_unsupported: n4x.package.v1 archives are not supported; "
                    "export a canonical Surface package"
                )
            manifest = PackageManifest.model_validate_json(manifest_bytes)
            payload = PackagePayload.model_validate_json(payload_bytes)
            self._validate_archive_payload(payload)
        except ValidationFailure:
            raise
        except Exception as exc:
            raise ValidationFailure(f"package_corrupt: {exc}") from exc
        entry = manifest.entries[0] if len(manifest.entries) == 1 else None
        if (
            entry is None
            or entry.path != "payload.json"
            or entry.size != len(payload_bytes)
            or entry.content_hash != sha256_text(payload_bytes.decode("utf-8"))
            or manifest.package_hash != self._package_hash(manifest, payload_bytes)
        ):
            raise ValidationFailure("package_corrupt: content hash mismatch")
        return manifest, payload

    @staticmethod
    def _validate_archive_payload(payload: PackagePayload) -> None:
        member_count = len(payload.applications) + (
            1 if payload.experience is not None else 0
        )
        if member_count > PACKAGE_MAX_MEMBERS:
            raise ValueError("too many Package members")
        members = list(payload.applications)
        source_groups = [item.source_files for item in members]
        if payload.experience is not None:
            source_groups.append(payload.experience.source_files)
        for source_files in source_groups:
            paths = [str(item.get("path", "")) for item in source_files]
            if len(paths) != len(set(paths)):
                raise ValueError("duplicate source path")
            for path in paths:
                candidate = Path(path)
                if (
                    not path
                    or candidate.is_absolute()
                    or "\\" in path
                    or ".." in candidate.parts
                ):
                    raise ValueError(f"unsafe source path: {path}")

    def _refuse_if_experience_references(
        self,
        application_id: str,
        *,
        ignore_experience_id: str | None = None,
    ) -> None:
        referenced_by = [
            revision.id
            for revision in self.records.experience_revisions.values()
            if (
                ignore_experience_id is None
                or revision.experience_id != ignore_experience_id
            )
            and any(
                item.application_id == application_id
                for item in revision.application_access
            )
        ]
        if referenced_by:
            raise ValidationFailure(
                "application is referenced by Experience revisions: "
                + ", ".join(referenced_by)
            )

    def _refuse_if_shared_applications(self, experience_id: str) -> None:
        experience = self.records.experiences.get(experience_id)
        if experience is None or experience.active_revision_id is None:
            return
        revision = self.records.experience_revisions.get(
            experience.active_revision_id
        )
        if revision is None:
            return
        for item in revision.application_access:
            self._refuse_if_experience_references(
                item.application_id,
                ignore_experience_id=experience_id,
            )

    def _delete_owned_secret_values(self, application_ids: list[str]) -> None:
        owned = set(application_ids)
        uris = [
            item.uri
            for item in self.records.secret_references.values()
            if item.application_id in owned
        ]
        self.uow.require_inactive("delete secret backend values")
        for uri in uris:
            self.secrets.backend.delete(uri)

    def _delete_pinning_deployments(
        self,
        application_ids: list[str],
        experience_id: str | None,
    ) -> None:
        owned_apps = set(application_ids)
        experience_revision_ids = {
            revision.id
            for revision in self.records.experience_revisions.values()
            if experience_id is not None and revision.experience_id == experience_id
        }
        for deployment in list(self.records.development_deployments.values()):
            pins_app = owned_apps.intersection(
                deployment.application_revision_ids
            ) or owned_apps.intersection(deployment.data_space_ids)
            pins_experience = (
                deployment.experience_revision_id in experience_revision_ids
            )
            if pins_app or pins_experience:
                self.records.development_deployments.delete(deployment.id)

    def _delete_application_subgraph(self, application_id: str) -> None:
        revision_ids = {
            item.id
            for item in self.records.revisions.values()
            if item.application_id == application_id
        }
        object_type_ids = {
            item.id
            for item in self.records.object_types.values()
            if item.application_id == application_id
        }
        relation_type_ids = {
            item.id
            for item in self.records.relation_types.values()
            if item.application_id == application_id
        }
        action_ids = {
            item.id
            for item in self.records.actions.values()
            if item.application_id == application_id
        }
        trigger_ids = {
            item.id
            for item in self.records.triggers.values()
            if item.application_id == application_id
        }
        action_revision_ids = {
            item.id
            for item in self.records.action_revisions.values()
            if item.application_revision_id in revision_ids
        }
        job_ids = {
            item.id
            for item in self.records.job_records.values()
            if item.application_id == application_id
        }
        checkpoint_ids = {
            item.id
            for item in self.records.checkpoints.values()
            if item.application_id == application_id
        }
        for relation in self.uow.relations.list_all():
            if relation.application_id == application_id:
                self.uow.relations.delete(relation)
        self._delete_where(
            self.records.objects,
            lambda item: item.application_id == application_id,
        )
        self._delete_where(
            self.records.data_spaces,
            lambda item: item.application_id == application_id,
        )
        self._delete_where(
            self.records.job_attempts, lambda item: item.job_id in job_ids
        )
        self._delete_where(
            self.records.job_records,
            lambda item: item.application_id == application_id,
        )
        self._delete_where(
            self.records.cypher_audits,
            lambda item: item.application_id == application_id,
        )
        self._delete_where(
            self.records.invocations,
            lambda item: item.action_revision_id in action_revision_ids,
        )
        self._delete_where(
            self.records.checkpoint_blobs,
            lambda item: item.checkpoint_id in checkpoint_ids,
        )
        self._delete_where(
            self.records.checkpoint_snapshots,
            lambda item: item.checkpoint_id in checkpoint_ids,
        )
        self._delete_where(
            self.records.checkpoints,
            lambda item: item.application_id == application_id,
        )
        self._delete_where(
            self.records.credential_records,
            lambda item: item.application_id == application_id,
        )
        self._delete_where(
            self.records.callback_routes,
            lambda item: item.application_id == application_id,
        )
        self._delete_where(
            self.records.secret_references,
            lambda item: item.application_id == application_id,
        )
        self._delete_where(
            self.records.test_cases,
            lambda item: item.application_revision_id in revision_ids,
        )
        self._delete_where(
            self.records.validation_reports,
            lambda item: item.application_revision_id in revision_ids,
        )
        self._delete_owned_revision_runtime(revision_ids)
        self._delete_where(
            self.records.trigger_revisions,
            lambda item: item.application_revision_id in revision_ids,
        )
        self._delete_where(
            self.records.action_revisions,
            lambda item: item.application_revision_id in revision_ids,
        )
        self._delete_where(
            self.records.object_type_revisions,
            lambda item: item.application_revision_id in revision_ids
            or item.object_type_id in object_type_ids,
        )
        self._delete_where(
            self.records.relation_type_revisions,
            lambda item: item.application_revision_id in revision_ids
            or item.relation_type_id in relation_type_ids,
        )
        self._delete_where(
            self.records.triggers, lambda item: item.id in trigger_ids
        )
        self._delete_where(self.records.actions, lambda item: item.id in action_ids)
        self._delete_where(
            self.records.object_types, lambda item: item.id in object_type_ids
        )
        self._delete_where(
            self.records.relation_types, lambda item: item.id in relation_type_ids
        )
        for revision_id in revision_ids:
            self.records.revisions.delete(revision_id)
        self.records.applications.delete(application_id)

    def _delete_experience_subgraph(self, experience_id: str) -> None:
        revision_ids = {
            item.id
            for item in self.records.experience_revisions.values()
            if item.experience_id == experience_id
        }
        self._delete_where(
            self.records.experience_surfaces,
            lambda item: item.experience_revision_id in revision_ids,
        )
        self._delete_where(
            self.records.experience_validation_reports,
            lambda item: item.experience_revision_id in revision_ids,
        )
        self._delete_owned_revision_runtime(revision_ids)
        for revision_id in revision_ids:
            self.records.experience_revisions.delete(revision_id)
        self.records.experiences.delete(experience_id)

    def _delete_owned_revision_runtime(self, revision_ids: set[str]) -> None:
        tree_ids = {
            item.id
            for item in self.records.source_trees.values()
            if item.owner_id in revision_ids
        }
        self._delete_where(
            self.records.source_changes,
            lambda item: item.source_tree_id in tree_ids,
        )
        self._delete_where(
            self.records.source_files,
            lambda item: item.source_tree_id in tree_ids,
        )
        for tree_id in tree_ids:
            self.records.source_trees.delete(tree_id)
        self._delete_where(
            self.records.runtime_dependencies,
            lambda item: item.owner_id in revision_ids,
        )
        self._delete_where(
            self.records.python_environments,
            lambda item: item.application_revision_id in revision_ids,
        )
        self._delete_where(
            self.records.javascript_environments,
            lambda item: item.owner_id in revision_ids,
        )
        self._delete_where(
            self.records.build_artifacts,
            lambda item: item.owner_id in revision_ids,
        )
        self._delete_where(
            self.records.build_invocations,
            lambda item: item.owner_id in revision_ids,
        )

    def _delete_where(self, collection, predicate) -> None:
        for value in list(collection.values()):
            if predicate(value):
                collection.delete(collection.key_for(value))

    def _package_hash(self, manifest: PackageManifest, payload_bytes: bytes) -> str:
        unsigned = manifest.model_copy(update={"package_hash": ""})
        return sha256_text(
            stable_json(unsigned.model_dump(mode="json"))
            + "\n"
            + payload_bytes.decode("utf-8")
        )

    def archive_path(self, archive_name: str) -> Path:
        if not _ARCHIVE_NAME.fullmatch(archive_name):
            raise ValueError("archive_name must be a simple name ending in .n4xp")
        return self.package_root / archive_name

    def _archive_path(self, archive_name: str) -> Path:
        return self.archive_path(archive_name)

    @staticmethod
    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()

    @staticmethod
    def normalize_sha256(value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("sha256:"):
            return stripped
        return "sha256:" + stripped

    @staticmethod
    def _write_entry(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        archive.writestr(info, content)

    @staticmethod
    def _mapped(
        id_map: dict[str, dict[str, str]],
        kind: str,
        source_id: str,
        destination_id: str,
    ) -> None:
        id_map.setdefault(kind, {})[source_id] = destination_id

    @staticmethod
    def _created(created_ids: dict[str, list[str]], kind: str, identity: str) -> None:
        created_ids.setdefault(kind, []).append(identity)

    @staticmethod
    def _attempt_result(
        attempt: PackageImportAttempt, manifest: PackageManifest
    ) -> dict[str, Any]:
        return {
            "attempt": attempt.model_dump(mode="json"),
            "package_hash": manifest.package_hash,
            "omitted_bindings": manifest.omitted_bindings,
            "unbound_secret_uris": sorted(
                attempt.created_ids.get("SecretReferenceUri", [])
            ),
            "paused_application_ids": sorted(
                attempt.created_ids.get("Application", [])
            ),
        }
