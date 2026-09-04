from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from n4x.secrets.paths import default_secrets_root, production_mode
from n4x.kernel.errors import SecretBackendError, SecretNotFoundError

MASTER_KEY_ENV = "N4X_SECRETS_MASTER_KEY"
BACKEND_ENV = "N4X_SECRET_BACKEND"


class SecretBackend(Protocol):
    name: str

    def set(self, uri: str, value: str) -> None: ...

    def get(self, uri: str) -> str: ...

    def delete(self, uri: str) -> None: ...


class MacOSKeychainBackend:
    name = "macos_keychain"

    def __init__(self, service: str = "N4X") -> None:
        self.service = service

    @classmethod
    def available(cls) -> bool:
        return platform.system() == "Darwin" and _which("security") is not None

    def set(self, uri: str, value: str) -> None:
        command = [
            "security",
            "add-generic-password",
            "-a",
            uri,
            "-s",
            self.service,
            "-U",
            "-w",
            value,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise SecretBackendError(_redact(value, result.stderr or result.stdout))

    def get(self, uri: str) -> str:
        command = [
            "security",
            "find-generic-password",
            "-a",
            uri,
            "-s",
            self.service,
            "-w",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            if result.returncode == 44:
                raise SecretNotFoundError(f"secret not found: {uri}")
            raise SecretBackendError(
                result.stderr or result.stdout or f"secret not found: {uri}"
            )
        return result.stdout.rstrip("\n")

    def delete(self, uri: str) -> None:
        command = ["security", "delete-generic-password", "-a", uri, "-s", self.service]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode not in {0, 44}:
            raise SecretBackendError(result.stderr or result.stdout)


class EncryptedLocalFileBackend:
    name = "encrypted_local"

    def __init__(self, root: Path | None = None, key: bytes | None = None) -> None:
        self.root = root or default_secrets_root()
        self.path = self.root / "secrets.enc"
        self.key_path = self.root / "secrets.key"
        self.key = key if key is not None else self._load_or_create_key()

    def set(self, uri: str, value: str) -> None:
        data = self._read_all()
        data[uri] = value
        self._write_all(data)

    def get(self, uri: str) -> str:
        data = self._read_all()
        if uri not in data:
            raise SecretNotFoundError(f"secret not found: {uri}")
        return data[uri]

    def delete(self, uri: str) -> None:
        data = self._read_all()
        data.pop(uri, None)
        self._write_all(data)

    def _load_or_create_key(self) -> bytes:
        configured = os.getenv(MASTER_KEY_ENV, "").strip()
        if configured:
            return fernet_key_from_master(configured)
        if production_mode():
            raise SecretBackendError(
                f"{MASTER_KEY_ENV} is required in production; "
                "the master key is not written next to ciphertext"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            return self.key_path.read_bytes()
        key = Fernet.generate_key()
        fd = os.open(self.key_path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(key)
        return key

    def _read_all(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            plaintext = Fernet(self.key).decrypt(self.path.read_bytes())
        except InvalidToken as exc:
            raise SecretBackendError("local secrets file cannot be decrypted") from exc
        return json.loads(plaintext.decode("utf-8"))

    def _write_all(self, data: dict[str, str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        encrypted = Fernet(self.key).encrypt(
            json.dumps(data, sort_keys=True).encode("utf-8")
        )
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(encrypted)


class InMemorySecretBackend:
    name = "memory"

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, uri: str, value: str) -> None:
        self.values[uri] = value

    def get(self, uri: str) -> str:
        if uri not in self.values:
            raise SecretNotFoundError(f"secret not found: {uri}")
        return self.values[uri]

    def delete(self, uri: str) -> None:
        self.values.pop(uri, None)


def default_secret_backend() -> SecretBackend:
    selected = os.getenv(BACKEND_ENV, "").strip().lower()
    if selected == "memory":
        return InMemorySecretBackend()
    if selected == "encrypted_local":
        return EncryptedLocalFileBackend()
    if selected == "macos_keychain":
        return MacOSKeychainBackend()
    if os.getenv(MASTER_KEY_ENV, "").strip():
        return EncryptedLocalFileBackend()
    if MacOSKeychainBackend.available():
        return MacOSKeychainBackend()
    return EncryptedLocalFileBackend()


def fernet_key_from_master(master: str) -> bytes:
    raw = master.strip().encode("utf-8")
    try:
        Fernet(raw)
        return raw
    except ValueError:
        return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


def _which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _redact(secret: str, value: str) -> str:
    if not secret:
        return value
    return value.replace(secret, "[REDACTED]")
