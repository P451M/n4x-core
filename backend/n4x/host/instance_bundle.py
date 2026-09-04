from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from n4x.host.content import sha256_bytes
from n4x.host.errors import InstanceDumpError
from n4x.host.identity import HOST_ADAPTER

BUNDLE_FORMAT = 1
MANIFEST_NAME = "manifest.json"
NEO4J_MEMBER = "neo4j/database.dump"
RUNTIME_PREFIX = "runtime/"
SKIP_NAMES = frozenset({"secrets.key", "host.lock"})
SKIP_SUFFIXES = (".log",)


@dataclass(frozen=True)
class InstanceManifest:
    bundle_format: int
    content_root: str
    host_abi: str
    created_at: str
    hashes: dict[str, str]

    def to_json(self) -> dict[str, object]:
        return {
            "bundle_format": self.bundle_format,
            "content_root": self.content_root,
            "host_abi": self.host_abi,
            "created_at": self.created_at,
            "hashes": dict(self.hashes),
        }

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> InstanceManifest:
        hashes = payload.get("hashes")
        if not isinstance(hashes, dict):
            raise InstanceDumpError("instance bundle manifest is missing hashes")
        return cls(
            bundle_format=int(payload.get("bundle_format") or BUNDLE_FORMAT),
            content_root=str(payload["content_root"]),
            host_abi=str(payload.get("host_abi") or HOST_ADAPTER),
            created_at=str(payload.get("created_at") or ""),
            hashes={str(key): str(value) for key, value in hashes.items()},
        )


def should_skip(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return True
    return path.name.endswith(SKIP_SUFFIXES)


def iter_runtime_files(runtime_root: Path) -> Iterable[Path]:
    if not runtime_root.exists():
        return
    for path in sorted(runtime_root.rglob("*")):
        if path.is_file() and not should_skip(path):
            yield path


def write_bundle(
    destination: Path,
    *,
    runtime_root: Path,
    neo4j_dump: Path,
    content_root: str,
    host_abi: str = HOST_ADAPTER,
) -> InstanceManifest:
    if not neo4j_dump.is_file():
        raise InstanceDumpError(f"neo4j dump is missing: {neo4j_dump}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    neo4j_hash = sha256_bytes(neo4j_dump.read_bytes())
    runtime_hashes: dict[str, str] = {}
    created_at = datetime.now(timezone.utc).isoformat()
    with tarfile.open(destination, "w:gz") as archive:
        for path in iter_runtime_files(runtime_root):
            relative = path.relative_to(runtime_root).as_posix()
            runtime_hashes[relative] = sha256_bytes(path.read_bytes())
            archive.add(path, arcname=f"{RUNTIME_PREFIX}{relative}")
        archive.add(neo4j_dump, arcname=NEO4J_MEMBER)
        manifest = InstanceManifest(
            bundle_format=BUNDLE_FORMAT,
            content_root=content_root,
            host_abi=host_abi,
            created_at=created_at,
            hashes={
                "neo4j": neo4j_hash,
                "runtime": _digest_mapping(runtime_hashes),
            },
        )
        manifest_bytes = (
            json.dumps(manifest.to_json(), indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(manifest_bytes)
        import io

        archive.addfile(info, io.BytesIO(manifest_bytes))
    return manifest


def extract_bundle(archive_path: Path, destination: Path) -> InstanceManifest:
    if not archive_path.is_file():
        raise InstanceDumpError(f"instance bundle is missing: {archive_path}")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        _safe_extract(archive, destination)
    manifest_path = destination / MANIFEST_NAME
    if not manifest_path.is_file():
        raise InstanceDumpError("instance bundle is missing manifest.json")
    return InstanceManifest.from_json(json.loads(manifest_path.read_text()))


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.getmembers():
        name = member.name
        if name.startswith("/") or ".." in Path(name).parts:
            raise InstanceDumpError(f"unsafe bundle member: {name}")
        target = (destination / name).resolve()
        if destination != target and destination not in target.parents:
            raise InstanceDumpError(f"bundle member escapes destination: {name}")
    archive.extractall(destination, filter="data")


def _digest_mapping(values: dict[str, str]) -> str:
    payload = json.dumps(values, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
