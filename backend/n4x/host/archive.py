"""Official System zip: import seed only. Not an exec path."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Iterable

from n4x.contracts.action_context import ACTION_CONTEXT_VERSION
from n4x.host.content import content_root
from n4x.host.identity import HOST_ADAPTER

PAYLOAD_PACKAGES = ("system", "runtime", "mcp", "http")
SKIP_DIR_NAMES = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_CONTRACT_FILES = {"graph_metamodel.py"}


def iter_payload_files(backend_root: Path) -> Iterable[tuple[str, Path]]:
    """Yield (zip_path, filesystem path) for official payload files.

    ``backend_root`` is the directory that contains the ``n4x`` package
    (repo ``backend/``, or an equivalent tree).
    """
    n4x_root = backend_root / "n4x"
    init_file = n4x_root / "__init__.py"
    if init_file.is_file():
        yield "n4x/__init__.py", init_file
    for package in PAYLOAD_PACKAGES:
        package_root = n4x_root / package
        if not package_root.is_dir():
            raise FileNotFoundError(f"payload package missing: {package_root}")
        yield from _iter_tree(package_root, f"n4x/{package}")
    contracts_root = n4x_root / "contracts"
    if not contracts_root.is_dir():
        raise FileNotFoundError(f"payload contracts missing: {contracts_root}")
    for path in sorted(contracts_root.rglob("*")):
        if not path.is_file() or _skip(path):
            continue
        if path.name in EXCLUDED_CONTRACT_FILES:
            continue
        relative = path.relative_to(n4x_root).as_posix()
        yield f"n4x/{relative}", path


def payload_file_map(backend_root: Path) -> dict[str, bytes]:
    return {
        zip_path: source.read_bytes()
        for zip_path, source in iter_payload_files(backend_root)
    }


def official_content_root(backend_root: Path) -> str:
    return content_root(payload_file_map(backend_root), abi_range=HOST_ADAPTER)


def write_official_archive(
    destination: Path,
    *,
    backend_root: Path,
    version: str = "0.1.0",
) -> dict[str, object]:
    files = payload_file_map(backend_root)
    digest = content_root(files, abi_range=HOST_ADAPTER)
    manifest = {
        "content_root": digest,
        "host_abi": HOST_ADAPTER,
        "action_context": ACTION_CONTEXT_VERSION,
        "version": version,
    }
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        for zip_path, blob in sorted(files.items()):
            archive.writestr(zip_path, blob)
    return manifest


def read_official_archive(archive: Path) -> tuple[dict[str, object], dict[str, bytes]]:
    archive = archive.expanduser().resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"official System archive missing: {archive}")
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        if "manifest.json" not in names:
            raise ValueError("official archive is missing manifest.json")
        payload = json.loads(zf.read("manifest.json").decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("official archive manifest must be an object")
        files: dict[str, bytes] = {}
        for name in names:
            if name == "manifest.json" or name.endswith("/"):
                continue
            if not name.startswith("n4x/"):
                raise ValueError(f"official archive has a non-payload path: {name}")
            files[name] = zf.read(name)
    return payload, files


def _iter_tree(root: Path, prefix: str) -> Iterable[tuple[str, Path]]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _skip(path):
            continue
        relative = path.relative_to(root).as_posix()
        yield f"{prefix}/{relative}", path


def _skip(path: Path) -> bool:
    if path.suffix in SKIP_SUFFIXES:
        return True
    return any(part in SKIP_DIR_NAMES for part in path.parts)
