"""System-owned diagnostics. Consumed by the worker, not the host."""

from __future__ import annotations

import os


def worker_identity() -> dict[str, object]:
    return {
        "status": "ok",
        "runtime": "n4x-system",
        "revision_id": os.environ.get("N4X_SYSTEM_REVISION_ID", ""),
        "source_tree_id": os.environ.get("N4X_SYSTEM_SOURCE_TREE_ID", ""),
        "content_root": os.environ.get("N4X_SYSTEM_CONTENT_ROOT", ""),
        "host_abi": os.environ.get("N4X_HOST_ABI", "n4x.host.v1"),
        "role": os.environ.get("N4X_SYSTEM_ROLE", "system"),
    }


def system_info() -> dict[str, object]:
    return worker_identity()
