from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastmcp.apps.config import AppConfig, ResourceCSP, app_config_to_meta_dict
from fastmcp.resources.function_resource import FunctionResource
from fastmcp.server.providers import Provider
from fastmcp.tools.function_tool import FunctionTool
from mcp.types import ToolAnnotations

from n4x.kernel.errors import ValidationFailure, public_invocation_error
from n4x.kernel.surface_types import McpAppSurfaceConfig
from n4x.system.origins import instance_link

MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"
SURFACE_URI_PATTERN = re.compile(
    r"^ui://n4x/experience/([^/]+)/revision/([^/]+)/surface/([^/]+)$"
)
DEVELOPMENT_SURFACE_URI_PATTERN = re.compile(
    r"^ui://n4x/development/([^/]+)/experience/([^/]+)"
    r"/revision/([^/]+)/surface/([^/]+)$"
)


class McpAppSurfaceProvider(Provider):
    """Expose built MCP App Surfaces from active Experience revisions."""

    def __init__(self, runtime: Any, *, public_origin: str) -> None:
        super().__init__()
        self.runtime = runtime
        self.public_origin = public_origin.rstrip("/")

    async def _list_resources(self) -> Sequence[FunctionResource]:
        return [self._resource_for(item) for item in self._available_surfaces()]

    async def _get_resource(
        self, uri: str, version: Any = None
    ) -> FunctionResource | None:
        active = SURFACE_URI_PATTERN.fullmatch(uri)
        development = DEVELOPMENT_SURFACE_URI_PATTERN.fullmatch(uri)
        if active is None and development is None:
            return None
        for item in self._available_surfaces():
            if self._uri_for(item) == uri:
                return self._resource_for(item)
        return None

    async def _list_tools(self) -> Sequence[FunctionTool]:
        tools: list[FunctionTool] = []
        for item in self._available_surfaces():
            tools.append(self._open_tool(item))
            if item.get("deployment_id") is None:
                tools.extend(self._bridge_tools(item))
        return tools

    async def _get_tool(self, name: str, version: Any = None) -> FunctionTool | None:
        return next(
            (tool for tool in await self._list_tools() if tool.name == name), None
        )

    def _active_surfaces(self) -> list[dict[str, Any]]:
        result = []
        for experience in self.runtime.list_experiences():
            revision_id = experience.active_revision_id
            if experience.status != "active" or revision_id is None:
                continue
            for surface in self.runtime.list_experience_surfaces(revision_id):
                if surface.surface_type != "mcp_app":
                    continue
                artifact = self.runtime.resolve_experience_surface_artifact(
                    revision_id, surface.surface_id
                )
                if (
                    artifact is not None
                    and artifact.manifest is not None
                    and isinstance(artifact.manifest.get("html"), str)
                    and Path(artifact.manifest["html"]).is_file()
                ):
                    result.append(
                        {
                            "experience_id": experience.id,
                            "revision_id": revision_id,
                            "surface": surface,
                            "artifact": artifact,
                        }
                    )
        return result

    def _development_surfaces(self) -> list[dict[str, Any]]:
        result = []
        for deployment in (
            self.runtime.graph.development_deployments.values()
        ):
            try:
                deployment = (
                    self.runtime.development_deployment_service.require_available(
                        deployment.id
                    )
                )
            except ValidationFailure:
                continue
            revision = self.runtime.graph.experience_revisions[
                deployment.experience_revision_id
            ]
            for surface in self.runtime.list_experience_surfaces(
                revision.id
            ):
                if surface.surface_type != "mcp_app":
                    continue
                artifact = self.runtime.resolve_experience_surface_artifact(
                    revision.id, surface.surface_id
                )
                if (
                    artifact is not None
                    and artifact.manifest is not None
                    and isinstance(artifact.manifest.get("html"), str)
                    and Path(artifact.manifest["html"]).is_file()
                ):
                    result.append(
                        {
                            "deployment_id": deployment.id,
                            "experience_id": revision.experience_id,
                            "revision_id": revision.id,
                            "surface": surface,
                            "artifact": artifact,
                        }
                    )
        return result

    def _available_surfaces(self) -> list[dict[str, Any]]:
        return [
            *self._active_surfaces(),
            *self._development_surfaces(),
        ]

    @staticmethod
    def _uri_for(item: dict[str, Any]) -> str:
        deployment_id = item.get("deployment_id")
        if deployment_id is None:
            return _surface_uri(
                item["experience_id"],
                item["revision_id"],
                item["surface"].surface_id,
            )
        return _development_surface_uri(
            deployment_id,
            item["experience_id"],
            item["revision_id"],
            item["surface"].surface_id,
        )

    def _resource_for(self, item: dict[str, Any]) -> FunctionResource:
        surface = item["surface"]
        uri = self._uri_for(item)
        html_path = Path(item["artifact"].manifest["html"])

        def read_html() -> str:
            document = html_path.read_text(encoding="utf-8")
            deployment_id = item.get("deployment_id")
            if deployment_id is None:
                return document
            payload = json.dumps(
                {
                    "mode": "development",
                    "deployment_id": deployment_id,
                    "experience_revision_id": item["revision_id"],
                },
                separators=(",", ":"),
            ).replace("<", "\\u003c")
            script = (
                "<script>window.__N4X_EXECUTION_CONTEXT__="
                f"{payload};</script>"
            )
            return document.replace("<head>", f"<head>{script}", 1)

        return FunctionResource.from_function(
            read_html,
            uri=uri,
            name=f"{item['experience_id']}/{surface.surface_id}",
            description=surface.description or surface.title or surface.surface_id,
            mime_type=MCP_APP_MIME_TYPE,
            meta=self._resource_meta(surface),
        )

    def _open_tool(self, item: dict[str, Any]) -> FunctionTool:
        surface = item["surface"]
        experience_id = item["experience_id"]
        uri = self._uri_for(item)
        config = McpAppSurfaceConfig.model_validate(surface.config)
        ui = app_config_to_meta_dict(
            AppConfig(
                resource_uri=uri,
                visibility=["app", "model"],
                csp=self._csp(config),
            )
        )

        def open_surface() -> dict[str, str]:
            result = {
                "experience_id": experience_id,
                "surface_id": surface.surface_id,
                "revision_id": item["revision_id"],
                "ui_uri": uri,
            }
            deployment_id = item.get("deployment_id")
            if deployment_id is not None:
                result["deployment_id"] = deployment_id
            if config.related_browser_path is not None:
                if deployment_id is None:
                    path = (
                        f"/experience/{experience_id}"
                        f"{config.related_browser_path}"
                    )
                else:
                    path = (
                        f"/development/{deployment_id}"
                        f"/experience/{experience_id}"
                        f"{config.related_browser_path}"
                    )
                link = instance_link(self.public_origin, path)
                result["related_browser_url"] = link["url"]
                result["origin_kind"] = link["origin_kind"]
                result["host_bind_url"] = link["host_bind_url"]
            return result

        operation = (
            "open"
            if item.get("deployment_id") is None
            else f"open_development_{item['deployment_id']}"
        )
        name = _surface_tool_name(
            operation, experience_id, surface.surface_id
        )
        open_surface.__name__ = name
        return FunctionTool.from_function(
            open_surface,
            name=name,
            description=surface.description or f"Open {surface.surface_id}",
            annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
            meta={"ui": ui, "openai/outputTemplate": uri},
        )

    def _bridge_tools(self, item: dict[str, Any]) -> list[FunctionTool]:
        experience_id = item["experience_id"]
        surface = item["surface"]
        uri = _surface_uri(experience_id, item["revision_id"], surface.surface_id)
        app_meta = {
            "ui": {"visibility": ["app"]},
            "fastmcp": {"app": uri},
        }
        result: list[FunctionTool] = []
        revision = self.runtime.experience_access_service.active_revision(experience_id)
        for access in revision.application_access:
            application_id = access.application_id

            def object_lister(application_id: str):
                def list_objects(
                    object_type_id: str | None = None,
                ) -> list[dict[str, Any]]:
                    return [
                        item.model_dump(mode="json")
                        for item in self.runtime.list_experience_application_objects(
                            experience_id, application_id, object_type_id
                        )
                    ]

                return list_objects

            def relation_lister(application_id: str):
                def list_relations(
                    relation_type_id: str | None = None,
                    from_object_id: str | None = None,
                    to_object_id: str | None = None,
                ) -> list[dict[str, Any]]:
                    return [
                        item.model_dump(mode="json")
                        for item in self.runtime.list_experience_application_relations(
                            experience_id,
                            application_id,
                            relation_type_id,
                            from_object_id,
                            to_object_id,
                        )
                    ]

                return list_relations

            def action_invoker(application_id: str):
                def invoke(action_id: str, input: dict[str, Any]) -> dict[str, Any]:
                    invocation = self.runtime.invoke_experience_application_action(
                        experience_id, application_id, action_id, input
                    )
                    return {
                        "id": invocation.id,
                        "status": invocation.status,
                        "output": _absolute_file_delivery_urls(
                            invocation.output, self.public_origin
                        ),
                        "error": public_invocation_error(invocation),
                        "metadata": invocation.metadata,
                    }

                return invoke

            for operation, function, description, read_only in (
                (
                    "list_objects",
                    object_lister(application_id),
                    f"List {application_id} objects",
                    True,
                ),
                (
                    "list_relations",
                    relation_lister(application_id),
                    f"List {application_id} relations",
                    True,
                ),
                (
                    "invoke",
                    action_invoker(application_id),
                    f"Invoke an allowed {application_id} action",
                    False,
                ),
            ):
                name = _surface_tool_name(
                    operation, experience_id, surface.surface_id, application_id
                )
                function.__name__ = name
                result.append(
                    FunctionTool.from_function(
                        function,
                        name=name,
                        description=description,
                        annotations=ToolAnnotations(
                            readOnlyHint=read_only, openWorldHint=False
                        ),
                        meta=app_meta,
                    )
                )
        return result

    def _resource_meta(self, surface: Any) -> dict[str, Any]:
        config = McpAppSurfaceConfig.model_validate(surface.config)
        csp = self._csp(config)
        csp_wire = csp.model_dump(by_alias=True, exclude_none=True)
        metadata = dict(config.metadata)
        metadata["ui"] = {
            **(metadata.get("ui") if isinstance(metadata.get("ui"), dict) else {}),
            **app_config_to_meta_dict(AppConfig(csp=csp)),
        }
        # Inspector 2.x compatibility.
        metadata["csp"] = csp_wire
        if config.related_browser_path is not None:
            metadata["related_browser_path"] = config.related_browser_path
        return metadata

    def _csp(self, config: McpAppSurfaceConfig) -> ResourceCSP:
        return ResourceCSP(
            connect_domains=list(
                dict.fromkeys([self.public_origin, *config.csp.connect_domains])
            ),
            resource_domains=config.csp.resource_domains,
        )


def _surface_uri(experience_id: str, revision_id: str, surface_id: str) -> str:
    return (
        f"ui://n4x/experience/{experience_id}/revision/{revision_id}"
        f"/surface/{surface_id}"
    )


def _development_surface_uri(
    deployment_id: str,
    experience_id: str,
    revision_id: str,
    surface_id: str,
) -> str:
    return (
        f"ui://n4x/development/{deployment_id}/experience/{experience_id}"
        f"/revision/{revision_id}/surface/{surface_id}"
    )


def _absolute_file_delivery_urls(output: Any, public_origin: str) -> Any:
    if not isinstance(output, dict) or not isinstance(
        output.get("file_deliveries"), list
    ):
        return output
    converted = dict(output)
    converted["file_deliveries"] = [
        {
            **item,
            "url": f"{public_origin}{item['url']}",
        }
        if isinstance(item, dict)
        and isinstance(item.get("url"), str)
        and item["url"].startswith("/")
        else item
        for item in output["file_deliveries"]
    ]
    return converted


def _surface_tool_name(
    operation: str,
    experience_id: str,
    surface_id: str,
    application_id: str | None = None,
) -> str:
    parts = [operation, experience_id, surface_id]
    if application_id is not None:
        parts.append(application_id)
    return re.sub(r"[^A-Za-z0-9]+", "_", "_".join(parts)).strip("_")
