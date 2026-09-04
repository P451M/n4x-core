from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
import zipfile
from pathlib import Path

import pytest

from n4x.graph.store import Neo4jGraphStore
from n4x.contracts import PACKAGE_FORMAT_VERSION
from n4x.contracts.package import PackageManifest
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.hash import sha256_json, stable_json
from n4x.kernel.models import ObjectTypeRevision
from n4x.system.runtime import SystemRuntime
from n4x.runtime.actions import RuntimePaths
from n4x.secrets.backends import InMemorySecretBackend
from n4x.testing import InMemoryGraphStore
from tests.cypher_source import action_source


def _active_application(system: SystemRuntime, application_id: str = "portable") -> None:
    system.create_application(application_id, "Portable")
    revision = system.create_application_revision(application_id)
    system.create_object_type(
        revision.id,
        f"{application_id}.Note",
        name="Note",
        properties={"title": {"type": "string"}},
        required=["title"],
    )
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/create.py",
        action_source(
            "def run(ctx, input):\n"
            f"    return upsert_object(ctx, '{application_id}.Note', input, "
            "object_id=input.get('id'))\n"
        ),
        role="action",
        language="python",
    )
    system.create_action(
        revision.id,
        f"{application_id}.create",
        kind="normal",
        entrypoint="actions/create.py:run",
        source_paths=["actions/create.py"],
    )
    system.activate_application_revision(revision.id)
    invocation = system.run_active_action(
        application_id,
        f"{application_id}.create",
        {"id": f"{application_id}-1", "title": "portable"},
    )
    assert invocation.status == "succeeded", invocation.error


def test_application_package_roundtrip_with_data_and_resume() -> None:
    paths = RuntimePaths.temporary()
    source = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(source)

    first = source.export_package(
        "application", "portable", "portable.n4xp", include_data=True
    )
    second = source.export_package(
        "application", "portable", "portable-copy.n4xp", include_data=True
    )
    assert first["package_hash"] == second["package_hash"]
    assert (paths.root / "packages" / "portable.n4xp").read_bytes() == (
        paths.root / "packages" / "portable-copy.n4xp"
    ).read_bytes()

    destination = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    dry_run = destination.import_package("portable.n4xp", dry_run=True)
    assert dry_run["collisions"] == []
    installed = destination.import_package("portable.n4xp")
    attempt = installed["attempt"]

    assert attempt["status"] == "succeeded"
    assert destination.inspect_application("portable").status == "triggers_paused"
    imported_action = destination.graph.actions["portable.create"]
    assert destination.graph.action_revisions[
        imported_action.active_revision_id
    ].kind == "normal"
    assert (
        destination.list_application_objects("portable")[0].values["title"]
        == "portable"
    )
    invocation = destination.run_active_action(
        "portable", "portable.create", {"id": "portable-2", "title": "again"}
    )
    assert invocation.status == "succeeded"
    assert destination.import_package("portable.n4xp")["attempt"]["id"] == attempt["id"]
    assert destination.resume_application_triggers("portable").status == "active"


def test_package_stage_roundtrip_into_a_separate_runtime_root() -> None:
    source_paths = RuntimePaths.temporary()
    source = SystemRuntime(InMemoryGraphStore(), runtime_paths=source_paths)
    _active_application(source)
    source.export_package("application", "portable", "portable.n4xp")
    archive_bytes = (source_paths.root / "packages" / "portable.n4xp").read_bytes()

    destination_paths = RuntimePaths.temporary()
    destination = SystemRuntime(InMemoryGraphStore(), runtime_paths=destination_paths)
    staged = destination.stage_package("portable.n4xp", archive_bytes)

    assert staged["archive_name"] == "portable.n4xp"
    assert staged["size"] == len(archive_bytes)
    assert staged["content_sha256"] == (
        "sha256:" + hashlib.sha256(archive_bytes).hexdigest()
    )
    assert (
        destination_paths.root / "packages" / "portable.n4xp"
    ).read_bytes() == archive_bytes
    inspected = destination.inspect_package("portable.n4xp")
    assert inspected["compatible"] is True
    assert inspected["collisions"] == []
    installed = destination.import_package("portable.n4xp")
    assert installed["attempt"]["status"] == "succeeded"
    assert destination.inspect_application("portable").status == "triggers_paused"


def test_package_stage_rejects_invalid_name_size_and_existing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimePaths.temporary()
    system = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    with pytest.raises(ValueError, match="archive_name"):
        system.stage_package("not-a-package.zip", b"pk")
    monkeypatch.setattr("n4x.system.packages.PACKAGE_MAX_ARCHIVE_BYTES", 8)
    with pytest.raises(ValidationFailure, match="too large"):
        system.stage_package("too-big.n4xp", b"0123456789")
    system.stage_package("repeat.n4xp", b"one")
    with pytest.raises(FileExistsError):
        system.stage_package("repeat.n4xp", b"two")
    replaced = system.stage_package("repeat.n4xp", b"two", overwrite=True)
    assert replaced["size"] == 3
    assert (paths.root / "packages" / "repeat.n4xp").read_bytes() == b"two"


def test_package_stage_from_path_and_list(tmp_path: Path) -> None:
    paths = RuntimePaths.temporary()
    system = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    source = tmp_path / "from-disk.n4xp"
    source.write_bytes(b"from-disk")
    staged = system.stage_package_from_path("from-disk.n4xp", source)
    assert staged["size"] == 9
    assert system.list_packages()[0]["archive_name"] == "from-disk.n4xp"
    with pytest.raises(ValidationFailure, match="hash mismatch"):
        system.stage_package_from_path(
            "from-disk.n4xp",
            source,
            overwrite=True,
            expected_sha256="sha256:" + "0" * 64,
        )


def test_package_import_collapses_repeated_definition_history_to_active_revision() -> None:
    paths = RuntimePaths.temporary()
    source = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(source, "evolved")
    stable = source.graph.object_types["evolved.Note"]
    active = source.graph.object_type_revisions[stable.active_revision_id]
    legacy = ObjectTypeRevision(
        id="evolved.Note@legacy",
        object_type_id=active.object_type_id,
        application_revision_id=active.application_revision_id,
        name=active.name,
        properties={"legacy_title": {"type": "string"}},
        required=[],
        content_hash=sha256_json(
            {
                "name": active.name,
                "properties": {"legacy_title": {"type": "string"}},
                "required": [],
            }
        ),
    )
    source.graph.object_type_revisions[legacy.id] = legacy
    source.export_package("application", "evolved", "evolved.n4xp")

    destination = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    result = destination.import_package("evolved.n4xp")

    assert result["attempt"]["status"] == "succeeded"
    imported = destination.inspect_application("evolved")
    assert imported.active_revision_id is not None
    imported_stable = destination.graph.object_types["evolved.Note"]
    imported_active = destination.graph.object_type_revisions[
        imported_stable.active_revision_id
    ]
    assert imported_active.properties == active.properties
    assert imported_active.required == active.required
    assert (
        len(
            [
                item
                for item in destination.graph.object_type_revisions.values()
                if item.application_revision_id == imported.active_revision_id
                and item.object_type_id == "evolved.Note"
            ]
        )
        == 1
    )


def test_package_archive_rejects_unknown_and_duplicate_entries() -> None:
    paths = RuntimePaths.temporary()
    system = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(system, "unsafe")
    system.export_package("application", "unsafe", "unsafe.n4xp")
    path = paths.root / "packages" / "unsafe.n4xp"
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("unknown", b"value")

    with pytest.raises(ValidationFailure, match="package_corrupt"):
        system.inspect_package("unsafe.n4xp")


def test_experience_package_v2_roundtrip_activates_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_surface_toolchain(monkeypatch)
    paths = RuntimePaths.temporary()
    source = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    source.create_application("backend", "Backend")
    app_revision = source.create_application_revision("backend")
    secret_reference = source.secrets.create_reference(
        "backend", "secret://backend/package-password"
    )
    source.activate_application_revision(app_revision.id)
    source.create_experience("portable-ui", "Portable UI")
    experience_revision = source.create_experience_revision(
        "portable-ui",
        ui_profile="none",
        application_access=[
            {
                "application_id": "backend",
                "secret_reference_ids": [secret_reference.id],
            }
        ],
    )
    source.source.write_source_file(
        experience_revision.source_tree_id,
        "src/main.ts",
        "document.body.textContent = 'portable';\n",
        role="surface",
        language="typescript",
    )
    source.create_experience_surface(
        experience_revision.id,
        "main",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/"},
    )
    source.activate_experience_revision(experience_revision.id)
    source.export_package("experience", "portable-ui", "portable-ui.n4xp")

    destination = SystemRuntime(
        InMemoryGraphStore(),
        secret_backend=InMemorySecretBackend(),
        runtime_paths=paths,
    )
    destination.import_package("portable-ui.n4xp")

    experience = destination.experiences.inspect("portable-ui")
    assert experience["experience"]["active_revision_id"] is not None
    imported_surface = destination.inspect_experience_surface(
        experience["experience"]["active_revision_id"], "main"
    )
    assert imported_surface.surface_type == "browser"
    assert imported_surface.config["mount_path"] == "/"
    assert destination.inspect_application("backend").status == "triggers_paused"
    imported_revision = destination.graph.experience_revisions[
        experience["experience"]["active_revision_id"]
    ]
    imported_secret_id = imported_revision.application_access[0].secret_reference_ids[0]
    assert imported_secret_id != secret_reference.id
    assert (
        destination.graph.secret_references[imported_secret_id].uri
        == secret_reference.uri
    )
    discovered = destination.list_experience_secrets("portable-ui", "backend")
    assert discovered[0]["secret_reference_id"] == imported_secret_id
    assert discovered[0]["uri"] == secret_reference.uri
    assert destination.validate_graph_shape()["ok"] is True


def test_package_v2_explicitly_rejects_package_v1_archive() -> None:
    paths = RuntimePaths.temporary()
    system = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(system, "legacy-package")
    exported = system.export_package(
        "application", "legacy-package", "legacy-package.n4xp"
    )
    assert exported["format_version"] == PACKAGE_FORMAT_VERSION == "n4x.package.v2"
    path = paths.root / "packages" / "legacy-package.n4xp"
    with zipfile.ZipFile(path, "r") as source:
        manifest = source.read("manifest.json").replace(
            b'"n4x.package.v2"', b'"n4x.package.v1"'
        )
        payload = source.read("payload.json")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr("payload.json", payload)

    with pytest.raises(
        ValidationFailure,
        match=r"package_unsupported: n4x\.package\.v1 archives",
    ):
        system.inspect_package("legacy-package.n4xp")


def test_v5_imports_definition_only_v4_package_and_rejects_v4_data() -> None:
    paths = RuntimePaths.temporary()
    source = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(source, "v4-definition")
    source.export_package(
        "application",
        "v4-definition",
        "v4-definition.n4xp",
        include_data=False,
    )
    _rewrite_archive_as_v4(source, paths.root / "packages" / "v4-definition.n4xp")

    destination = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    inspected = destination.inspect_package("v4-definition.n4xp")
    installed = destination.import_package("v4-definition.n4xp")

    assert inspected["compatible"] is True
    assert installed["attempt"]["status"] == "succeeded"
    assert destination.inspect_application("v4-definition").status == (
        "triggers_paused"
    )

    source.export_package(
        "application",
        "v4-definition",
        "v4-data.n4xp",
        include_data=True,
    )
    _rewrite_archive_as_v4(source, paths.root / "packages" / "v4-data.n4xp")
    assert destination.inspect_package("v4-data.n4xp")["compatible"] is False
    with pytest.raises(ValidationFailure, match="package_incompatible"):
        destination.import_package("v4-data.n4xp")


def test_outdated_package_inspects_and_imports_disabled() -> None:
    paths = RuntimePaths.temporary()
    source = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(source, "outdated")
    source.export_package(
        "application", "outdated", "outdated.n4xp", include_data=True
    )
    _rewrite_archive_outdated(source, paths.root / "packages" / "outdated.n4xp")

    destination = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    inspected = destination.inspect_package("outdated.n4xp")
    assert inspected["compatible"] is False
    assert inspected["materializable"] is True
    contracts = {item["contract"] for item in inspected["disagreements"]}
    assert "action_context_version" in contracts
    assert "subprocess_version" in contracts
    assert "graph_schema_fingerprint" in contracts

    dry_run = destination.import_package("outdated.n4xp", dry_run=True)
    assert dry_run["compatible"] is False
    assert dry_run["materializable"] is True
    with pytest.raises(ValidationFailure, match="package_incompatible"):
        destination.import_package("outdated.n4xp")

    installed = destination.import_package(
        "outdated.n4xp", allow_incompatible=True
    )
    application = destination.inspect_application("outdated")
    assert installed["attempt"]["status"] == "succeeded"
    assert installed["attempt"]["phase"] == "completed_disabled"
    assert installed["activated"] is False
    assert application.status == "disabled"
    assert application.active_revision_id is None
    assert destination.list_application_objects("outdated") == []
    with pytest.raises(ValidationFailure, match="not available for actions"):
        destination.run_active_action(
            "outdated", "outdated.create", {"id": "x", "title": "no"}
        )
    retried = destination.import_package("outdated.n4xp", allow_incompatible=True)
    assert retried["attempt"]["id"] == installed["attempt"]["id"]
    assert destination.inspect_application("outdated").status == "disabled"

    rewritten = destination.create_application_revision("outdated")
    assert rewritten.parent_revision_id is not None
    destination.activate_application_revision(rewritten.id)
    usable = destination.inspect_application("outdated")
    assert usable.status == "triggers_paused"
    assert usable.active_revision_id == rewritten.id


def test_package_import_rejects_collision_without_overwrite() -> None:
    paths = RuntimePaths.temporary()
    source = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(source, "collision")
    source.export_package("application", "collision", "collision.n4xp")
    destination = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    destination.create_application("collision", "Destination")

    dry_run = destination.import_package("collision.n4xp", dry_run=True)
    assert dry_run["collisions"] == [{"kind": "Application", "id": "collision"}]
    with pytest.raises(ValidationFailure, match="package_conflict"):
        destination.import_package("collision.n4xp")
    assert destination.inspect_application("collision").name == "Destination"
    assert destination.graph.package_import_attempts.values() == []


def test_package_import_resumes_after_runtime_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimePaths.temporary()
    source = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(source, "restartable")
    source.export_package(
        "application", "restartable", "restartable.n4xp", include_data=True
    )
    store = InMemoryGraphStore()
    destination = SystemRuntime(store, runtime_paths=paths)

    def fail_activation(*_: object, **__: object) -> None:
        raise RuntimeError("simulated build interruption")

    monkeypatch.setattr(destination.activation_service, "activate", fail_activation)
    with pytest.raises(
        ValidationFailure, match="package_import_failed.*simulated build interruption"
    ):
        destination.import_package("restartable.n4xp")
    failed = destination.graph.package_import_attempts.values()[0]
    assert failed.status == "failed"
    assert failed.phase == "data_restored"

    restarted = SystemRuntime(store, runtime_paths=paths)
    result = restarted.import_package("restartable.n4xp")
    assert result["attempt"]["id"] == failed.id
    assert result["attempt"]["status"] == "succeeded"
    assert restarted.inspect_application("restartable").status == "triggers_paused"


@pytest.mark.neo4j
def test_package_roundtrip_has_neo4j_store_parity(neo4j_graph) -> None:
    application_id = f"package-neo4j-{uuid.uuid4().hex}"
    archive_name = f"{application_id}.n4xp"
    paths = RuntimePaths.temporary()
    source = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(source, application_id)
    exported = source.export_package(
        "application", application_id, archive_name, include_data=True
    )
    destination = SystemRuntime(Neo4jGraphStore(neo4j_graph), runtime_paths=paths)
    try:
        result = destination.import_package(archive_name)
        assert result["attempt"]["status"] == "succeeded"
        assert destination.validate_graph_shape()["ok"] is True
        restarted = SystemRuntime(Neo4jGraphStore(neo4j_graph), runtime_paths=paths)
        assert (
            restarted.inspect_package_import(result["attempt"]["id"])["package_hash"]
            == exported["package_hash"]
        )
        assert restarted.list_application_objects(application_id)
    finally:
        neo4j_graph.run_cypher(
            """
            MATCH (app:Application {id: $application_id})
            OPTIONAL MATCH (app)-[*0..]->(owned)
            WITH collect(DISTINCT owned) AS nodes
            UNWIND nodes AS node
            DETACH DELETE node
            """,
            {"application_id": application_id},
        )
        neo4j_graph.run_cypher(
            """
            MATCH (attempt:PackageImportAttempt {package_hash: $package_hash})
            DETACH DELETE attempt
            """,
            {"package_hash": exported["package_hash"]},
        )


def _fake_surface_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "n4x.runtime.surfaces.shutil.which",
        lambda executable: f"/fake/{executable}",
    )

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "--version":
            return subprocess.CompletedProcess(
                command, 0, stdout="v24.0.0\n", stderr=""
            )
        project = Path(command[command.index("--dir") + 1])
        if "install" in command:
            (project / "pnpm-lock.yaml").write_text(
                "lockfileVersion: '9.0'\n", encoding="utf-8"
            )
        else:
            dist = project / "dist"
            dist.mkdir(parents=True, exist_ok=True)
            (dist / "index.html").write_text("<main>built</main>", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("n4x.runtime.surfaces.subprocess.run", run)


def _rewrite_archive_outdated(system: SystemRuntime, path: Path) -> None:
    with zipfile.ZipFile(path, "r") as source:
        manifest_values = json.loads(source.read("manifest.json"))
        payload = source.read("payload.json")
    manifest_values.update(
        {
            "action_context_version": "n4x.action.context.v1",
            "subprocess_version": "n4x.action.subprocess.v1",
            "graph_schema_fingerprint": (
                "sha256:4ade0cf9e406761f68e8ae5cfbb1ced53fba5049959c951475158ed884e38800"
            ),
            "package_hash": "",
        }
    )
    manifest = PackageManifest.model_validate(manifest_values)
    manifest_values["package_hash"] = system.package_service._package_hash(
        manifest, payload
    )
    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.writestr(
            "manifest.json",
            stable_json(manifest_values).encode("utf-8"),
        )
        archive.writestr("payload.json", payload)


def _rewrite_archive_as_v4(system: SystemRuntime, path: Path) -> None:
    with zipfile.ZipFile(path, "r") as source:
        manifest_values = json.loads(source.read("manifest.json"))
        payload = source.read("payload.json")
    manifest_values.update(
        {
            "graph_metamodel_version": "n4x.graph.metamodel.v4",
            "graph_schema_fingerprint": (
                "sha256:e939e862b9dd9d88d1a17b8fe699107c"
                "f5230b2ebebab905624df2d931d8b5d0"
            ),
            "package_hash": "",
        }
    )
    manifest = PackageManifest.model_validate(manifest_values)
    manifest_values["package_hash"] = system.package_service._package_hash(
        manifest, payload
    )
    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.writestr(
            "manifest.json",
            stable_json(manifest_values).encode("utf-8"),
        )
        archive.writestr("payload.json", payload)
