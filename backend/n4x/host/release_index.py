from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from n4x.host.identity import HOST_ADAPTER

RELEASE_INDEX_ENV = "N4X_SYSTEM_RELEASE_INDEX"


@dataclass(frozen=True)
class OfficialRelease:
    version: str
    content_root: str
    host_abi: str
    artifact_url: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "content_root": self.content_root,
            "host_abi": self.host_abi,
            "artifact_url": self.artifact_url,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> OfficialRelease:
        artifact_url = payload.get("artifact_url")
        return cls(
            version=str(payload["version"]),
            content_root=str(payload["content_root"]),
            host_abi=str(payload.get("host_abi") or HOST_ADAPTER),
            artifact_url=None if artifact_url in {None, ""} else str(artifact_url),
        )


def load_release_index(source: str | None = None) -> list[OfficialRelease]:
    configured = source if source is not None else os.getenv(RELEASE_INDEX_ENV, "").strip()
    if not configured:
        return []
    payload = _read_index(configured)
    items = payload.get("releases")
    if not isinstance(items, list):
        raise ValueError("release index must contain a releases array")
    releases: list[OfficialRelease] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        release = OfficialRelease.from_json(item)
        if release.content_root in seen:
            continue
        seen.add(release.content_root)
        releases.append(release)
    return releases


def _read_index(source: str) -> dict[str, Any]:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        with urlopen(source, timeout=30) as response:  # noqa: S310
            raw = response.read()
        payload = json.loads(raw.decode("utf-8"))
    else:
        payload = json.loads(Path(source).expanduser().read_text())
    if not isinstance(payload, dict):
        raise ValueError("release index must be a JSON object")
    return payload
