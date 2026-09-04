from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from n4x.host.errors import InstanceDumpError
from n4x.host.worker import WorkerBootError
from n4x.kernel.paths import PathContainmentError

HOST_PREFIX = "/n4x-host"


class ReverseProxy:
    def __init__(self, host: Any) -> None:
        self.host = host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            raise RuntimeError("reverse proxy only handles HTTP")
        path = scope.get("path", "")
        if path == "/health":
            response = JSONResponse(self.host.health_payload())
            await response(scope, receive, send)
            return
        if path.startswith(HOST_PREFIX):
            await self.host.control_app(scope, receive, send)
            return
        worker = self.host.supervisor.current
        if worker is None:
            response = JSONResponse(
                {"error": "system_worker_unavailable"}, status_code=503
            )
            await response(scope, receive, send)
            return
        request = Request(scope, receive)
        url = f"{worker.base_url}{path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "content-length"}
        }

        async def request_body():
            async for chunk in request.stream():
                yield chunk

        timeout = httpx.Timeout(connect=10.0, read=900.0, write=900.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                request.method,
                url,
                headers=headers,
                content=request_body(),
            ) as forwarded:
                response_headers = [
                    (key.encode("latin-1"), value.encode("latin-1"))
                    for key, value in forwarded.headers.items()
                    if key.lower()
                    not in {
                        "content-encoding",
                        "transfer-encoding",
                        "content-length",
                    }
                ]
                await send(
                    {
                        "type": "http.response.start",
                        "status": forwarded.status_code,
                        "headers": response_headers,
                    }
                )
                async for chunk in forwarded.aiter_bytes():
                    await send(
                        {
                            "type": "http.response.body",
                            "body": chunk,
                            "more_body": True,
                        }
                    )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    }
                )


def _localhost_only(request: Request) -> JSONResponse | None:
    client_host = None if request.client is None else request.client.host
    if client_host not in {None, "127.0.0.1", "::1", "testclient"}:
        return JSONResponse({"error": "local_only"}, status_code=403)
    return None


def create_control_routes(host: Any) -> list[Route]:
    async def inspect(_request: Request) -> JSONResponse:
        denied = _localhost_only(_request)
        if denied is not None:
            return denied
        enabled = host.system_graph.enabled_revision()
        worker = host.supervisor.current
        return JSONResponse(
            {
                "runtime": "n4x-host",
                "revision_id": None if enabled is None else enabled.id,
                "source_tree_id": None if enabled is None else enabled.source_tree_id,
                "content_root": None if enabled is None else enabled.content_root,
                "worker": None
                if worker is None
                else {
                    "revision_id": worker.spec.revision_id,
                    "port": worker.port,
                },
            }
        )

    async def enable(request: Request) -> JSONResponse:
        denied = _localhost_only(request)
        if denied is not None:
            return denied
        payload = await request.json()
        revision_id = str(payload["revision_id"])
        try:
            host.enable_revision(revision_id)
        except KeyError:
            return JSONResponse({"error": "revision_not_found"}, status_code=404)
        except WorkerBootError as error:
            return JSONResponse(
                {"error": "system_worker_unavailable", "detail": str(error)},
                status_code=503,
            )
        return JSONResponse({"enabled": revision_id})

    async def import_archive(request: Request) -> JSONResponse:
        denied = _localhost_only(request)
        if denied is not None:
            return denied
        payload = await request.json()
        try:
            result = host.import_official_archive(Path(str(payload["archive"])))
        except (FileNotFoundError, ValueError, KeyError) as error:
            return JSONResponse(
                {"error": "import_failed", "detail": str(error)},
                status_code=400,
            )
        return JSONResponse(result)

    async def release(_request: Request) -> JSONResponse:
        denied = _localhost_only(_request)
        if denied is not None:
            return denied
        return JSONResponse(host.release_status())

    async def dump(request: Request) -> JSONResponse:
        denied = _localhost_only(request)
        if denied is not None:
            return denied
        payload = await request.json()
        neo4j = payload.get("neo4j_dump")
        output = payload.get("output")
        try:
            result = host.dump_instance(
                None if output in {None, ""} else Path(str(output)),
                neo4j_dump=None if neo4j in {None, ""} else Path(str(neo4j)),
            )
        except InstanceDumpError as error:
            return JSONResponse(
                {"error": "instance_dump_failed", "detail": str(error)},
                status_code=400,
            )
        return JSONResponse(result)

    async def restore(request: Request) -> JSONResponse:
        denied = _localhost_only(request)
        if denied is not None:
            return denied
        payload = await request.json()
        try:
            result = host.restore_instance(Path(str(payload["input"])))
        except InstanceDumpError as error:
            return JSONResponse(
                {"error": "instance_restore_failed", "detail": str(error)},
                status_code=400,
            )
        return JSONResponse(result)

    async def list_exports(_request: Request) -> JSONResponse:
        denied = _localhost_only(_request)
        if denied is not None:
            return denied
        return JSONResponse(host.list_exports())

    async def download_export(request: Request) -> FileResponse | JSONResponse:
        denied = _localhost_only(request)
        if denied is not None:
            return denied
        name = request.path_params["name"]
        try:
            path = host.export_file(name)
        except PathContainmentError as error:
            return JSONResponse(
                {"error": "invalid_export", "detail": str(error)},
                status_code=400,
            )
        except FileNotFoundError:
            return JSONResponse({"error": "export_not_found"}, status_code=404)
        return FileResponse(
            path,
            filename=name,
            media_type="application/gzip",
        )

    return [
        Route(f"{HOST_PREFIX}/control", inspect),
        Route(f"{HOST_PREFIX}/release", release),
        Route(f"{HOST_PREFIX}/enable", enable, methods=["POST"]),
        Route(f"{HOST_PREFIX}/import", import_archive, methods=["POST"]),
        Route(f"{HOST_PREFIX}/dump", dump, methods=["POST"]),
        Route(f"{HOST_PREFIX}/restore", restore, methods=["POST"]),
        Route(f"{HOST_PREFIX}/exports", list_exports),
        Route(HOST_PREFIX + "/exports/{name}", download_export),
    ]
