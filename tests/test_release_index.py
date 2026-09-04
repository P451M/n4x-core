from __future__ import annotations

import json
from pathlib import Path

from n4x.host.identity import HOST_ADAPTER
from n4x.host.release_index import load_release_index


def test_release_index_is_empty_without_source() -> None:
    assert load_release_index("") == []


def test_release_index_reads_local_file(tmp_path: Path) -> None:
    path = tmp_path / "release-index.json"
    path.write_text(
        json.dumps(
            {
                "releases": [
                    {
                        "version": "1.0.0",
                        "content_root": "sha256:abc",
                        "artifact_url": "https://example.test/system.zip",
                    }
                ]
            }
        )
    )
    releases = load_release_index(str(path))
    assert len(releases) == 1
    assert releases[0].version == "1.0.0"
    assert releases[0].content_root == "sha256:abc"
    assert releases[0].host_abi == HOST_ADAPTER
    assert releases[0].artifact_url == "https://example.test/system.zip"
