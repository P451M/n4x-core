from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from n4x.host.errors import InstanceDumpError
from n4x.host.instance_bundle import (
    NEO4J_MEMBER,
    RUNTIME_PREFIX,
    extract_bundle,
    write_bundle,
)
from n4x.host.paths import default_runtime_root

DUMP_COMMAND_ENV = "N4X_NEO4J_DUMP_COMMAND"
LOAD_COMMAND_ENV = "N4X_NEO4J_LOAD_COMMAND"


def dump_instance(
    host: object,
    output: Path,
    *,
    runtime_root: Path | None = None,
    neo4j_dump: Path | None = None,
) -> dict[str, object]:
    """Stop the worker, pack ciphertext + runtime + Neo4j dump, restart."""
    output = output.expanduser().resolve()
    root = (runtime_root or default_runtime_root()).resolve()
    enabled = host.system_graph.enabled_revision()
    content_root = "" if enabled is None else enabled.content_root
    was_running = host.supervisor.current is not None
    if was_running:
        host.supervisor.stop()
    try:
        produced = neo4j_dump or _run_neo4j_command(
            DUMP_COMMAND_ENV, output.parent / "neo4j.database.dump"
        )
        manifest = write_bundle(
            output,
            runtime_root=root,
            neo4j_dump=produced,
            content_root=content_root,
        )
    finally:
        if was_running and enabled is not None:
            host._materialize_and_start(enabled)
    return {"output": str(output), "dumped": True, **manifest.to_json()}


def restore_instance(
    host: object,
    archive: Path,
    *,
    runtime_root: Path | None = None,
    neo4j_dump: Path | None = None,
) -> dict[str, object]:
    """Stop the worker, restore runtime + Neo4j, leave System unstarted."""
    archive = archive.expanduser().resolve()
    root = (runtime_root or default_runtime_root()).resolve()
    workspace = archive.parent / f".{archive.name}.restore"
    if workspace.exists():
        shutil.rmtree(workspace)
    was_running = host.supervisor.current is not None
    if was_running:
        host.supervisor.stop()
    try:
        manifest = extract_bundle(archive, workspace)
        runtime_source = workspace / RUNTIME_PREFIX.rstrip("/")
        if runtime_source.exists():
            _replace_runtime(root, runtime_source)
        dump_file = neo4j_dump or workspace / NEO4J_MEMBER
        if not dump_file.is_file():
            raise InstanceDumpError("restored bundle has no Neo4j dump")
        kept_dump = root / "neo4j-restore" / "database.dump"
        kept_dump.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dump_file, kept_dump)
        _run_neo4j_command(LOAD_COMMAND_ENV, kept_dump, required=False)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return {"restored": str(archive), **manifest.to_json()}


def _replace_runtime(dest: Path, source: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name == "host":
            _merge_host(dest / "host", item)
            continue
        target = dest / item.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _merge_host(dest: Path, source: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name in {"host.lock", "runtime"}:
            continue
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _run_neo4j_command(
    env_name: str, output: Path, *, required: bool = True
) -> Path:
    configured = os.getenv(env_name, "").strip()
    if not configured:
        if required and not output.is_file():
            raise InstanceDumpError(
                f"provide --neo4j-dump or set {env_name} ({{output}} is replaced)"
            )
        return output
    command = [
        part.replace("{output}", str(output)) for part in shlex.split(configured)
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise InstanceDumpError(
            f"{env_name} exited {completed.returncode}: {command}"
        )
    if required and not output.is_file():
        raise InstanceDumpError(f"{env_name} did not write {output}")
    return output
