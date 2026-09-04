from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.messages import MessageHandler
from mcp.types import ResourceListChangedNotification, ToolListChangedNotification

from n4x.kernel.errors import ValidationFailure
from n4x.system.mcp import create_system_mcp
from n4x.testing import create_test_runtime


def test_surface_crud_is_draft_only() -> None:
    system = create_test_runtime()
    experience = system.create_experience("surface-crud", "Surface CRUD")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _write_source(system, revision.source_tree_id, "src/main.ts", "ready")

    created = system.create_experience_surface(
        revision.id,
        "main",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/main"},
    )
    updated = system.update_experience_surface(
        revision.id,
        created.surface_id,
        title="Main",
        config={"mount_path": "/updated"},
    )

    assert system.inspect_experience_surface(revision.id, "main") == updated
    assert [item.surface_id for item in system.list_experience_surfaces(revision.id)] == [
        "main"
    ]
    assert updated.title == "Main"
    assert updated.config["mount_path"] == "/updated"

    with system.uow:
        system.graph.experience_revisions.save(
            revision.model_copy(update={"status": "active"})
        )
    with pytest.raises(ValueError, match="not draft"):
        system.update_experience_surface(revision.id, "main", title="Forbidden")
    with pytest.raises(ValueError, match="not draft"):
        system.delete_experience_surface(revision.id, "main")


def test_child_experience_revision_clones_surface_declarations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = create_test_runtime()
    _install_fake_toolchain(monkeypatch)
    experience = system.create_experience("surface-clone", "Surface Clone")
    first = system.create_experience_revision(experience.id, ui_profile="none")
    _write_source(system, first.source_tree_id, "src/main.ts", "ready")
    system.create_experience_surface(
        first.id,
        "main",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/main"},
    )
    system.activate_experience_revision(first.id)

    child = system.create_experience_revision(experience.id)
    cloned = system.inspect_experience_surface(child.id, "main")

    assert cloned.experience_revision_id == child.id
    assert cloned.source_tree_id == child.source_tree_id
    assert cloned.created_by == "revision_clone"
    assert cloned.config == {"mount_path": "/main", "build_profile": {}, "fallback": None, "csp": {"connect_domains": [], "resource_domains": []}, "host_metadata": {}}


def test_surface_build_persists_exact_authority_and_self_contained_mcp_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = create_test_runtime()
    _install_fake_toolchain(monkeypatch, with_assets=True)
    experience = system.create_experience("surface-build", "Surface Build")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _write_source(system, revision.source_tree_id, "src/main.ts", "ready")
    surface = system.create_experience_surface(
        revision.id,
        "assistant",
        surface_type="mcp_app",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={},
    )

    result = system.build_experience_surface(revision.id, surface.surface_id)
    artifact = result.artifact
    html = Path(artifact.manifest["html"]).read_text(encoding="utf-8")

    assert artifact.surface_id == surface.surface_id
    assert artifact.surface_type == "mcp_app"
    assert artifact.input_hash == result.build_invocation.input_hash
    assert system.resolve_experience_surface_artifact(
        revision.id, surface.surface_id, artifact.input_hash
    ) == artifact
    assert "<script type=\"module\">" in html
    assert "console.log('built')" in html
    assert "<style rel=\"stylesheet\">" in html
    assert ".built" in html
    assert "src=" not in html
    assert "href=" not in html


def test_default_experience_build_materializes_active_theme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized_themes: list[str] = []
    system = create_test_runtime()
    _install_fake_toolchain(
        monkeypatch,
        observed_themes=materialized_themes,
    )
    experience = system.create_experience("default-theme", "Default Theme")
    revision = system.create_experience_revision(experience.id)
    _write_source(
        system,
        revision.source_tree_id,
        "src/main.ts",
        "import './n4x-theme.css';",
    )
    surface = system.create_experience_surface(
        revision.id,
        "main",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"mount_path": "/"},
    )

    result = system.build_experience_surface(revision.id, surface.surface_id)

    assert materialized_themes == [system.inspect_surface_theme()["css_text"]]
    assert "--background: oklch(0.9881 0 0);" in materialized_themes[0]
    assert result.build_invocation.input_hash == system.surfaces.build_input_hash(
        surface
    )


def test_surface_build_failure_does_not_activate_experience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = create_test_runtime()
    _install_fake_toolchain(monkeypatch, fail_source="FAIL_BUILD")
    experience = system.create_experience("surface-atomic", "Surface Atomic")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _write_source(system, revision.source_tree_id, "src/good.ts", "ready")
    _write_source(system, revision.source_tree_id, "src/bad.ts", "FAIL_BUILD")
    for surface_id, source in (("a-good", "src/good.ts"), ("z-bad", "src/bad.ts")):
        system.create_experience_surface(
            revision.id,
            surface_id,
            surface_type="browser",
            entrypoint=source,
            source_paths=[source],
            config={"mount_path": f"/{surface_id}"},
        )

    with pytest.raises(ValidationFailure, match="vite build failed"):
        system.activate_experience_revision(revision.id)

    assert system.graph.experiences[experience.id].active_revision_id is None
    assert system.graph.experience_revisions[revision.id].status == "rejected"
    assert all(
        edge.type != "ACTIVE_REVISION"
        for edge in system.store.list_edges()
        if edge.from_ref.label in {"Experience", "ExperienceSurface"}
    )


def test_pwa_validate_requires_manifest_source() -> None:
    system = create_test_runtime()
    experience = system.create_experience("pwa-validate", "PWA Validate")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _write_source(system, revision.source_tree_id, "src/main.ts", "ready")
    system.create_experience_surface(
        revision.id,
        "browser",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
        config={"pwa": {}},
    )

    failed = system.validate_experience_revision(revision.id)
    assert failed.status == "failed"
    assert any("PWA requires SourceFile" in error for error in failed.errors)


def test_pwa_activation_requires_json_manifest_in_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = create_test_runtime()
    _install_fake_toolchain(monkeypatch)
    experience = system.create_experience("pwa-activate", "PWA Activate")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _write_source(system, revision.source_tree_id, "src/main.ts", "ready")
    _write_source(
        system,
        revision.source_tree_id,
        "manifest.webmanifest",
        json.dumps({"name": "PWA Activate", "display": "standalone"}),
    )
    system.create_experience_surface(
        revision.id,
        "browser",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts", "manifest.webmanifest"],
        config={"pwa": {}},
    )

    passed = system.validate_experience_revision(revision.id)
    assert passed.status == "passed"

    with pytest.raises(ValidationFailure, match="PWA manifest"):
        system.activate_experience_revision(revision.id)

    assert system.graph.experiences[experience.id].active_revision_id is None
    assert all(
        edge.type != "ACTIVE_REVISION"
        for edge in system.store.list_edges()
        if edge.from_ref.label in {"Experience", "ExperienceSurface"}
    )


def test_pwa_activation_succeeds_when_manifest_is_in_dist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = create_test_runtime()
    _install_fake_toolchain(monkeypatch)
    experience = system.create_experience("pwa-ready", "PWA Ready")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _write_source(system, revision.source_tree_id, "src/main.ts", "ready")
    _write_source(
        system,
        revision.source_tree_id,
        "public/manifest.webmanifest",
        json.dumps({"name": "PWA Ready", "display": "standalone"}),
    )
    system.create_experience_surface(
        revision.id,
        "browser",
        surface_type="browser",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts", "public/manifest.webmanifest"],
        config={"pwa": {}},
    )

    activated = system.activate_experience_revision(revision.id)

    assert activated.status == "active"
    assert system.graph.experiences[experience.id].active_revision_id == revision.id


def test_blueprint_and_mcp_author_canonical_surfaces() -> None:
    system = create_test_runtime()
    blueprint = system.create_blueprint("surface-blueprint", "Surface Blueprint")
    blueprint_revision = system.create_blueprint_revision(
        blueprint.id,
        instructions="Create a browser Surface.",
        content={
            "experiences": [
                {
                    "id": "blueprint-ui",
                    "ui_profile": "none",
                    "source_files": [
                        {
                            "path": "src/main.ts",
                            "content": "ready",
                            "role": "surface",
                            "language": "typescript",
                        }
                    ],
                    "surfaces": [
                        {
                            "id": "main",
                            "surface_type": "browser",
                            "entrypoint": "src/main.ts",
                            "source_paths": ["src/main.ts"],
                            "config": {"mount_path": "/main"},
                        }
                    ],
                }
            ]
        },
    )
    instantiated = system.instantiate_blueprint(
        blueprint_revision.id, application_id="blueprint-backend"
    )
    result = instantiated["experiences"]["blueprint-ui"]
    assert result["surface_ids"] == ["main"]
    assert system.inspect_experience_surface(
        result["experience_revision_id"], "main"
    ).surface_type == "browser"

    asyncio.run(_assert_mcp_surface_crud(system))


def test_mcp_activation_notifies_calling_session_of_surface_catalog_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_toolchain(monkeypatch)
    asyncio.run(_assert_mcp_activation_notifications())


async def _assert_mcp_activation_notifications() -> None:
    system = create_test_runtime()
    experience = system.create_experience("notify-surface", "Notify Surface")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _write_source(system, revision.source_tree_id, "src/main.ts", "ready")
    system.create_experience_surface(
        revision.id,
        "assistant",
        surface_type="mcp_app",
        entrypoint="src/main.ts",
        source_paths=["src/main.ts"],
    )
    received: list[str] = []

    class Handler(MessageHandler):
        async def on_resource_list_changed(
            self, message: ResourceListChangedNotification
        ) -> None:
            received.append(message.method)

        async def on_tool_list_changed(
            self, message: ToolListChangedNotification
        ) -> None:
            received.append(message.method)

    async with Client(
        create_system_mcp(system), message_handler=Handler()
    ) as client:
        result = await client.call_tool(
            "activate_experience_revision",
            {"experience_revision_id": revision.id},
        )

    assert result.structured_content["status"] == "active"
    assert received == [
        "notifications/resources/list_changed",
        "notifications/tools/list_changed",
        "notifications/resources/list_changed",
        "notifications/tools/list_changed",
    ]


async def _assert_mcp_surface_crud(system) -> None:
    experience = system.create_experience("mcp-surface", "MCP Surface")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    _write_source(system, revision.source_tree_id, "src/main.ts", "ready")
    server = create_system_mcp(system)
    created = await server.call_tool(
        "create_experience_surface",
        {
            "experience_revision_id": revision.id,
            "surface_id": "main",
            "surface_type": "browser",
            "entrypoint": "src/main.ts",
            "source_paths": ["src/main.ts"],
            "config": {"mount_path": "/main"},
        },
    )
    listed = await server.call_tool(
        "list_experience_surfaces",
        {"experience_revision_id": revision.id},
    )
    deleted = await server.call_tool(
        "delete_experience_surface",
        {
            "experience_revision_id": revision.id,
            "surface_id": "main",
        },
    )

    assert created.structured_content["surface_id"] == "main"
    assert listed.structured_content["result"][0]["surface_id"] == "main"
    assert deleted.structured_content["deleted"] is True


def _write_source(system, tree_id: str, path: str, content: str) -> None:
    system.source.write_source_file(
        tree_id,
        path,
        content,
        role="surface",
        language="typescript",
    )


def _install_fake_toolchain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_assets: bool = False,
    fail_source: str | None = None,
    observed_themes: list[str] | None = None,
) -> None:
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
            theme_path = project / "src" / "n4x-theme.css"
            if observed_themes is not None and theme_path.exists():
                observed_themes.append(theme_path.read_text(encoding="utf-8"))
            (project / "pnpm-lock.yaml").write_text(
                "lockfileVersion: '9.0'\n", encoding="utf-8"
            )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if fail_source is not None and any(
            fail_source in path.read_text(encoding="utf-8")
            for path in (project / "src").glob("*")
            if path.is_file()
        ):
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="intentional failure"
            )
        dist = project / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        public = project / "public"
        if public.is_dir():
            shutil.copytree(public, dist, dirs_exist_ok=True)
        if with_assets:
            assets = dist / "assets"
            assets.mkdir()
            (assets / "app.js").write_text(
                "console.log('built')", encoding="utf-8"
            )
            (assets / "app.css").write_text(".built {}", encoding="utf-8")
            index = (
                '<link rel="stylesheet" href="/assets/app.css">'
                '<script type="module" src="/assets/app.js"></script>'
            )
        else:
            index = "<main>built</main>"
        (dist / "index.html").write_text(index, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("n4x.runtime.surfaces.subprocess.run", run)
