"""Public instance origin versus process host bind."""

from __future__ import annotations

from urllib.parse import urlsplit

DEFAULT_HOST_BIND = "http://127.0.0.1:7744"


def is_loopback_origin(origin: str) -> bool:
    host = (urlsplit(origin.rstrip("/")).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def instance_link(
    public_origin: str,
    path: str,
    *,
    host_bind: str = DEFAULT_HOST_BIND,
) -> dict[str, str]:
    origin = public_origin.rstrip("/")
    bind = host_bind.rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    url = f"{origin}{path}"
    if is_loopback_origin(origin):
        return {
            "url": url,
            "origin_kind": "host_bind",
            "host_bind_url": url,
        }
    return {
        "url": url,
        "origin_kind": "public",
        "host_bind_url": f"{bind}{path}",
    }
