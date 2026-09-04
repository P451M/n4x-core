"""Host HTTP constants. Do not import n4x.http or n4x.system."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

MCP_HTTP_PATH = "/mcp"
RuntimeMode = Literal["development", "production"]


def is_loopback_origin(origin: str) -> bool:
    host = (urlsplit(origin.rstrip("/")).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def public_base_url(host: str, port: int) -> str:
    bound = "127.0.0.1" if host in {"0.0.0.0", "::", "[::]"} else host
    return f"http://{bound}:{port}"


def resolve_worker_public_origin(
    host: str,
    port: int,
    configured: str | None = None,
) -> str:
    origin = (configured or "").strip().rstrip("/")
    if origin and not is_loopback_origin(origin):
        return origin
    return public_base_url(host, port)
