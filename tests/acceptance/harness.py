from __future__ import annotations

import asyncio
from typing import Any

from n4x.system.mcp import create_system_mcp
from n4x.testing import create_test_runtime


class McpAuthoringHarness:
    """Generic MCP caller for acceptance tests. Graph-app source stays in fixtures."""

    def __init__(self, runtime=None) -> None:
        self.runtime = runtime or create_test_runtime()
        self.server = create_system_mcp(self.runtime)

    async def call(self, tool: str, **arguments: Any) -> Any:
        result = await self.server.call_tool(tool, arguments)
        if result.structured_content is None:
            return result
        content = result.structured_content
        if isinstance(content, dict) and "result" in content and len(content) == 1:
            return content["result"]
        return content

    def run(self, tool: str, **arguments: Any) -> Any:
        return asyncio.run(self.call(tool, **arguments))

    def inspect_objects(self, **arguments: Any) -> list[dict[str, Any]]:
        arguments.setdefault("include_values", True)
        return self.run("inspect_objects", **arguments)["items"]

    def inspect_relations(self, **arguments: Any) -> list[dict[str, Any]]:
        arguments.setdefault("include_values", True)
        return self.run("inspect_relations", **arguments)["items"]

    def author_experience(
        self,
        *,
        experience_id: str,
        name: str,
        application_access: list[dict[str, Any]],
        source_files: dict[str, str],
        surfaces: list[dict[str, Any]],
        ui_profile: str = "n4x-default",
    ) -> dict[str, Any]:
        """Author and activate an Experience made only from native Surfaces."""
        experience = self.run(
            "create_experience", experience_id=experience_id, name=name
        )
        revision = self.run(
            "create_experience_revision",
            experience_id=experience_id,
            ui_profile=ui_profile,
            application_access=application_access,
        )
        for path, content in source_files.items():
            self.run(
                "write_source_file",
                source_tree_id=revision["source_tree_id"],
                path=path,
                role="surface",
                language="typescript",
                content=content,
            )
        created_surfaces = {}
        for declaration in surfaces:
            surface_id = declaration["surface_id"]
            created_surfaces[surface_id] = self.run(
                "create_experience_surface",
                experience_revision_id=revision["id"],
                **declaration,
            )
        activation = self.run(
            "activate_experience_revision",
            experience_revision_id=revision["id"],
        )
        return {
            "experience": experience,
            "revision": revision,
            "surfaces": created_surfaces,
            "activation": activation,
        }
