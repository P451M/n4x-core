from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from n4x.graph.store import GraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.hash import sha256_json, sha256_text
from n4x.kernel.models import (
    BuildArtifact,
    BuildInvocation,
    PythonEnvironment,
    RuntimeDependency,
    now_utc,
)


@dataclass(frozen=True)
class PythonEnvironmentResult:
    environment: PythonEnvironment
    invocation: BuildInvocation

    @property
    def python_executable(self) -> Path:
        return Path(self.environment.env_path) / "bin" / "python"


class PythonEnvironmentManager:
    def __init__(
        self,
        runtime_root: Path,
        graph_store: GraphStore,
        uow: GraphUnitOfWork | None = None,
    ) -> None:
        self.runtime_root = runtime_root
        self.store = graph_store
        self.uow = uow or GraphUnitOfWork(graph_store)
        self.graph = self.uow.records
        self._prepare_lock = threading.Lock()

    def prepare(self, application_revision_id: str) -> PythonEnvironmentResult:
        with self._prepare_lock:
            return self._prepare_locked(application_revision_id)

    def _prepare_locked(
        self, application_revision_id: str
    ) -> PythonEnvironmentResult:
        self.uow.require_inactive("prepare Python environment")
        with self.uow:
            dependencies = self._python_dependencies(application_revision_id)
            input_hash = self._dependency_fingerprint(dependencies)
            env_id = f"{application_revision_id}.python.{input_hash[:16]}"
            existing = self.graph.python_environments.get(env_id)

        if (
            existing is not None
            and existing.status == "ready"
            and Path(existing.env_path, "bin", "python").exists()
        ):
            with self.uow:
                current = self.graph.python_environments.get(env_id)
                if current is not None and current.status == "ready":
                    invocation = self._record_invocation(
                        application_revision_id=application_revision_id,
                        input_hash=input_hash,
                        status="succeeded",
                        command=[],
                        stdout="reused existing Python environment",
                    )
                    self._link_environment(application_revision_id, current.id)
                    return PythonEnvironmentResult(current, invocation)

        env_path = self.runtime_root / "python-envs" / env_id
        uv = shutil.which("uv")
        command = [uv or "uv", "venv", str(env_path), "--python", sys.executable]
        if env_path.exists():
            command.append("--clear")
        invocation = BuildInvocation(
            id=str(uuid.uuid4()),
            owner_kind="ApplicationRevision",
            owner_id=application_revision_id,
            kind="python_env",
            status="started",
            input_hash=input_hash,
            command=command,
            cwd=str(self.runtime_root),
            started_at=now_utc(),
        )
        pending_environment = PythonEnvironment(
            id=env_id,
            application_revision_id=application_revision_id,
            dependency_ids=[dependency.id for dependency in dependencies],
            env_path=str(env_path),
            python_version="",
            lock_hash=input_hash,
            status="pending",
        )
        with self.uow:
            self.graph.build_invocations[invocation.id] = invocation
            self._link_build_invocation(application_revision_id, invocation.id)
            self.graph.python_environments[pending_environment.id] = (
                pending_environment
            )
            self._link_environment(
                application_revision_id, pending_environment.id
            )

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        try:
            if uv is None:
                raise ValidationFailure(
                    "uv is required for Python environment resolution"
                )
            env_path.parent.mkdir(parents=True, exist_ok=True)
            self.uow.require_inactive("run uv venv")
            create = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=120
            )
            stdout_parts.append(create.stdout)
            stderr_parts.append(create.stderr)
            if create.returncode != 0:
                raise ValidationFailure(
                    f"uv venv failed with exit code {create.returncode}"
                )

            install_args = self._requirement_args(dependencies)
            if install_args:
                install_command = [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(env_path / "bin" / "python"),
                    *install_args,
                ]
                self.uow.require_inactive("run uv pip install")
                install = subprocess.run(
                    install_command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=180,
                )
                stdout_parts.append(install.stdout)
                stderr_parts.append(install.stderr)
                command = install_command
                if install.returncode != 0:
                    raise ValidationFailure(
                        f"uv pip install failed with exit code {install.returncode}"
                    )

            freeze_command = [
                uv,
                "pip",
                "freeze",
                "--python",
                str(env_path / "bin" / "python"),
            ]
            self.uow.require_inactive("run uv pip freeze")
            freeze = subprocess.run(
                freeze_command, capture_output=True, text=True, check=False, timeout=60
            )
            stdout_parts.append(freeze.stdout)
            stderr_parts.append(freeze.stderr)
            if freeze.returncode != 0:
                raise ValidationFailure(
                    f"uv pip freeze failed with exit code {freeze.returncode}"
                )

            python_version = self._python_version(env_path / "bin" / "python")
            lock_metadata = {
                "requirements": install_args,
                "freeze": [line for line in freeze.stdout.splitlines() if line.strip()],
            }
            lock_hash = sha256_text(freeze.stdout)
            environment = PythonEnvironment(
                id=env_id,
                application_revision_id=application_revision_id,
                dependency_ids=[dependency.id for dependency in dependencies],
                env_path=str(env_path),
                python_version=python_version,
                lock_hash=lock_hash,
                lock_metadata=lock_metadata,
                status="ready",
                last_resolved_at=now_utc(),
            )
            completed = invocation.model_copy(
                update={
                    "status": "succeeded",
                    "command": command,
                    "stdout": "\n".join(stdout_parts),
                    "stderr": "\n".join(stderr_parts),
                    "completed_at": now_utc(),
                }
            )
            artifact_id = str(uuid.uuid4())
            artifact = BuildArtifact(
                id=artifact_id,
                owner_kind="ApplicationRevision",
                owner_id=application_revision_id,
                build_invocation_id=completed.id,
                artifact_type="python_environment",
                path=str(env_path),
                content_hash=lock_hash,
                metadata={"environment_id": environment.id},
            )
            with self.uow:
                self.graph.python_environments[environment.id] = environment
                self._link_environment(application_revision_id, environment.id)
                self.graph.build_invocations[completed.id] = completed
                self.graph.build_artifacts[artifact.id] = artifact
                self.store.create_edge(
                    node_ref(artifact.owner_kind, id=artifact.owner_id),
                    "HAS_BUILD_ARTIFACT",
                    node_ref("BuildArtifact", id=artifact.id),
                )
                self.store.create_edge(
                    node_ref("BuildInvocation", id=completed.id),
                    "PRODUCED",
                    node_ref("BuildArtifact", id=artifact.id),
                )
            return PythonEnvironmentResult(environment, completed)
        except Exception as exc:
            environment = PythonEnvironment(
                id=env_id,
                application_revision_id=application_revision_id,
                dependency_ids=[dependency.id for dependency in dependencies],
                env_path=str(env_path),
                python_version="",
                lock_hash=input_hash,
                status="failed",
                last_resolved_at=now_utc(),
                error=f"{type(exc).__name__}: {exc}",
            )
            failed = invocation.model_copy(
                update={
                    "status": "failed",
                    "command": command,
                    "stdout": "\n".join(stdout_parts),
                    "stderr": "\n".join(stderr_parts),
                    "error": f"{type(exc).__name__}: {exc}",
                    "completed_at": now_utc(),
                }
            )
            with self.uow:
                self.graph.python_environments[environment.id] = environment
                self._link_environment(application_revision_id, environment.id)
                self.graph.build_invocations[failed.id] = failed
            raise ValidationFailure(
                f"Python dependency resolution failed: {failed.error}"
            ) from exc

    def _record_invocation(
        self,
        *,
        application_revision_id: str,
        input_hash: str,
        status: str,
        command: list[str],
        stdout: str = "",
        stderr: str = "",
        error: str | None = None,
    ) -> BuildInvocation:
        invocation = BuildInvocation(
            id=str(uuid.uuid4()),
            owner_kind="ApplicationRevision",
            owner_id=application_revision_id,
            kind="python_env",
            status=status,  # type: ignore[arg-type]
            input_hash=input_hash,
            command=command,
            stdout=stdout,
            stderr=stderr,
            error=error,
            completed_at=now_utc(),
        )
        self.graph.build_invocations[invocation.id] = invocation
        self._link_build_invocation(application_revision_id, invocation.id)
        return invocation

    def _link_environment(
        self, application_revision_id: str, environment_id: str
    ) -> None:
        self.store.replace_single_edge(
            node_ref("ApplicationRevision", id=application_revision_id),
            "USES_PYTHON_ENV",
            node_ref("PythonEnvironment", id=environment_id),
        )

    def _link_build_invocation(
        self, application_revision_id: str, invocation_id: str
    ) -> None:
        self.store.create_edge(
            node_ref("ApplicationRevision", id=application_revision_id),
            "HAS_BUILD_INVOCATION",
            node_ref("BuildInvocation", id=invocation_id),
        )

    def _python_dependencies(
        self, application_revision_id: str
    ) -> list[RuntimeDependency]:
        from n4x.graph.bindings import RevisionBindings

        dependencies = [
            dependency
            for dependency in RevisionBindings(self.uow).dependencies(
                application_revision_id
            )
            if dependency.ecosystem == "python"
        ]
        return sorted(
            dependencies,
            key=lambda dependency: (dependency.package, dependency.spec, dependency.id),
        )

    def _dependency_fingerprint(self, dependencies: list[RuntimeDependency]) -> str:
        return sha256_json(
            [
                {
                    "id": dependency.id,
                    "package": dependency.package,
                    "spec": dependency.spec,
                }
                for dependency in dependencies
            ]
        )

    def _requirement_args(self, dependencies: list[RuntimeDependency]) -> list[str]:
        requirements = []
        for dependency in dependencies:
            requirements.append(
                f"{dependency.package}{dependency.spec}"
                if dependency.spec
                else dependency.package
            )
        return requirements

    def _python_version(self, python_executable: Path) -> str:
        self.uow.require_inactive("read Python version")
        result = subprocess.run(
            [str(python_executable), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return (result.stdout or result.stderr).strip()
