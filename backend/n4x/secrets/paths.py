"""Secret and product path helpers. Adapter-owned; Host must not be imported here."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_MACOS_PRODUCT_ROOT = Path.home() / "Library" / "Application Support" / "N4X"
_POSIX_PRODUCT_ROOT = Path.home() / ".n4x"


def default_runtime_root() -> Path:
    configured = os.getenv("N4X_RUNTIME_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "darwin":
        return _MACOS_PRODUCT_ROOT.resolve()
    return _POSIX_PRODUCT_ROOT.resolve()


def default_secrets_root() -> Path:
    return (default_runtime_root() / "config").resolve()


def default_application_runtime_root() -> Path:
    return (default_runtime_root() / "runtime").resolve()


def production_mode() -> bool:
    return os.getenv("N4X_HTTP_MODE", "").strip().lower() == "production"
