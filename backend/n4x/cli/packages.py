"""Stream Package archives to an instance over HTTP."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from n4x.cli.oauth import LoginError, bearer_for
from n4x.cli.session import SecretBackend, is_loopback_origin, normalize_origin

_ARCHIVE_SUFFIX = ".n4xp"


class PackageTransferError(RuntimeError):
    pass


def stage_archive(
    origin: str,
    source: Path,
    *,
    store: SecretBackend,
    overwrite: bool = False,
    http: httpx.Client | None = None,
) -> dict[str, object]:
    origin = normalize_origin(origin)
    if not source.is_file():
        raise PackageTransferError(f"file not found: {source}")
    archive_name = source.name
    if not archive_name.endswith(_ARCHIVE_SUFFIX):
        raise PackageTransferError("archive_name must end in .n4xp")
    digest = _file_sha256(source)
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-SHA256": digest,
        "Accept": "application/json",
    }
    if not is_loopback_origin(origin):
        try:
            headers["Authorization"] = f"Bearer {bearer_for(origin, store=store, http=http)}"
        except LoginError as exc:
            raise PackageTransferError(str(exc)) from exc
    url = f"{origin}/packages/{archive_name}"
    if overwrite:
        url = f"{url}?overwrite=true"
    client = http or httpx.Client(timeout=httpx.Timeout(900.0))
    owns_client = http is None
    try:
        with source.open("rb") as handle:
            response = client.put(url, content=handle, headers=headers)
    finally:
        if owns_client:
            client.close()
    if response.status_code == 401:
        raise PackageTransferError(
            f"not authorized for {origin}; run n4x login --origin {origin}"
        )
    if response.status_code >= 400:
        raise PackageTransferError(_error_text(response))
    payload = response.json()
    if not isinstance(payload, dict):
        raise PackageTransferError("stage returned a non-object")
    return payload


def fetch_archive(
    origin: str,
    archive_name: str,
    output: Path,
    *,
    store: SecretBackend,
    http: httpx.Client | None = None,
) -> Path:
    origin = normalize_origin(origin)
    if not archive_name.endswith(_ARCHIVE_SUFFIX):
        raise PackageTransferError("archive_name must end in .n4xp")
    headers = {"Accept": "application/octet-stream"}
    if not is_loopback_origin(origin):
        try:
            headers["Authorization"] = f"Bearer {bearer_for(origin, store=store, http=http)}"
        except LoginError as exc:
            raise PackageTransferError(str(exc)) from exc
    url = f"{origin}/packages/{archive_name}"
    client = http or httpx.Client(timeout=httpx.Timeout(900.0))
    owns_client = http is None
    try:
        with client.stream("GET", url, headers=headers) as response:
            if response.status_code == 401:
                raise PackageTransferError(
                    f"not authorized for {origin}; run n4x login --origin {origin}"
                )
            if response.status_code >= 400:
                raise PackageTransferError(_error_text(response))
            output.parent.mkdir(parents=True, exist_ok=True)
            hasher = hashlib.sha256()
            with output.open("wb") as handle:
                for chunk in response.iter_bytes():
                    hasher.update(chunk)
                    handle.write(chunk)
    finally:
        if owns_client:
            client.close()
    return output


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"package HTTP {response.status_code}: {response.text}"
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return f"package HTTP {response.status_code}"
