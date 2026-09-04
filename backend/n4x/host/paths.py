from __future__ import annotations

import os
from pathlib import Path

from n4x.secrets.paths import (
    default_application_runtime_root,
    default_runtime_root,
    default_secrets_root,
    production_mode,
)


def default_install_root() -> Path:
    configured = os.getenv("N4X_INSTALL_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (default_runtime_root() / "host").resolve()


def default_exports_root() -> Path:
    return (default_runtime_root() / "exports").resolve()


def resolve_export_file(name: str) -> Path:
    from n4x.kernel.paths import PathContainmentError

    if not isinstance(name, str) or not name or "/" in name or "\\" in name:
        raise PathContainmentError("export name must be a single path segment")
    if not name.endswith(".n4xi"):
        raise PathContainmentError("export name must end with .n4xi")
    root = default_exports_root()
    candidate = (root / name).resolve()
    if root not in candidate.parents:
        raise PathContainmentError("export path escapes the exports root")
    return candidate


__all__ = [
    "default_application_runtime_root",
    "default_exports_root",
    "default_install_root",
    "default_runtime_root",
    "default_secrets_root",
    "production_mode",
    "resolve_export_file",
]
