from __future__ import annotations

import json
import posixpath
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from n4x.graph.store import GraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.hash import sha256_json, sha256_text
from n4x.kernel.models import (
    BuildArtifact,
    BuildInvocation,
    ExperienceSurface,
    JavaScriptEnvironment,
    RuntimeDependency,
    SourceFile,
    UiProfile,
    UiThemeRevision,
    now_utc,
)
from n4x.source_store.service import SourceStore

KERNEL_SURFACE_DEPENDENCIES = {
    "@vitejs/plugin-react": "^5.0.0",
    "vite": "^7.0.0",
    "typescript": "^5.8.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
}
N4X_DEFAULT_UI_DEPENDENCIES = {
    "tailwindcss": "^4.3.3",
    "@tailwindcss/vite": "^4.3.3",
}
VITE_CONFIG_NAMES = (
    "vite.config.ts",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.mts",
    "vite.config.cjs",
    "vite.config.cts",
)


class ThemeRevisionProvider(Protocol):
    def active_theme_revision(self) -> UiThemeRevision: ...


@dataclass(frozen=True)
class SurfaceBuildResult:
    environment: JavaScriptEnvironment
    build_invocation: BuildInvocation
    artifact: BuildArtifact


@dataclass(frozen=True)
class SurfaceUiContext:
    profile: UiProfile
    theme_revision: UiThemeRevision | None = None


class ExperienceSurfaceRuntime:
    def __init__(
        self,
        source: SourceStore,
        runtime_root: Path,
        graph_store: GraphStore,
        uow: GraphUnitOfWork | None = None,
        theme_provider: ThemeRevisionProvider | None = None,
    ) -> None:
        self.source = source
        self.runtime_root = runtime_root
        self.store = graph_store
        self.uow = uow or GraphUnitOfWork(graph_store)
        self.graph = self.uow.records
        self.theme_provider = theme_provider

    def build(self, surface: ExperienceSurface) -> SurfaceBuildResult:
        self.uow.require_inactive("build Experience Surface artifact")
        experience_revision_id = surface.experience_revision_id
        with self.uow:
            dependencies = self._javascript_dependencies(experience_revision_id)
            ui_context = self._ui_context(experience_revision_id)
            source_files = [
                self.source.read_source_file(
                    surface.source_tree_id, source_path
                )
                for source_path in surface.source_paths
            ]
            input_hash = self._build_fingerprint(
                surface,
                dependencies,
                ui_context,
                source_hashes=[file.content_hash for file in source_files],
            )

        project_path = self.runtime_root / "surfaces" / _safe_path_hash(input_hash)[:24]
        dist_path = project_path / "dist"
        environment_id = (
            f"{experience_revision_id}.javascript.{input_hash[:16]}"
        )
        pnpm = shutil.which("pnpm")
        install_command = [
            pnpm or "pnpm",
            "install",
            "--dir",
            str(project_path),
        ]
        env_invocation = BuildInvocation(
            id=str(uuid.uuid4()),
            owner_kind="ExperienceRevision",
            owner_id=experience_revision_id,
            kind="javascript_env",
            status="started",
            input_hash=input_hash,
            command=install_command,
            cwd=str(project_path),
        )
        pending_environment = JavaScriptEnvironment(
            id=environment_id,
            owner_kind="ExperienceRevision",
            owner_id=experience_revision_id,
            dependency_ids=[dependency.id for dependency in dependencies],
            install_path=str(project_path),
            lock_hash=input_hash,
            status="pending",
        )
        with self.uow:
            self.graph.build_invocations[env_invocation.id] = env_invocation
            self._link_build_invocation(
                experience_revision_id, env_invocation.id
            )
            self.graph.javascript_environments[pending_environment.id] = (
                pending_environment
            )
            self._link_environment(
                experience_revision_id, pending_environment.id
            )

        install_process: subprocess.CompletedProcess[str] | None = None
        try:
            if pnpm is None:
                raise ValidationFailure("pnpm is required for Surface builds")
            source_hashes = self._materialize_surface_source(
                source_files, project_path
            )
            package_json = self._write_package_json(
                project_path, dependencies, ui_context.profile
            )
            self._write_shared_theme_file(project_path, ui_context)
            self._write_host_files(
                surface, project_path, ui_context.profile
            )
            self.uow.require_inactive("run pnpm install")
            install_process = subprocess.run(
                install_command,
                capture_output=True,
                text=True,
                check=False,
                timeout=240,
            )
            if install_process.returncode != 0:
                detail = install_process.stderr.strip()
                raise ValidationFailure(
                    "pnpm install failed with exit code "
                    f"{install_process.returncode}"
                    + (f": {detail}" if detail else "")
                )
            node_version = self._node_version()
            lock_path = project_path / "pnpm-lock.yaml"
            lock_text = (
                lock_path.read_text(encoding="utf-8")
                if lock_path.exists()
                else ""
            )
            environment = pending_environment.model_copy(
                update={
                    "node_version": node_version,
                    "lock_hash": sha256_text(lock_text),
                    "lock_metadata": {
                        "package_json": package_json,
                        "pnpm_lock_hash": sha256_text(lock_text),
                        "source_hashes": source_hashes,
                        **_ui_build_metadata(ui_context),
                    },
                    "status": "ready",
                    "last_resolved_at": now_utc(),
                }
            )
            completed_env_invocation = env_invocation.model_copy(
                update={
                    "status": "succeeded",
                    "stdout": install_process.stdout,
                    "stderr": install_process.stderr,
                    "completed_at": now_utc(),
                }
            )
        except Exception as exc:
            failed_environment = pending_environment.model_copy(
                update={
                    "status": "failed",
                    "last_resolved_at": now_utc(),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            failed_invocation = env_invocation.model_copy(
                update={
                    "status": "failed",
                    "stdout": (
                        "" if install_process is None else install_process.stdout
                    ),
                    "stderr": (
                        "" if install_process is None else install_process.stderr
                    ),
                    "error": f"{type(exc).__name__}: {exc}",
                    "completed_at": now_utc(),
                }
            )
            with self.uow:
                self.graph.javascript_environments[
                    failed_environment.id
                ] = failed_environment
                self._link_environment(
                    experience_revision_id, failed_environment.id
                )
                self.graph.build_invocations[
                    failed_invocation.id
                ] = failed_invocation
            if isinstance(exc, ValidationFailure):
                raise
            raise ValidationFailure(
                f"JavaScript dependency resolution failed: {exc}"
            ) from exc

        with self.uow:
            self.graph.javascript_environments[environment.id] = environment
            self._link_environment(experience_revision_id, environment.id)
            self.graph.build_invocations[
                completed_env_invocation.id
            ] = completed_env_invocation

        build_command = [
            pnpm,
            "--dir",
            str(project_path),
            "exec",
            "vite",
            "build",
        ]
        build_invocation = BuildInvocation(
            id=str(uuid.uuid4()),
            owner_kind="ExperienceRevision",
            owner_id=experience_revision_id,
            kind="surface_build",
            status="started",
            input_hash=input_hash,
            command=build_command,
            cwd=str(project_path),
        )
        with self.uow:
            self.graph.build_invocations[build_invocation.id] = build_invocation
            self._link_build_invocation(
                experience_revision_id, build_invocation.id
            )

        build_process: subprocess.CompletedProcess[str] | None = None
        try:
            self.uow.require_inactive("run Vite build")
            build_process = subprocess.run(
                build_command,
                capture_output=True,
                text=True,
                check=False,
                timeout=240,
            )
            if build_process.returncode != 0:
                detail = build_process.stderr.strip()
                raise ValidationFailure(
                    f"vite build failed with exit code {build_process.returncode}"
                    + (f": {detail}" if detail else "")
                )
            manifest = self._build_manifest(surface, dist_path)
            artifact = BuildArtifact(
                id=str(uuid.uuid4()),
                owner_kind="ExperienceRevision",
                owner_id=experience_revision_id,
                build_invocation_id=build_invocation.id,
                artifact_type="surface_bundle",
                path=str(dist_path),
                content_hash=self._artifact_hash(dist_path),
                surface_id=surface.surface_id,
                surface_type=surface.surface_type,
                input_hash=input_hash,
                manifest=manifest,
                metadata={
                    "input_hash": input_hash,
                    "entrypoint": surface.entrypoint,
                    "standalone_index": manifest.get("index")
                    or manifest.get("html"),
                    "host_base": "relative",
                    **_ui_build_metadata(ui_context),
                },
            )
            completed_build_invocation = build_invocation.model_copy(
                update={
                    "status": "succeeded",
                    "stdout": build_process.stdout,
                    "stderr": build_process.stderr,
                    "completed_at": now_utc(),
                }
            )
        except Exception as exc:
            failed_invocation = build_invocation.model_copy(
                update={
                    "status": "failed",
                    "stdout": (
                        "" if build_process is None else build_process.stdout
                    ),
                    "stderr": (
                        "" if build_process is None else build_process.stderr
                    ),
                    "error": f"{type(exc).__name__}: {exc}",
                    "completed_at": now_utc(),
                }
            )
            with self.uow:
                self.graph.build_invocations[
                    failed_invocation.id
                ] = failed_invocation
            if isinstance(exc, ValidationFailure):
                raise
            raise ValidationFailure(f"Surface build failed: {exc}") from exc

        with self.uow:
            self.graph.javascript_environments[environment.id] = environment
            self.graph.build_invocations[
                completed_env_invocation.id
            ] = completed_env_invocation
            self.graph.build_invocations[
                completed_build_invocation.id
            ] = completed_build_invocation
            self.graph.build_artifacts[artifact.id] = artifact
            self._link_environment(experience_revision_id, environment.id)
            self.store.create_edge(
                node_ref(artifact.owner_kind, id=artifact.owner_id),
                "HAS_BUILD_ARTIFACT",
                node_ref("BuildArtifact", id=artifact.id),
            )
            self.store.create_edge(
                node_ref(
                    "ExperienceSurface",
                    experience_revision_id=surface.experience_revision_id,
                    surface_id=surface.surface_id,
                ),
                "BUILDS_TO",
                node_ref("BuildArtifact", id=artifact.id),
            )
            self.store.create_edge(
                node_ref("BuildInvocation", id=completed_build_invocation.id),
                "PRODUCED",
                node_ref("BuildArtifact", id=artifact.id),
            )
        return SurfaceBuildResult(
            environment=environment,
            build_invocation=completed_build_invocation,
            artifact=artifact,
        )

    def _build_manifest(
        self, surface: ExperienceSurface, dist_path: Path
    ) -> dict[str, str]:
        index = dist_path / "index.html"
        if not index.is_file():
            raise ValidationFailure("Surface build did not produce dist/index.html")
        if surface.surface_type == "browser":
            return {"root": str(dist_path), "index": str(index)}
        if surface.surface_type == "mcp_app":
            html_path = dist_path / "mcp-app.html"
            html_path.write_text(
                self._self_contained_html(index, dist_path),
                encoding="utf-8",
            )
            return {"html": str(html_path)}
        raise ValidationFailure(
            f"unsupported Surface builder: {surface.surface_type}"
            f"@{surface.surface_type_version}"
        )

    def _self_contained_html(self, index: Path, dist_path: Path) -> str:
        html = index.read_text(encoding="utf-8")

        def inline_script(match: re.Match[str]) -> str:
            before, source, after = match.groups()
            asset = self._dist_asset(dist_path, source)
            return (
                f"<script{before}{after}>"
                f"{asset.read_text(encoding='utf-8')}</script>"
            )

        def inline_style(match: re.Match[str]) -> str:
            before, source, after = match.groups()
            asset = self._dist_asset(dist_path, source)
            return (
                f"<style{before}{after}>"
                f"{asset.read_text(encoding='utf-8')}</style>"
            )

        html = re.sub(
            r"<script([^>]*?)\s+src=[\"']([^\"']+)[\"']([^>]*)></script>",
            inline_script,
            html,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"<link([^>]*?)\s+href=[\"']([^\"']+)[\"']([^>]*?)>",
            lambda match: (
                inline_style(
                    match
                )
                if re.search(
                    r"\brel=[\"']stylesheet[\"']",
                    f"{match.group(1)} {match.group(3)}",
                    flags=re.IGNORECASE,
                )
                else match.group(0)
            ),
            html,
            flags=re.IGNORECASE,
        )

    def _dist_asset(self, dist_path: Path, reference: str) -> Path:
        clean = reference.split("?", 1)[0].split("#", 1)[0]
        if "://" in clean or clean.startswith("//"):
            raise ValidationFailure(
                "mcp_app Surface output must not reference remote assets"
            )
        asset = (dist_path / clean.lstrip("/")).resolve()
        root = dist_path.resolve()
        if root not in asset.parents or not asset.is_file():
            raise ValidationFailure(
                f"mcp_app Surface asset is missing or outside dist: {reference}"
            )
        return asset

    def _materialize_surface_source(
        self, source_files: list[SourceFile], project_path: Path
    ) -> list[dict[str, str]]:
        project_path.mkdir(parents=True, exist_ok=True)
        source_hashes = []
        for file in source_files:
            destination = project_path / file.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(file.content, encoding="utf-8")
            source_hashes.append({"path": file.path, "hash": file.content_hash})
        return source_hashes

    def _write_package_json(
        self,
        project_path: Path,
        dependencies: list[RuntimeDependency],
        ui_profile: UiProfile,
    ) -> dict[str, object]:
        package_dependencies = dict(KERNEL_SURFACE_DEPENDENCIES)
        for dependency in dependencies:
            package_dependencies[dependency.package] = dependency.spec or "latest"
        if ui_profile == "n4x-default":
            package_dependencies.update(N4X_DEFAULT_UI_DEPENDENCIES)
        package_json: dict[str, object] = {
            "private": True,
            "type": "module",
            "scripts": {"build": "vite build --host 127.0.0.1"},
            "dependencies": package_dependencies,
            "devDependencies": {},
        }
        (project_path / "package.json").write_text(
            json.dumps(package_json, indent=2), encoding="utf-8"
        )
        (project_path / "pnpm-workspace.yaml").write_text(
            "dangerouslyAllowAllBuilds: true\n",
            encoding="utf-8",
        )
        return package_json

    def _write_host_files(
        self,
        surface: ExperienceSurface,
        project_path: Path,
        ui_profile: UiProfile,
    ) -> None:
        index = project_path / "index.html"
        if not index.exists():
            index.write_text(
                '<!doctype html>\n<html><head><meta charset="UTF-8" />'
                '<meta name="viewport" content="width=device-width, initial-scale=1.0" />'
                '</head><body><div id="root"></div>'
                f'<script type="module" src="/{surface.entrypoint}"></script>'
                "</body></html>\n",
                encoding="utf-8",
            )
        vite_configs = [
            project_path / name
            for name in VITE_CONFIG_NAMES
            if (project_path / name).exists()
        ]
        if len(vite_configs) > 1:
            names = ", ".join(path.name for path in vite_configs)
            raise ValidationFailure(
                f"Surface source contains multiple Vite configs: {names}"
            )
        if not vite_configs:
            vite_config = project_path / "vite.config.ts"
            plugin_import = (
                "import tailwindcss from '@tailwindcss/vite';\n"
                if ui_profile == "n4x-default"
                else ""
            )
            plugins = (
                "react(), tailwindcss()"
                if ui_profile == "n4x-default"
                else "react()"
            )
            vite_config.write_text(
                "import { defineConfig } from 'vite';\n"
                "import react from '@vitejs/plugin-react';\n"
                f"{plugin_import}\n"
                "export default defineConfig({\n"
                "  base: './',\n"
                f"  plugins: [{plugins}],\n"
                "});\n",
                encoding="utf-8",
            )
        elif ui_profile == "n4x-default":
            self._validate_tailwind_vite_config(vite_configs[0])
        if ui_profile == "n4x-default":
            self._validate_theme_import(surface, project_path)

    def _write_shared_theme_file(
        self, project_path: Path, ui_context: SurfaceUiContext
    ) -> None:
        if ui_context.profile != "n4x-default":
            return
        if ui_context.theme_revision is None:
            raise ValidationFailure(
                "default N4X UI profile requires an active graph theme revision"
            )
        src = project_path / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "n4x-theme.css").write_text(
            ui_context.theme_revision.css_text, encoding="utf-8"
        )

    def _ui_context(self, experience_revision_id: str) -> SurfaceUiContext:
        revision = self.graph.experience_revisions[experience_revision_id]
        if revision.ui_profile != "n4x-default":
            return SurfaceUiContext(profile=revision.ui_profile)
        if self.theme_provider is None:
            raise ValidationFailure(
                "default N4X UI profile requires a graph theme provider"
            )
        theme_revision = self.theme_provider.active_theme_revision()
        actual_hash = sha256_text(theme_revision.css_text)
        if actual_hash != theme_revision.content_hash:
            raise ValidationFailure(
                "active UI theme revision content does not match its content hash: "
                f"{theme_revision.id}"
            )
        return SurfaceUiContext(
            profile=revision.ui_profile,
            theme_revision=theme_revision,
        )

    def _validate_tailwind_vite_config(self, config_path: Path) -> None:
        content = config_path.read_text(encoding="utf-8")
        imported = re.search(
            r"""import\s+([A-Za-z_$][\w$]*)\s+from\s+"""
            r"""["']@tailwindcss/vite["']""",
            content,
        )
        if imported is None:
            raise ValidationFailure(
                "default N4X UI profile requires the app-supplied Vite config "
                f"{config_path.name!r} to import @tailwindcss/vite"
            )
        plugin_name = imported.group(1)
        if re.search(rf"\b{re.escape(plugin_name)}\s*\(", content) is None:
            raise ValidationFailure(
                "default N4X UI profile requires the app-supplied Vite config "
                f"{config_path.name!r} to include {plugin_name}() in plugins"
            )

    def _validate_theme_import(
        self, surface: ExperienceSurface, project_path: Path
    ) -> None:
        target = "src/n4x-theme.css"
        entrypoint = project_path / surface.entrypoint
        if entrypoint.exists() and _imports_project_path(
            entrypoint.read_text(encoding="utf-8"),
            surface.entrypoint,
            target,
        ):
            return
        index = project_path / "index.html"
        if index.exists() and _imports_project_path(
            index.read_text(encoding="utf-8"), "index.html", target
        ):
            return
        raise ValidationFailure(
            "default N4X UI profile requires src/n4x-theme.css to be imported "
            f"by Surface entrypoint {surface.entrypoint!r} or custom index.html"
        )

    def _javascript_dependencies(
        self, experience_revision_id: str
    ) -> list[RuntimeDependency]:
        dependencies = [
            dependency
            for dependency in self.graph.runtime_dependencies.values()
            if dependency.owner_kind == "ExperienceRevision"
            and dependency.owner_id == experience_revision_id
            and dependency.ecosystem == "javascript"
        ]
        return sorted(
            dependencies,
            key=lambda dependency: (dependency.package, dependency.spec, dependency.id),
        )

    def build_input_hash(self, surface: ExperienceSurface) -> str:
        with self.uow:
            ui_context = self._ui_context(
                surface.experience_revision_id
            )
            return self._build_fingerprint(
                surface,
                self._javascript_dependencies(
                    surface.experience_revision_id
                ),
                ui_context,
            )

    def _build_fingerprint(
        self,
        surface: ExperienceSurface,
        dependencies: list[RuntimeDependency],
        ui_context: SurfaceUiContext,
        *,
        source_hashes: list[str] | list[dict[str, str]] | None = None,
    ) -> str:
        if source_hashes is None:
            source_hashes = [
                self.source.read_source_file(
                    surface.source_tree_id, path
                ).content_hash
                for path in surface.source_paths
            ]
        return sha256_json(
            {
                "experience_revision_id": surface.experience_revision_id,
                "surface_id": surface.surface_id,
                "surface_type": surface.surface_type,
                "surface_type_version": surface.surface_type_version,
                "entrypoint": surface.entrypoint,
                "source_hashes": source_hashes,
                "kernel_dependencies": KERNEL_SURFACE_DEPENDENCIES,
                "ui_profile": ui_context.profile,
                "managed_ui_dependencies": (
                    N4X_DEFAULT_UI_DEPENDENCIES
                    if ui_context.profile == "n4x-default"
                    else {}
                ),
                "ui_theme_revision": (
                    None
                    if ui_context.theme_revision is None
                    else {
                        "id": ui_context.theme_revision.id,
                        "release_version": (
                            ui_context.theme_revision.release_version
                        ),
                        "content_hash": ui_context.theme_revision.content_hash,
                    }
                ),
                "dependencies": [
                    {
                        "id": dependency.id,
                        "package": dependency.package,
                        "spec": dependency.spec,
                    }
                    for dependency in dependencies
                ],
                "config": surface.config,
            }
        )

    def _artifact_hash(self, path: Path) -> str:
        files = []
        for file in sorted(path.rglob("*")):
            if file.is_file():
                files.append(
                    {
                        "path": str(file.relative_to(path)),
                        "hash": sha256_text(file.read_text(errors="ignore")),
                    }
                )
        return sha256_json(files)

    def _node_version(self) -> str | None:
        node = shutil.which("node")
        if node is None:
            return None
        self.uow.require_inactive("read Node.js version")
        process = subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=False, timeout=30
        )
        return (process.stdout or process.stderr).strip()

    def _link_environment(
        self, experience_revision_id: str, environment_id: str
    ) -> None:
        self.store.create_edge(
            node_ref("ExperienceRevision", id=experience_revision_id),
            "USES_JAVASCRIPT_ENV",
            node_ref("JavaScriptEnvironment", id=environment_id),
        )

    def _link_build_invocation(
        self, experience_revision_id: str, invocation_id: str
    ) -> None:
        self.store.create_edge(
            node_ref("ExperienceRevision", id=experience_revision_id),
            "HAS_BUILD_INVOCATION",
            node_ref("BuildInvocation", id=invocation_id),
        )


def _safe_path_hash(value: str) -> str:
    return value.replace(":", "-")


def _ui_build_metadata(ui_context: SurfaceUiContext) -> dict[str, str | None]:
    theme = ui_context.theme_revision
    return {
        "ui_profile": ui_context.profile,
        "ui_theme_revision_id": None if theme is None else theme.id,
        "ui_theme_content_hash": None if theme is None else theme.content_hash,
    }


def _imports_project_path(
    content: str, importer_path: str, target_path: str
) -> bool:
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    content = re.sub(r"(?m)^\s*//.*$", "", content)
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    import_specs = re.findall(
        r"""(?:\bimport\s+(?:[^'"]*?\s+from\s+)?|@import\s+)"""
        r"""["']([^"']+)["']""",
        content,
    )
    html_specs = re.findall(
        r"""\b(?:href|src)\s*=\s*["']([^"']+)["']""",
        content,
        flags=re.IGNORECASE,
    )
    for spec in [*import_specs, *html_specs]:
        clean_spec = spec.split("?", 1)[0].split("#", 1)[0]
        if clean_spec.startswith("/"):
            resolved = posixpath.normpath(clean_spec.lstrip("/"))
        elif clean_spec.startswith("."):
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(importer_path), clean_spec)
            )
        elif importer_path == "index.html":
            resolved = posixpath.normpath(clean_spec)
        else:
            continue
        if resolved == target_path:
            return True
    return False
