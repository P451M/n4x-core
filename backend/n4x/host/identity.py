"""Host identity constants. Not System policy and not a boot seal."""

from __future__ import annotations

import os
from pathlib import Path

HOST_ADAPTER = "n4x.host.v1"
PLATFORM_SYSTEM_ID = "n4x"
OFFICIAL_ARCHIVE_ENV = "N4X_OFFICIAL_SYSTEM_ARCHIVE"
DEFAULT_OFFICIAL_ARCHIVE = Path("/opt/n4x/cache/official-system.zip")


def official_archive_path() -> Path:
    configured = os.getenv(OFFICIAL_ARCHIVE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_OFFICIAL_ARCHIVE
