from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from n4x.graph.store import node_ref
from n4x.kernel.errors import GraphUnitOfWorkError, ValidationFailure
from n4x.testing import create_test_runtime


def test_inactive_guard_preserves_caller_owned_scope() -> None:
    system = create_test_runtime()

    with system.uow:
        with pytest.raises(
            GraphUnitOfWorkError, match="test external wait"
        ):
            system.uow.require_inactive("test external wait")
        assert system.uow.is_active
        assert system.uow.depth == 1

    assert not system.uow.is_active


def test_python_environment_rejects_inherited_uow() -> None:
    system = create_test_runtime()
    app = system.create_application("python-boundary", "Python Boundary")
    revision = system.create_application_revision(app.id)

    with system.uow:
        with pytest.raises(
            GraphUnitOfWorkError, match="prepare Python environment"
        ):
            system.runtime.python_environments.prepare(revision.id)
        assert system.uow.is_active
        assert system.uow.depth == 1


def test_python_subprocesses_run_without_active_uow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = create_test_runtime()
    app = system.create_application("python-waits", "Python Waits")
    revision = system.create_application_revision(app.id)
    manager = system.runtime.python_environments
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "n4x.runtime.environments.shutil.which", lambda _: "/fake/uv"
    )

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert not manager.uow.is_active
        assert any(
            invocation.status == "started"
            for invocation in manager.graph.build_invocations.values()
        )
        calls.append(command)
        if len(command) > 1 and command[1] == "venv":
            python_path = Path(command[2]) / "bin" / "python"
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("", encoding="utf-8")
        if command[-1] == "--version":
            return subprocess.CompletedProcess(
                command, 0, stdout="Python 3.13.0\n", stderr=""
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("n4x.runtime.environments.subprocess.run", run)

    result = manager.prepare(revision.id)
    reused = manager.prepare(revision.id)

    assert result.environment.status == "ready"
    assert result.invocation.status == "succeeded"
    assert reused.environment.id == result.environment.id
    assert reused.invocation.stdout == "reused existing Python environment"
    assert len(calls) == 3
    assert not manager.uow.is_active


def test_python_manager_persists_missing_uv_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = create_test_runtime()
    app = system.create_application("python-failure", "Python Failure")
    revision = system.create_application_revision(app.id)
    monkeypatch.setattr(
        "n4x.runtime.environments.shutil.which", lambda _: None
    )

    with pytest.raises(ValidationFailure, match="uv is required"):
        system.runtime.python_environments.prepare(revision.id)

    environments = system.uow.records.python_environments.values()
    invocations = system.uow.records.build_invocations.values()
    assert [environment.status for environment in environments] == ["failed"]
    assert [invocation.status for invocation in invocations] == ["failed"]


def test_surface_runtime_rejects_inherited_uow() -> None:
    system, surface = _surface_fixture("surface-boundary")

    with system.uow:
        with pytest.raises(
            GraphUnitOfWorkError, match="build Experience Surface artifact"
        ):
            system.surfaces.build(surface)
        assert system.uow.is_active
        assert system.uow.depth == 1


def test_surface_subprocesses_run_without_active_uow_and_own_build_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system, surface = _surface_fixture("surface-waits")
    calls: list[list[str]] = []
    _mock_surface_tools(monkeypatch, system, calls)

    result = system.build_experience_surface(surface.experience_revision_id, surface.surface_id)

    assert result.environment.status == "ready"
    assert result.build_invocation.status == "succeeded"
    assert len(calls) == 3
    assert not system.uow.is_active
    edges = system.store.list_edges(
        from_ref=node_ref("ExperienceSurface", experience_revision_id=surface.experience_revision_id, surface_id=surface.surface_id),
        edge_type="BUILDS_TO",
    )
    assert len(edges) == 1
    assert edges[0].to_ref == node_ref(
        "BuildArtifact", id=result.artifact.id
    )


def test_surface_install_failure_is_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system, surface = _surface_fixture("surface-failure")
    monkeypatch.setattr(
        "n4x.runtime.surfaces.shutil.which",
        lambda executable: f"/fake/{executable}",
    )

    def fail_install(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        assert not system.uow.is_active
        assert any(
            invocation.status == "started"
            for invocation in system.uow.records.build_invocations.values()
        )
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="install failed"
        )

    monkeypatch.setattr("n4x.runtime.surfaces.subprocess.run", fail_install)

    with pytest.raises(ValidationFailure, match="pnpm install failed"):
        system.surfaces.build(surface)

    environments = system.uow.records.javascript_environments.values()
    invocations = system.uow.records.build_invocations.values()
    assert [environment.status for environment in environments] == ["failed"]
    assert [invocation.status for invocation in invocations] == ["failed"]
    assert "install failed" in invocations[0].stderr


def _surface_fixture(slug: str):
    system = create_test_runtime()
    experience = system.create_experience(slug, slug.replace("-", " ").title())
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    system.source.write_source_file(
        revision.source_tree_id,
        "src/main.tsx",
        "document.body.textContent = 'ready';\n",
        role="surface",
        language="typescript",
    )
    surface = system.create_experience_surface(
        revision.id,
        f"{slug}.main",
        surface_type="browser",
        entrypoint="src/main.tsx",
        source_paths=["src/main.tsx"],
        config={"mount_path": "/"},
    )
    return system, surface


def _mock_surface_tools(
    monkeypatch: pytest.MonkeyPatch,
    system,
    calls: list[list[str]],
) -> None:
    monkeypatch.setattr(
        "n4x.runtime.surfaces.shutil.which",
        lambda executable: f"/fake/{executable}",
    )

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert not system.uow.is_active
        assert any(
            invocation.status == "started"
            for invocation in system.uow.records.build_invocations.values()
        )
        calls.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(
                command, 0, stdout="v24.0.0\n", stderr=""
            )
        project_path = Path(command[command.index("--dir") + 1])
        if "install" in command:
            (project_path / "pnpm-lock.yaml").write_text(
                "lockfileVersion: '9.0'\n", encoding="utf-8"
            )
        else:
            dist_path = project_path / "dist"
            dist_path.mkdir(parents=True, exist_ok=True)
            (dist_path / "index.html").write_text(
                "<main>built</main>", encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("n4x.runtime.surfaces.subprocess.run", run)
