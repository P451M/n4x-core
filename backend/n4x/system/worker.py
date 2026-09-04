"""Designed System worker entry. The host must not import this module."""

from __future__ import annotations

import os

import uvicorn
from starlette.applications import Starlette

from n4x.http.inspector import InspectorSettings
from n4x.system.http import RuntimeMode, create_system_http_app
from n4x.system.runtime import SystemRuntime


def create_system_app() -> Starlette:
    runtime = SystemRuntime.from_env()
    mode: RuntimeMode = (
        "development"
        if os.environ.get("N4X_HTTP_MODE", "production") == "development"
        else "production"
    )
    public_origin = os.environ.get("N4X_PUBLIC_ORIGIN", "http://127.0.0.1:7744")
    inspector = None
    if mode == "development" and os.environ.get("N4X_INSPECTOR", "1") != "0":
        inspector = InspectorSettings(
            mcp_url=f"{public_origin.rstrip('/')}{os.environ.get('N4X_MCP_PATH', '/mcp')}",
            client_port=int(os.environ.get("N4X_INSPECTOR_PORT", "6274")),
            proxy_port=int(os.environ.get("N4X_INSPECTOR_PROXY_PORT", "6277")),
        )
    return create_system_http_app(
        runtime,
        mode=mode,
        inspector=inspector,
        public_origin=public_origin,
    )


def main() -> None:
    if os.environ.get("N4X_WORKER_MODE") == "stdio":
        from n4x.system.mcp import create_system_mcp

        create_system_mcp().run()
        return
    host = os.environ.get("N4X_WORKER_HOST", "127.0.0.1")
    port = int(os.environ["N4X_WORKER_PORT"])
    uvicorn.run(create_system_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
