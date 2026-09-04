from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import tomllib

from n4x.host.archive import official_content_root, write_official_archive
from tests.host_support import BACKEND_ROOT


def test_wheel_excludes_system_payload() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    exclude = pyproject["tool"]["setuptools"]["packages"]["find"]["exclude"]
    assert "n4x.system*" in exclude
    assert "n4x.runtime*" in exclude
    assert "n4x.mcp*" in exclude
    assert "n4x.http*" in exclude


def test_host_only_path_cannot_import_system(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter"
    backend = Path(__file__).resolve().parents[1] / "backend" / "n4x"
    n4x_dst = adapter / "n4x"
    n4x_dst.mkdir(parents=True)
    (n4x_dst / "__init__.py").write_text((backend / "__init__.py").read_text())
    for name in ("host", "graph", "secrets", "source_store", "kernel", "contracts"):
        dest = n4x_dst / name
        dest.symlink_to(backend / name, target_is_directory=True)
    backend = str(Path(__file__).resolve().parents[1] / "backend")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(adapter)
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            "import sys\n"
            f"adapter = {str(adapter)!r}\n"
            f"backend = {backend!r}\n"
            "sys.path = [adapter] + [p for p in sys.path if p != backend]\n"
            "import n4x.host\n"
            "try:\n"
            "    import n4x.system\n"
            "except ImportError:\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit('imported n4x.system from ' + n4x.system.__file__)\n",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_official_zip_hashes_action_runner(tmp_path: Path) -> None:
    digest = official_content_root(BACKEND_ROOT)
    archive = tmp_path / "official.zip"
    write_official_archive(archive, backend_root=BACKEND_ROOT)
    assert digest.startswith("sha256:")
    assert (BACKEND_ROOT / "n4x" / "runtime" / "action_runner.py").is_file()
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert {
        "n4x/runtime/surface_theme/theme.css",
        "n4x/runtime/surface_theme/authoring-guide.md",
        "n4x/runtime/surface_theme/manifest.json",
    }.issubset(names)
