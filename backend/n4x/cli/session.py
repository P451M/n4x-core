"""Stored operator session for the N4X CLI. Never print token values."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from n4x.kernel.errors import SecretNotFoundError
from n4x.secrets.backends import (
    EncryptedLocalFileBackend,
    MacOSKeychainBackend,
    SecretBackend,
)

CLI_KEYCHAIN_SERVICE = "N4X CLI"
_SESSION_PREFIX = "n4x-cli://session/"


@dataclass(frozen=True)
class CliSession:
    origin: str
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None
    token_endpoint: str | None = None
    client_id: str | None = None

    def expired(self, *, skew_seconds: int = 60) -> bool:
        if self.expires_at is None:
            return False
        return time.time() + skew_seconds >= self.expires_at

    def to_json(self) -> str:
        return json.dumps(
            {
                "origin": self.origin,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "token_endpoint": self.token_endpoint,
                "client_id": self.client_id,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, payload: str) -> CliSession:
        data = json.loads(payload)
        return cls(
            origin=str(data["origin"]),
            access_token=str(data["access_token"]),
            refresh_token=(
                None
                if data.get("refresh_token") in {None, ""}
                else str(data["refresh_token"])
            ),
            expires_at=(
                None
                if data.get("expires_at") in {None, ""}
                else float(data["expires_at"])
            ),
            token_endpoint=(
                None
                if data.get("token_endpoint") in {None, ""}
                else str(data["token_endpoint"])
            ),
            client_id=(
                None if data.get("client_id") in {None, ""} else str(data["client_id"])
            ),
        )


def normalize_origin(origin: str) -> str:
    parsed = urlsplit(origin.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("origin must be an http(s) URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def is_loopback_origin(origin: str) -> bool:
    host = urlsplit(normalize_origin(origin)).hostname
    return host in {"127.0.0.1", "localhost", "::1"}


def session_uri(origin: str) -> str:
    return _SESSION_PREFIX + quote(normalize_origin(origin), safe="")


def default_cli_store() -> SecretBackend:
    if MacOSKeychainBackend.available():
        return MacOSKeychainBackend(service=CLI_KEYCHAIN_SERVICE)
    return EncryptedLocalFileBackend(
        root=Path.home() / ".config" / "n4x" / "cli"
    )


def save_session(store: SecretBackend, session: CliSession) -> None:
    store.set(session_uri(session.origin), session.to_json())


def load_session(store: SecretBackend, origin: str) -> CliSession | None:
    try:
        return CliSession.from_json(store.get(session_uri(origin)))
    except SecretNotFoundError:
        return None


def delete_session(store: SecretBackend, origin: str) -> None:
    store.delete(session_uri(origin))


def session_from_token_payload(
    origin: str,
    payload: dict[str, Any],
    *,
    token_endpoint: str | None,
    client_id: str | None,
    previous: CliSession | None = None,
) -> CliSession:
    expires_in = payload.get("expires_in")
    expires_at = None if expires_in is None else time.time() + float(expires_in)
    refresh = payload.get("refresh_token")
    if not refresh and previous is not None:
        refresh = previous.refresh_token
    return CliSession(
        origin=normalize_origin(origin),
        access_token=str(payload["access_token"]),
        refresh_token=None if refresh in {None, ""} else str(refresh),
        expires_at=expires_at,
        token_endpoint=token_endpoint or (None if previous is None else previous.token_endpoint),
        client_id=client_id or (None if previous is None else previous.client_id),
    )
