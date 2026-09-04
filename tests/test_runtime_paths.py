from __future__ import annotations

from pathlib import Path

from n4x.host.paths import (
    default_application_runtime_root,
    default_install_root,
    default_runtime_root,
    default_secrets_root,
)
from n4x.runtime.actions import RuntimePaths
from n4x.secrets.backends import EncryptedLocalFileBackend


def test_runtime_root_env_is_the_only_durable_parent(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("N4X_RUNTIME_ROOT", str(tmp_path / "n4x"))
    monkeypatch.delenv("N4X_INSTALL_ROOT", raising=False)
    root = default_runtime_root()
    assert root == (tmp_path / "n4x").resolve()
    assert default_install_root() == root / "host"
    assert default_secrets_root() == root / "config"
    assert default_application_runtime_root() == root / "runtime"
    assert RuntimePaths.default().root == root / "runtime"


def test_encrypted_backend_uses_runtime_root_and_env_key(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("N4X_RUNTIME_ROOT", str(tmp_path / "n4x"))
    monkeypatch.setenv("N4X_SECRETS_MASTER_KEY", "operator-provided-master")
    backend = EncryptedLocalFileBackend()
    backend.set("secret://test/key", "value")
    assert backend.get("secret://test/key") == "value"
    assert backend.path == default_secrets_root() / "secrets.enc"
    assert not backend.key_path.exists()


def test_production_encrypted_backend_refuses_sidecar_key(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("N4X_RUNTIME_ROOT", str(tmp_path / "n4x"))
    monkeypatch.setenv("N4X_HTTP_MODE", "production")
    monkeypatch.delenv("N4X_SECRETS_MASTER_KEY", raising=False)
    from n4x.kernel.errors import SecretBackendError

    try:
        EncryptedLocalFileBackend()
    except SecretBackendError as error:
        assert "N4X_SECRETS_MASTER_KEY" in str(error)
    else:
        raise AssertionError("expected SecretBackendError")
