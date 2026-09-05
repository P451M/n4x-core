from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from n4x.kernel.errors import ValidationFailure
from n4x.runtime.actions import RuntimePaths
from n4x.secrets.backends import InMemorySecretBackend
from n4x.system.runtime import SystemRuntime
from n4x.testing import InMemoryGraphStore
from tests.test_packages import _active_application


def test_experience_root_delete_then_reimport_restores_working_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimePaths.temporary()
    system = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(system, "backend")
    _fake_surface_build(monkeypatch)
    system.create_application("unrelated", "Unrelated")
    unrelated = system.create_application_revision("unrelated")
    system.activate_application_revision(unrelated.id)
    system.create_experience("portable-ui", "Portable UI")
    experience_revision = system.create_experience_revision(
        "portable-ui",
        ui_profile="none",
        application_access=[{"application_id": "backend"}],
    )
    system.source.write_source_file(
        experience_revision.id,
        "src/main.ts",
        "document.body.textContent = 'portable';\n",
        role="surface",
        language="typescript",
    )
    system.create_experience_surface(
        experience_revision.id,
        "main",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/"},
    )
    system.activate_experience_revision(experience_revision.id)

    deleted = system.delete_working_set(
        "experience",
        "portable-ui",
        "portable-ui.n4xp",
        confirmation_root_id="portable-ui",
    )

    assert deleted["application_ids"] == ["backend"]
    assert deleted["experience_id"] == "portable-ui"
    assert system.inspect_application("backend") is None
    assert system.graph.experiences.get("portable-ui") is None
    assert system.inspect_application("unrelated") is not None
    assert system.validate_graph_shape()["ok"] is True

    restored = SystemRuntime(
        InMemoryGraphStore(),
        secret_backend=InMemorySecretBackend(),
        runtime_paths=paths,
    )
    installed = restored.import_package("portable-ui.n4xp")
    assert installed["attempt"]["status"] == "succeeded"
    assert restored.inspect_application("backend").status == "triggers_paused"
    assert (
        restored.list_application_objects("backend")[0].values["title"] == "portable"
    )
    assert restored.experiences.inspect("portable-ui")["experience"][
        "active_revision_id"
    ]
    assert restored.validate_graph_shape()["ok"] is True


def test_application_root_refuses_while_experience_lists_it() -> None:
    paths = RuntimePaths.temporary()
    system = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(system, "portable")
    system.create_experience("shell", "Shell")
    system.create_experience_revision(
        "shell",
        ui_profile="none",
        application_access=[{"application_id": "portable"}],
    )

    with pytest.raises(ValidationFailure, match="referenced by Experience"):
        system.delete_working_set(
            "application",
            "portable",
            "portable.n4xp",
            confirmation_root_id="portable",
        )

    assert system.inspect_application("portable") is not None
    assert not (paths.root / "packages" / "portable.n4xp").exists()
    assert system.list_application_objects("portable")


def test_experience_root_refuses_when_another_experience_lists_an_exported_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimePaths.temporary()
    system = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(system, "backend")
    _fake_surface_build(monkeypatch)
    owner = system.create_experience("owner-ui", "Owner UI")
    owner_revision = system.create_experience_revision(
        owner.id,
        ui_profile="none",
        application_access=[{"application_id": "backend"}],
    )
    system.source.write_source_file(
        owner_revision.id,
        "src/main.ts",
        "document.body.textContent = 'owner';\n",
        role="surface",
        language="typescript",
    )
    system.create_experience_surface(
        owner_revision.id,
        "main",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/"},
    )
    system.activate_experience_revision(owner_revision.id)
    system.create_experience("sibling-ui", "Sibling UI")
    system.create_experience_revision(
        "sibling-ui",
        ui_profile="none",
        application_access=[{"application_id": "backend"}],
    )

    with pytest.raises(ValidationFailure, match="referenced by Experience"):
        system.delete_working_set(
            "experience",
            "owner-ui",
            "owner-ui.n4xp",
            confirmation_root_id="owner-ui",
        )

    assert system.inspect_application("backend") is not None
    assert system.graph.experiences.get("owner-ui") is not None
    assert system.graph.experiences.get("sibling-ui") is not None
    assert not (paths.root / "packages" / "owner-ui.n4xp").exists()
    assert system.validate_graph_shape()["ok"] is True


def test_delete_working_set_exports_data_from_prior_schema_revisions() -> None:
    paths = RuntimePaths.temporary()
    system = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(system, "portable")
    existing = system.list_application_objects("portable")[0]
    prior_type_revision = next(
        item.id
        for item in system.graph.object_type_revisions.values()
        if item.object_type_id == existing.object_type_id
    )
    with system.uow:
        system.uow.objects.save(
            existing.model_copy(update={"object_type_revision_id": prior_type_revision})
        )
    draft = system.create_application_revision("portable")
    system.activate_application_revision(draft.id)

    deleted = system.delete_working_set(
        "application",
        "portable",
        "portable.n4xp",
        confirmation_root_id="portable",
    )

    assert deleted["application_ids"] == ["portable"]
    assert system.inspect_application("portable") is None
    archive = paths.root / "packages" / "portable.n4xp"
    assert archive.is_file()
    with zipfile.ZipFile(archive) as zipped:
        payload = json.loads(zipped.read("payload.json"))
    exported = payload["applications"][0]
    type_revisions = {item["revision"]["id"] for item in exported["object_types"]}
    object_revisions = {
        item["object_type_revision_id"] for item in exported["objects"]
    }
    assert prior_type_revision in type_revisions
    assert object_revisions == {prior_type_revision}


def test_confirmation_mismatch_does_not_delete() -> None:
    paths = RuntimePaths.temporary()
    system = SystemRuntime(InMemoryGraphStore(), runtime_paths=paths)
    _active_application(system, "portable")

    with pytest.raises(ValidationFailure, match="confirmation_root_id"):
        system.delete_working_set(
            "application",
            "portable",
            "portable.n4xp",
            confirmation_root_id="other",
        )

    assert system.inspect_application("portable") is not None
    assert not (paths.root / "packages" / "portable.n4xp").exists()


def _fake_surface_build(monkeypatch: pytest.MonkeyPatch) -> None:
    real_run = subprocess.run
    real_which = shutil.which
    monkeypatch.setattr(
        "n4x.runtime.surfaces.shutil.which",
        lambda executable: (
            f"/fake/{executable}"
            if executable in {"node", "pnpm"}
            else real_which(executable)
        ),
    )

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        executable = Path(command[0])
        if not str(executable).startswith("/fake/"):
            return real_run(command, **kwargs)
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
