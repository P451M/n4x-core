#!/usr/bin/env python3
"""Write an official release index from the official System zip payload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from n4x.host.archive import official_content_root  # noqa: E402
from n4x.host.identity import HOST_ADAPTER  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifact-url", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend-root", type=Path, default=ROOT / "backend")
    args = parser.parse_args()
    digest = official_content_root(args.backend_root)
    payload = {
        "releases": [
            {
                "version": args.version,
                "content_root": digest,
                "host_abi": HOST_ADAPTER,
                "artifact_url": args.artifact_url or None,
            }
        ]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
