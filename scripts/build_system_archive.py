#!/usr/bin/env python3
"""Build the official System zip used as an import seed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from n4x.host.archive import write_official_archive  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "official-system.zip",
    )
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument(
        "--backend-root",
        type=Path,
        default=ROOT / "backend",
    )
    args = parser.parse_args()
    manifest = write_official_archive(
        args.output,
        backend_root=args.backend_root,
        version=args.version,
    )
    print(json.dumps({"output": str(args.output), **manifest}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
