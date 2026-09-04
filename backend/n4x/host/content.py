from __future__ import annotations

import hashlib
from typing import Mapping

from n4x.kernel.hash import sha256_json


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def content_root(
    files: Mapping[str, bytes],
    *,
    abi_range: str,
) -> str:
    """Canonical System identity. Official = this hash is in the host catalog."""
    return sha256_json(
        {
            "abi_range": abi_range,
            "files": {path: sha256_bytes(blob) for path, blob in sorted(files.items())},
        }
    )
