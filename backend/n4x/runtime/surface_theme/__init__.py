"""Versioned platform UI seed assets packaged with the N4X kernel."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from importlib import resources
from typing import Any


@dataclass(frozen=True)
class PackagedAuthoringRelease:
    release_version: str
    theme_id: str
    theme_title: str
    css_text: str
    theme_provenance: dict[str, Any]
    theme_license: dict[str, Any]
    guide_id: str
    guide_title: str
    guide_content: str
    guide_provenance: dict[str, Any]
    guide_license: dict[str, Any]
    references: list[dict[str, Any]]


def load_packaged_authoring_release() -> PackagedAuthoringRelease:
    package = resources.files(__package__)
    manifest = json.loads(
        package.joinpath("manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("schema_version") != "n4x.platform-authoring-seed.v1":
        raise ValueError("unsupported platform authoring seed manifest")

    release_version = manifest.get("release_version")
    theme = manifest.get("theme")
    guide = manifest.get("guide")
    references = manifest.get("references")
    if (
        not isinstance(release_version, str)
        or not release_version
        or not isinstance(theme, dict)
        or not isinstance(guide, dict)
        or not isinstance(references, list)
    ):
        raise ValueError("invalid platform authoring seed manifest")

    return PackagedAuthoringRelease(
        release_version=release_version,
        theme_id=_required_text(theme, "id"),
        theme_title=_required_text(theme, "title"),
        css_text=package.joinpath("theme.css").read_text(encoding="utf-8"),
        theme_provenance=deepcopy(theme.get("provenance", {})),
        theme_license=deepcopy(theme.get("license", {})),
        guide_id=_required_text(guide, "id"),
        guide_title=_required_text(guide, "title"),
        guide_content=package.joinpath("authoring-guide.md").read_text(
            encoding="utf-8"
        ),
        guide_provenance=deepcopy(guide.get("provenance", {})),
        guide_license=deepcopy(guide.get("license", {})),
        references=deepcopy(references),
    )


def _required_text(values: dict[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"platform authoring seed requires {key}")
    return value


__all__ = ["PackagedAuthoringRelease", "load_packaged_authoring_release"]
