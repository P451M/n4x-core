"""HTTP client for localhost Host control. Does not import Host Python."""

from __future__ import annotations

import os
from typing import Any

import httpx

from n4x.system.origins import is_loopback_origin

DEFAULT_ORIGIN = "http://127.0.0.1:7744"


def host_control_origin() -> str:
    origin = os.getenv("N4X_HOST_CONTROL_ORIGIN", DEFAULT_ORIGIN).rstrip("/")
    if not is_loopback_origin(origin):
        raise RuntimeError(f"host control origin must be loopback, got {origin}")
    return origin


def request_host_control(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{host_control_origin()}{path}"
    with httpx.Client(timeout=120.0) as client:
        response = client.request(
            method, url, json=payload if payload is not None else None
        )
    try:
        body = response.json()
    except ValueError as error:
        raise RuntimeError(f"host {path} returned non-JSON") from error
    if response.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else body
        raise RuntimeError(f"host {path} failed ({response.status_code}): {detail}")
    if not isinstance(body, dict):
        raise RuntimeError(f"host {path} returned a non-object")
    return body
