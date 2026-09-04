from __future__ import annotations

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager
from html import escape
import json
from pathlib import Path
from collections.abc import AsyncIterator
import re
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from n4x.http.inspector import InspectorProcess, InspectorSettings
from n4x.kernel.errors import (
    ExperienceAccessError,
    FileDeliveryError,
    SecretBackendError,
    ValidationFailure,
    public_invocation_error,
)
from n4x.contracts.package import PACKAGE_MAX_ARCHIVE_BYTES
from n4x.kernel.paths import PathContainmentError, resolve_path_within
from n4x.kernel.surface_types import BrowserSurfaceConfig
from n4x.system.inspect import system_info, worker_identity
from n4x.system.mcp import create_system_mcp
from n4x.system.packages import PackageService
from n4x.system.runtime import SystemRuntime

MCP_HTTP_PATH = "/mcp"
RuntimeMode = Literal["development", "production"]


def create_system_http_app(
    runtime: SystemRuntime,
    *,
    mode: RuntimeMode = "production",
    inspector: InspectorSettings | None = None,
    public_origin: str = "http://127.0.0.1:7744",
) -> Starlette:
    if mode not in {"development", "production"}:
        raise ValueError(f"unsupported runtime mode: {mode}")
    if inspector is not None and mode != "development":
        raise ValueError("MCP Inspector is only started in development mode")

    mcp_app = create_system_mcp(runtime, public_origin=public_origin).http_app(
        path=MCP_HTTP_PATH
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with mcp_app.lifespan(app):
            runtime.start_scheduler()
            inspector_process: InspectorProcess | None = None
            try:
                if inspector is not None:
                    inspector_process = InspectorProcess(inspector)
                    inspector_process.start()
                yield
            finally:
                if inspector_process is not None:
                    inspector_process.stop()
                runtime.close()

    async def health(_request: Request) -> JSONResponse:
        payload: dict[str, Any] = {
            **worker_identity(),
            "mode": mode,
            "mcp": {"transport": "http", "path": MCP_HTTP_PATH},
        }
        if inspector is not None:
            payload["inspector"] = {
                "url": f"http://127.0.0.1:{inspector.client_port}",
                "mcp_url": inspector.mcp_url,
            }
        return JSONResponse(payload)

    async def list_experiences(_request: Request) -> JSONResponse:
        experiences = []
        for experience in runtime.list_experiences():
            if experience.status != "active" or experience.active_revision_id is None:
                continue
            surfaces = [
                surface
                for surface in runtime.list_experience_surfaces(
                    experience.active_revision_id
                )
                if surface.surface_type == "browser"
                and runtime.resolve_experience_surface_artifact(
                    experience.active_revision_id, surface.surface_id
                )
                is not None
            ]
            if surfaces:
                experiences.append(
                    {
                        "id": experience.id,
                        "name": experience.name,
                        "surfaces": [
                            {
                                "surface_id": surface.surface_id,
                                "mount_path": surface.config["mount_path"],
                                "url": (
                                    f"/experience/{experience.id}"
                                    + (
                                        ""
                                        if surface.config["mount_path"] == "/"
                                        else surface.config["mount_path"]
                                    )
                                ),
                            }
                            for surface in surfaces
                        ],
                    }
                )
        return JSONResponse({"experiences": experiences})

    async def serve_experience_surface(request: Request) -> Response:
        experience_id = request.path_params["experience_id"]
        path = "/" + request.path_params.get("path", "").lstrip("/")
        try:
            return _serve_browser_surface(runtime, experience_id, path)
        except ExperienceAccessError as exc:
            return _experience_error(exc)

    async def serve_development_surface(request: Request) -> Response:
        deployment_id = request.path_params["deployment_id"]
        experience_id = request.path_params["experience_id"]
        path = "/" + request.path_params.get("path", "").lstrip("/")
        try:
            revision = runtime.resolve_development_experience_revision(
                deployment_id, experience_id
            )
            return _serve_browser_surface_revision(
                runtime,
                revision.id,
                path,
                document_prefix=(
                    f"/development/{deployment_id}/experience/"
                    f"{experience_id}"
                ),
                runtime_context={
                    "mode": "development",
                    "deployment_id": deployment_id,
                    "experience_revision_id": revision.id,
                    "api_base": f"/api/development/{deployment_id}",
                },
            )
        except (KeyError, ValidationFailure) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    async def list_development_objects(request: Request) -> JSONResponse:
        deployment_id = request.path_params["deployment_id"]
        application_id = request.path_params["application_id"]
        try:
            objects = runtime.development_deployment_service.list_objects(
                deployment_id,
                application_id,
                request.query_params.get("object_type_id"),
            )
            return JSONResponse(
                [item.model_dump(mode="json") for item in objects]
            )
        except (KeyError, ValidationFailure) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    async def list_development_relations(request: Request) -> JSONResponse:
        deployment_id = request.path_params["deployment_id"]
        application_id = request.path_params["application_id"]
        try:
            relations = (
                runtime.development_deployment_service.list_relations(
                    deployment_id,
                application_id,
                request.query_params.get("relation_type_id"),
                request.query_params.get("from_object_id"),
                request.query_params.get("to_object_id"),
                )
            )
            return JSONResponse(
                [item.model_dump(mode="json") for item in relations]
            )
        except (KeyError, ValidationFailure) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    async def invoke_development_action(request: Request) -> JSONResponse:
        body = await _json_object(request)
        try:
            invocation = await asyncio.to_thread(
                runtime.run_development_action,
                request.path_params["deployment_id"],
                request.path_params["application_id"],
                request.path_params["action_id"],
                body.get("input", {}),
            )
            return JSONResponse(invocation.model_dump(mode="json"))
        except (KeyError, ValidationFailure) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def submit_development_action(request: Request) -> JSONResponse:
        body = await _json_object(request)
        try:
            invocation = runtime.submit_development_action(
                request.path_params["deployment_id"],
                request.path_params["application_id"],
                request.path_params["action_id"],
                body.get("input", {}),
            )
            return JSONResponse(
                invocation.model_dump(mode="json"),
                status_code=202,
            )
        except (KeyError, ValidationFailure) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def inspect_invocation(request: Request) -> JSONResponse:
        try:
            invocation = runtime.action_supervisor.inspect(
                request.path_params["invocation_id"]
            )
            return JSONResponse(invocation.model_dump(mode="json"))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    async def cancel_invocation(request: Request) -> JSONResponse:
        try:
            invocation = await asyncio.to_thread(
                runtime.cancel_invocation,
                request.path_params["invocation_id"],
            )
            return JSONResponse(invocation.model_dump(mode="json"))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValidationFailure as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    async def deliver_development_file(request: Request) -> Response:
        try:
            delivered = (
                runtime.development_deployment_service.deliver_file(
                    request.path_params["deployment_id"],
                    request.path_params["application_id"],
                    request.path_params["token"],
                )
            )
            response = FileResponse(
                delivered.path,
                media_type=delivered.content_type,
                filename=delivered.filename,
                content_disposition_type=delivered.disposition,
            )
            if delivered.filename is None:
                response.headers["Content-Disposition"] = (
                    delivered.disposition
                )
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except FileDeliveryError as exc:
            return JSONResponse(
                {"error": str(exc), "code": exc.code},
                status_code=exc.status_code,
            )
        except (KeyError, ValidationFailure) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    async def list_objects(request: Request) -> JSONResponse:
        application_id = request.path_params["application_id"]
        object_type_id = request.query_params.get("object_type_id")
        return JSONResponse(
            {
                "objects": [
                    obj.model_dump(mode="json")
                    for obj in runtime.list_application_objects(
                        application_id, object_type_id
                    )
                ]
            }
        )

    async def list_relations(request: Request) -> JSONResponse:
        application_id = request.path_params["application_id"]
        return JSONResponse(
            {
                "relations": [
                    relation.model_dump(mode="json")
                    for relation in runtime.list_application_relations(
                        application_id,
                        relation_type_id=request.query_params.get("relation_type_id"),
                        from_object_id=request.query_params.get("from_object_id"),
                        to_object_id=request.query_params.get("to_object_id"),
                    )
                ]
            }
        )

    async def invoke_action(request: Request) -> JSONResponse:
        application_id = request.path_params["application_id"]
        action_id = request.path_params["action_id"]
        body: dict[str, Any] = await request.json()
        invocation = await asyncio.to_thread(
            runtime.run_active_action,
            application_id,
            action_id,
            body.get("input", {}),
        )
        return JSONResponse(invocation.model_dump(mode="json"))

    async def submit_action(request: Request) -> JSONResponse:
        body = await _json_object(request)
        try:
            invocation = runtime.submit_active_action(
                request.path_params["application_id"],
                request.path_params["action_id"],
                body.get("input", {}),
            )
            return JSONResponse(
                invocation.model_dump(mode="json"),
                status_code=202,
            )
        except (KeyError, ValidationFailure) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def list_experience_objects(request: Request) -> JSONResponse:
        try:
            objects = runtime.list_experience_application_objects(
                request.path_params["experience_id"],
                request.path_params["application_id"],
                request.query_params.get("object_type_id"),
            )
            return JSONResponse(
                {"objects": [item.model_dump(mode="json") for item in objects]}
            )
        except ExperienceAccessError as exc:
            return _experience_error(exc)

    async def list_experience_relations(request: Request) -> JSONResponse:
        try:
            relations = runtime.list_experience_application_relations(
                request.path_params["experience_id"],
                request.path_params["application_id"],
                request.query_params.get("relation_type_id"),
                request.query_params.get("from_object_id"),
                request.query_params.get("to_object_id"),
            )
            return JSONResponse(
                {"relations": [item.model_dump(mode="json") for item in relations]}
            )
        except ExperienceAccessError as exc:
            return _experience_error(exc)

    async def invoke_experience_action(request: Request) -> JSONResponse:
        try:
            body = await _json_object(request)
            input_value = body.get("input", {})
            if not isinstance(input_value, dict):
                raise ExperienceAccessError(
                    "invalid_input", "input must be a JSON object", 400
                )
            invocation = await asyncio.to_thread(
                runtime.invoke_experience_application_action,
                request.path_params["experience_id"],
                request.path_params["application_id"],
                request.path_params["action_id"],
                input_value,
            )
            payload = {
                "id": invocation.id,
                "status": invocation.status,
                "output": invocation.output,
                "error": public_invocation_error(invocation),
                "metadata": invocation.metadata,
            }
            return JSONResponse(payload)
        except ExperienceAccessError as exc:
            return _experience_error(exc)

    async def deliver_experience_file(request: Request) -> Response:
        try:
            delivered = runtime.deliver_experience_application_file(
                request.path_params["experience_id"],
                request.path_params["application_id"],
                request.path_params["token"],
            )
            response = FileResponse(
                delivered.path,
                media_type=delivered.content_type,
                filename=delivered.filename,
                content_disposition_type=delivered.disposition,
            )
            if delivered.filename is None:
                response.headers["Content-Disposition"] = delivered.disposition
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except ExperienceAccessError as exc:
            return _experience_error(exc)

    async def manage_experience_secret(request: Request) -> JSONResponse:
        experience_id = request.path_params["experience_id"]
        application_id = request.path_params["application_id"]
        reference_id = request.path_params["secret_reference_id"]
        try:
            if request.method == "GET":
                payload = runtime.experience_secret_status(
                    experience_id, application_id, reference_id
                )
            elif request.method == "PUT":
                body = await _json_object(request)
                if set(body) != {"value"} or not isinstance(body["value"], str):
                    raise ExperienceAccessError(
                        "invalid_input",
                        "request body must contain only a string value",
                        400,
                    )
                payload = runtime.set_experience_secret_value(
                    experience_id,
                    application_id,
                    reference_id,
                    body["value"],
                )
            else:
                payload = runtime.delete_experience_secret_value(
                    experience_id, application_id, reference_id
                )
            return JSONResponse(payload)
        except ExperienceAccessError as exc:
            return _experience_error(exc)
        except SecretBackendError:
            return JSONResponse(
                {
                    "code": "secret_backend_error",
                    "message": "secret backend operation failed",
                },
                status_code=502,
            )

    async def list_experience_secrets(request: Request) -> JSONResponse:
        try:
            payload = runtime.list_experience_secrets(
                request.path_params["experience_id"],
                request.path_params["application_id"],
            )
            return JSONResponse(payload)
        except ExperienceAccessError as exc:
            return _experience_error(exc)
        except SecretBackendError:
            return JSONResponse(
                {
                    "code": "secret_backend_error",
                    "message": "secret backend operation failed",
                },
                status_code=502,
            )

    async def component_palette(_request: Request) -> JSONResponse:
        return JSONResponse(runtime.inspect_component_palette())

    async def authoring_guide(_request: Request) -> JSONResponse:
        return JSONResponse(runtime.inspect_authoring_guide())

    async def surface_theme(_request: Request) -> JSONResponse:
        return JSONResponse(runtime.platform_authoring_service.inspect_surface_theme())

    async def experience_bridge_contract(_request: Request) -> JSONResponse:
        return JSONResponse(runtime.inspect_experience_bridge())

    async def dispatch_callback(request: Request) -> JSONResponse:
        body: Any = None
        if request.method == "POST":
            content_type = request.headers.get("content-type", "")
            body = (
                await request.json()
                if "application/json" in content_type
                else (await request.body()).decode("utf-8")
            )
        payload = {
            "method": request.method,
            "query": dict(request.query_params),
            "state": request.query_params.get("state"),
            "body": body,
            "headers": {
                key: value
                for key, value in request.headers.items()
                if key.lower() in {"content-type", "user-agent"}
            },
        }
        invocation = await asyncio.to_thread(
            runtime.dispatch_callback_route,
            request.path_params["route_id"],
            payload,
        )
        return JSONResponse(invocation.model_dump(mode="json"))

    async def system_health(_request: Request) -> JSONResponse:
        return JSONResponse(worker_identity())

    async def info(_request: Request) -> JSONResponse:
        return JSONResponse(system_info())

    async def list_packages(_request: Request) -> JSONResponse:
        archives = []
        for item in runtime.list_packages():
            archives.append(
                {
                    **item,
                    "url": f"{public_origin.rstrip('/')}/packages/{item['archive_name']}",
                }
            )
        return JSONResponse(
            {
                "upload_url": f"{public_origin.rstrip('/')}/packages",
                "archives": archives,
            }
        )

    async def get_package(request: Request) -> Response:
        archive_name = request.path_params["archive_name"]
        try:
            path = runtime.package_service.archive_path(archive_name)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "archive not found"}, status_code=404)
        return FileResponse(
            path,
            filename=archive_name,
            media_type="application/octet-stream",
        )

    async def put_package(request: Request) -> JSONResponse:
        archive_name = request.path_params["archive_name"]
        overwrite = request.query_params.get("overwrite", "").lower() in {
            "1",
            "true",
            "yes",
        }
        expected = request.headers.get("content-sha256")
        try:
            destination = runtime.package_service.archive_path(archive_name)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        runtime.package_service.package_root.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.incoming"
        )
        size = 0
        hasher = hashlib.sha256()
        try:
            with temporary.open("wb") as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > PACKAGE_MAX_ARCHIVE_BYTES:
                        raise ValidationFailure(
                            "package_corrupt: archive is too large"
                        )
                    hasher.update(chunk)
                    handle.write(chunk)
            if expected:
                digest = "sha256:" + hasher.hexdigest()
                if PackageService.normalize_sha256(expected) != digest:
                    raise ValidationFailure(
                        "package_corrupt: content hash mismatch"
                    )
            staged = runtime.stage_package_from_path(
                archive_name,
                temporary,
                overwrite=overwrite,
                expected_sha256=expected,
            )
            return JSONResponse(staged)
        except FileExistsError:
            return JSONResponse({"error": "archive already exists"}, status_code=409)
        except ValidationFailure as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        finally:
            if temporary.exists():
                temporary.unlink()

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/n4x/system/health", system_health, methods=["GET"]),
            Route("/n4x/system/info", info, methods=["GET"]),
            Route("/packages", list_packages, methods=["GET"]),
            Route("/packages/{archive_name:str}", get_package, methods=["GET"]),
            Route("/packages/{archive_name:str}", put_package, methods=["PUT"]),
            Route("/experiences", list_experiences, methods=["GET"]),
            Route(
                (
                    "/api/experiences/{experience_id:str}/apps/"
                    "{application_id:str}/secrets"
                ),
                list_experience_secrets,
                methods=["GET"],
            ),
            Route(
                (
                    "/api/experiences/{experience_id:str}/apps/"
                    "{application_id:str}/objects"
                ),
                list_experience_objects,
                methods=["GET"],
            ),
            Route(
                (
                    "/api/experiences/{experience_id:str}/apps/"
                    "{application_id:str}/relations"
                ),
                list_experience_relations,
                methods=["GET"],
            ),
            Route(
                (
                    "/api/experiences/{experience_id:str}/apps/"
                    "{application_id:str}/actions/{action_id:str}/invoke"
                ),
                invoke_experience_action,
                methods=["POST"],
            ),
            Route(
                (
                    "/api/experiences/{experience_id:str}/apps/"
                    "{application_id:str}/files/{token:str}"
                ),
                deliver_experience_file,
                methods=["GET", "HEAD"],
            ),
            Route(
                (
                    "/api/experiences/{experience_id:str}/apps/"
                    "{application_id:str}/secrets/{secret_reference_id:str}"
                ),
                manage_experience_secret,
                methods=["GET", "PUT", "DELETE"],
            ),
            Route(
                "/api/apps/{application_id:str}/objects", list_objects, methods=["GET"]
            ),
            Route(
                "/api/apps/{application_id:str}/relations",
                list_relations,
                methods=["GET"],
            ),
            Route(
                "/api/apps/{application_id:str}/actions/{action_id:path}/invoke",
                invoke_action,
                methods=["POST"],
            ),
            Route(
                "/api/apps/{application_id:str}/actions/{action_id:path}/submit",
                submit_action,
                methods=["POST"],
            ),
            Route(
                (
                    "/api/development/{deployment_id:str}/apps/"
                    "{application_id:str}/objects"
                ),
                list_development_objects,
                methods=["GET"],
            ),
            Route(
                (
                    "/api/development/{deployment_id:str}/apps/"
                    "{application_id:str}/relations"
                ),
                list_development_relations,
                methods=["GET"],
            ),
            Route(
                (
                    "/api/development/{deployment_id:str}/apps/"
                    "{application_id:str}/actions/{action_id:path}/invoke"
                ),
                invoke_development_action,
                methods=["POST"],
            ),
            Route(
                (
                    "/api/development/{deployment_id:str}/apps/"
                    "{application_id:str}/actions/{action_id:path}/submit"
                ),
                submit_development_action,
                methods=["POST"],
            ),
            Route(
                "/api/invocations/{invocation_id:str}",
                inspect_invocation,
                methods=["GET"],
            ),
            Route(
                "/api/invocations/{invocation_id:str}",
                cancel_invocation,
                methods=["DELETE"],
            ),
            Route(
                (
                    "/api/development/{deployment_id:str}/apps/"
                    "{application_id:str}/files/{token:str}"
                ),
                deliver_development_file,
                methods=["GET", "HEAD"],
            ),
            Route("/bridge/authoring-guide", authoring_guide, methods=["GET"]),
            Route("/bridge/component-palette", component_palette, methods=["GET"]),
            Route("/bridge/theme", surface_theme, methods=["GET"]),
            Route("/bridge/contract", experience_bridge_contract, methods=["GET"]),
            Route(
                "/callback/{route_id:str}", dispatch_callback, methods=["GET", "POST"]
            ),
            *mcp_app.routes,
            Route(
                "/experience/{experience_id:str}",
                serve_experience_surface,
                methods=["GET"],
            ),
            Route(
                "/experience/{experience_id:str}/{path:path}",
                serve_experience_surface,
                methods=["GET"],
            ),
            Route(
                (
                    "/development/{deployment_id:str}/experience/"
                    "{experience_id:str}"
                ),
                serve_development_surface,
                methods=["GET"],
            ),
            Route(
                (
                    "/development/{deployment_id:str}/experience/"
                    "{experience_id:str}/{path:path}"
                ),
                serve_development_surface,
                methods=["GET"],
            ),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            ),
            *mcp_app.user_middleware,
        ],
        lifespan=lifespan,
    )


def _serve_browser_surface(
    runtime: SystemRuntime, experience_id: str, request_path: str
) -> Response:
    revision = runtime.experience_access_service.active_revision(experience_id)
    return _serve_browser_surface_revision(
        runtime,
        revision.id,
        request_path,
        document_prefix=f"/experience/{experience_id}",
    )


def _serve_browser_surface_revision(
    runtime: SystemRuntime,
    experience_revision_id: str,
    request_path: str,
    *,
    document_prefix: str,
    runtime_context: dict[str, Any] | None = None,
) -> Response:
    candidates = []
    for surface in runtime.list_experience_surfaces(experience_revision_id):
        if surface.surface_type != "browser":
            continue
        config = BrowserSurfaceConfig.model_validate(surface.config)
        mount = config.mount_path
        if mount == "/" or request_path == mount or request_path.startswith(f"{mount}/"):
            candidates.append((len(mount), surface, config))
    if not candidates:
        return JSONResponse({"error": "browser surface not found"}, status_code=404)
    _, surface, config = max(candidates, key=lambda item: item[0])
    artifact = runtime.resolve_experience_surface_artifact(
        experience_revision_id, surface.surface_id
    )
    if artifact is None or artifact.manifest is None:
        return JSONResponse(
            {"error": "surface artifact is not built", "code": "surface_artifact_missing"},
            status_code=503,
        )
    root_value = artifact.manifest.get("root")
    index_value = artifact.manifest.get("index")
    if not isinstance(root_value, str) or not isinstance(index_value, str):
        return JSONResponse({"error": "invalid surface manifest"}, status_code=503)
    root = Path(root_value).resolve()
    relative = (
        request_path.lstrip("/")
        if config.mount_path == "/"
        else request_path[len(config.mount_path) :].lstrip("/")
    )
    try:
        requested = resolve_path_within(root, relative, allow_root=True)
    except PathContainmentError:
        return JSONResponse({"error": "invalid surface asset path"}, status_code=400)
    if requested.is_dir():
        requested = requested / "index.html"
    if not requested.is_file():
        if _request_has_file_extension(relative):
            return JSONResponse({"error": "surface asset not found"}, status_code=404)
        try:
            fallback_relative = (
                config.fallback.lstrip("/")
                if config.fallback is not None
                else str(Path(index_value).resolve().relative_to(root))
            )
            fallback = resolve_path_within(root, fallback_relative)
        except (PathContainmentError, ValueError):
            return JSONResponse({"error": "invalid surface fallback"}, status_code=503)
        requested = fallback
    if not requested.is_file():
        return JSONResponse({"error": "surface asset not found"}, status_code=404)
    document_base = _browser_document_base(document_prefix, config.mount_path)
    if (
        config.pwa is not None
        and relative == config.pwa.manifest_path
    ):
        return _serve_pwa_manifest(requested, config, document_base)
    response: Response
    if requested.suffix.lower() == ".html":
        document = _anchor_browser_html(
            requested.read_text(encoding="utf-8"),
            document_base,
        )
        if runtime_context is not None:
            document = _inject_runtime_context(document, runtime_context)
        response = HTMLResponse(document)
    elif requested.suffix.lower() == ".webmanifest":
        response = FileResponse(
            requested, media_type="application/manifest+json"
        )
    else:
        response = FileResponse(requested)
    response.headers["Content-Security-Policy"] = _browser_csp(config)
    return response


def _browser_document_base(document_prefix: str, mount_path: str) -> str:
    mount = "" if mount_path == "/" else mount_path
    return f"{document_prefix}{mount}/"


def _request_has_file_extension(relative: str) -> bool:
    if not relative:
        return False
    name = relative.rsplit("/", 1)[-1]
    if name.startswith("."):
        return "." in name[1:]
    return "." in name


def _serve_pwa_manifest(
    requested: Path, config: BrowserSurfaceConfig, document_base: str
) -> Response:
    try:
        payload = json.loads(requested.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid PWA manifest"}, status_code=503)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid PWA manifest"}, status_code=503)
    try:
        bound = _bind_pwa_manifest(payload, document_base)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    response = JSONResponse(bound, media_type="application/manifest+json")
    response.headers["Content-Security-Policy"] = _browser_csp(config)
    return response


def _bind_pwa_manifest(
    payload: dict[str, Any], document_base: str
) -> dict[str, Any]:
    bound = dict(payload)
    for key in ("start_url", "scope", "id"):
        bound[key] = _confine_manifest_url(payload.get(key), document_base)
    return bound


def _confine_manifest_url(value: object, document_base: str) -> str:
    if value is None or value == "" or value == "/":
        return document_base
    if not isinstance(value, str):
        raise ValueError("PWA manifest URL members must be strings")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        raise ValueError(
            "PWA manifest URLs must stay path-absolute within the Experience"
        )
    resolved = urlsplit(urljoin(document_base, value))
    if resolved.scheme or resolved.netloc:
        raise ValueError(
            "PWA manifest URLs must stay path-absolute within the Experience"
        )
    path = resolved.path
    if path != document_base.rstrip("/") and not path.startswith(document_base):
        raise ValueError("PWA manifest URL escapes the Experience document base")
    confined = path
    if resolved.query:
        confined += f"?{resolved.query}"
    if resolved.fragment:
        confined += f"#{resolved.fragment}"
    return confined


def _anchor_browser_html(document: str, base_href: str) -> str:
    base = f'<base href="{escape(base_href, quote=True)}">'
    existing = re.compile(r"<base\b[^>]*>", flags=re.IGNORECASE)
    if existing.search(document):
        return existing.sub(base, document, count=1)
    head = re.compile(r"<head\b[^>]*>", flags=re.IGNORECASE)
    if head.search(document):
        return head.sub(lambda match: f"{match.group(0)}{base}", document, count=1)
    return f"{base}{document}"


def _inject_runtime_context(
    document: str, runtime_context: dict[str, Any]
) -> str:
    payload = json.dumps(
        runtime_context,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    script = f"<script>window.__N4X_EXECUTION_CONTEXT__={payload};</script>"
    head = re.compile(r"<head\b[^>]*>", flags=re.IGNORECASE)
    if head.search(document):
        return head.sub(
            lambda match: f"{match.group(0)}{script}",
            document,
            count=1,
        )
    return f"{script}{document}"


def _browser_csp(config: BrowserSurfaceConfig) -> str:
    connect = " ".join(["'self'", *config.csp.connect_domains])
    resources = " ".join(["'self'", *config.csp.resource_domains])
    return (
        "default-src 'self'; "
        f"connect-src {connect}; "
        f"img-src {resources} data:; "
        f"font-src {resources}; "
        f"style-src {resources} 'unsafe-inline'; "
        f"script-src {resources}; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    )


def _experience_error(exc: ExperienceAccessError) -> JSONResponse:
    return JSONResponse(
        {"error": str(exc), "code": exc.code},
        status_code=exc.status_code,
    )


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise ExperienceAccessError(
            "invalid_input", "request body must be valid JSON", 400
        ) from exc
    if not isinstance(body, dict):
        raise ExperienceAccessError(
            "invalid_input", "request body must be a JSON object", 400
        )
    return body
