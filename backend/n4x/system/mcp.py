"""System MCP catalog. Host does not import this module."""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.server.middleware import Middleware
from mcp.types import (
    ResourceListChangedNotification,
    ToolAnnotations,
    ToolListChangedNotification,
)

from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import SourceFileSummary
from n4x.mcp.client_guide import (
    APPLICATION_AUTHORING_NEXT_STEP,
    ClientGuideTopic,
    INSTRUCTIONS,
    inspect_client_guide as load_client_guide,
)
from n4x.mcp.surface_apps import McpAppSurfaceProvider
from n4x.system.development_preview import (
    development_deployment_payload,
    with_development_preview,
)
from n4x.system.host_control import request_host_control
from n4x.system.inspect import system_info, worker_identity
from n4x.system.inspect_bounds import (
    INSPECT_DEFAULT_LIMIT,
    model_summary,
    page_inspect,
)
from n4x.system.runtime import SystemRuntime

_READ = {
    "annotations": ToolAnnotations(readOnlyHint=True, openWorldHint=False)
}
_READ_HOST = {
    "annotations": ToolAnnotations(readOnlyHint=True, openWorldHint=True)
}
_WRITE = {
    "annotations": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, openWorldHint=False
    )
}
_DESTROY = {
    "annotations": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, openWorldHint=False
    )
}
_WRITE_HOST = {
    "annotations": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, openWorldHint=True
    )
}
_DESTROY_HOST = {
    "annotations": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, openWorldHint=True
    )
}


def _optional_enum(
    field: str,
    value: str | None,
    allowed: tuple[str, ...],
    default: str | None = None,
) -> str:
    if value is None:
        if default is None:
            raise ValidationFailure(f"{field} is required", field=field)
        return default
    if value not in allowed:
        raise ValidationFailure(f"invalid {field}: {value}", field=field)
    return value


class _HandshakeCatalogMiddleware(Middleware):
    """Re-list after a new MCP handshake.

    Worker replacement keeps the same /mcp URL. Clients that cache tools/list
    until notified would otherwise keep the previous image's catalog.
    """

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        session = context.fastmcp_context
        if session is not None:
            await session.send_notification(ResourceListChangedNotification())
            await session.send_notification(ToolListChangedNotification())
        return result


def _attached_database(runtime: SystemRuntime) -> str | None:
    graph = getattr(runtime.store, "graph", None)
    config = getattr(graph, "config", None)
    database = getattr(config, "database", None)
    return database if isinstance(database, str) and database else None


def _entity_inventory(
    runtime: SystemRuntime,
    entities,
    revisions,
    owner_field: str,
) -> list[dict[str, Any]]:
    bindings = runtime.source.bindings
    items = []
    for entity in sorted(entities, key=lambda item: item.id):
        drafts = [
            revision
            for revision in revisions
            if getattr(revision, owner_field) == entity.id
            and revision.status == "draft"
        ]
        draft = (
            max(drafts, key=lambda item: item.created_at) if drafts else None
        )

        def tree_bits(revision_id: str | None) -> tuple[str | None, str | None]:
            if not revision_id:
                return None, None
            tree = bindings.tree(revision_id)
            return tree.id, tree.status

        draft_tree, draft_status = tree_bits(None if draft is None else draft.id)
        active_tree, active_status = tree_bits(entity.active_revision_id)
        items.append(
            {
                "id": entity.id,
                "name": entity.name,
                "status": entity.status,
                "active_revision_id": entity.active_revision_id,
                "draft_revision_id": None if draft is None else draft.id,
                "source_tree_id": draft_tree or active_tree,
                "tree_status": draft_status or active_status,
            }
        )
    return items


def create_system_mcp(
    runtime: SystemRuntime | None = None,
    *,
    public_origin: str = "http://127.0.0.1:7744",
) -> FastMCP:
    runtime = runtime or SystemRuntime.from_env()
    reset_database = _attached_database(runtime)
    content_root = worker_identity().get("content_root") or "n4x"
    mcp = FastMCP(
        "N4X",
        instructions=INSTRUCTIONS,
        version=str(content_root),
        middleware=[_HandshakeCatalogMiddleware()],
        providers=[McpAppSurfaceProvider(runtime, public_origin=public_origin)],
    )

    @mcp.tool(**_READ)
    def inspect_system() -> dict[str, Any]:
        """Inspect the enabled System revision and authoring inventory."""
        info = system_info()
        enabled = None
        try:
            system = runtime.uow.records.systems.get("n4x")
            if system is not None and system.active_revision_id:
                revision = runtime.uow.records.system_revisions.get(
                    system.active_revision_id
                )
                if revision is not None:
                    enabled = revision
        except Exception:
            enabled = None
        if enabled is not None:
            info.update(
                {
                    "revision_id": enabled.id,
                    "source_tree_id": runtime.source.bindings.tree_id(enabled.id),
                    "tree_status": runtime.source.bindings.tree(enabled.id).status,
                    "content_root": enabled.content_root,
                    "provenance_kind": enabled.provenance_kind,
                }
            )
        info["applications"] = _entity_inventory(
            runtime,
            runtime.uow.records.applications.values(),
            runtime.uow.records.revisions.values(),
            "application_id",
        )
        info["experiences"] = _entity_inventory(
            runtime,
            runtime.uow.records.experiences.values(),
            runtime.uow.records.experience_revisions.values(),
            "experience_id",
        )
        return info

    @mcp.tool(**_READ)
    def inspect_client_guide(
        topic: ClientGuideTopic | None = None,
    ) -> dict[str, Any]:
        """Inspect System MCP client-usage playbooks. Omit topic for the catalog.

        Use this before Application or Experience authoring. Do not use
        inspect_authoring_guide for catalog usage; that tool returns the
        graph-owned UI authoring guide.
        """
        return load_client_guide(topic)

    @mcp.tool(**_READ_HOST)
    def inspect_official_release() -> dict[str, Any]:
        """Compare the running System to the official release index via Host."""
        return request_host_control("GET", "/n4x-host/release")

    @mcp.tool(**_WRITE_HOST)
    def import_official_system(archive: str) -> dict[str, Any]:
        """Import an official System zip as a new SystemRevision. Does not enable."""
        return request_host_control(
            "POST", "/n4x-host/import", {"archive": archive}
        )

    @mcp.tool(**_WRITE_HOST)
    def enable_system_revision(revision_id: str) -> dict[str, Any]:
        """Enable a SystemRevision: point the graph edge, rematerialize, restart."""
        return request_host_control(
            "POST", "/n4x-host/enable", {"revision_id": revision_id}
        )

    @mcp.tool(**_WRITE_HOST)
    def dump_instance(
        output: str | None = None,
        neo4j_dump: str | None = None,
    ) -> dict[str, Any]:
        """Ask Host to write an instance bundle. Returns path and download_path.

        The archive is not inlined in the tool result. Download it from
        download_path (OIDC ingress) or copy the file on the box.
        """
        payload: dict[str, Any] = {}
        if output:
            payload["output"] = output
        if neo4j_dump:
            payload["neo4j_dump"] = neo4j_dump
        return request_host_control("POST", "/n4x-host/dump", payload)

    @mcp.tool(**_READ_HOST)
    def inspect_instance_dumps() -> dict[str, Any]:
        """List Host export bundles and their download paths."""
        return request_host_control("GET", "/n4x-host/exports")

    @mcp.tool(**_WRITE)
    def create_application(
        application_id: str, name: str, description: str = ""
    ) -> dict[str, Any]:
        """Create an N4X application namespace."""
        result = runtime.applications.create(
            application_id, name, description
        ).model_dump(mode="json")
        result["authoring_next_step"] = APPLICATION_AUTHORING_NEXT_STEP
        return result

    @mcp.tool(**_WRITE)
    def create_development_data_space(
        application_id: str,
        data_space_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an empty isolated development DataSpace for an Application."""
        return runtime.data_spaces.create_development(
            application_id,
            data_space_id=data_space_id,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def create_development_deployment(
        experience_revision_id: str,
        application_revision_ids: dict[str, str],
        ttl_seconds: int | None = None,
        initialization: Literal["empty", "clone"] | None = None,
        clone_specs: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Deploy draft candidates into empty isolated DataSpaces."""
        initialization = _optional_enum(
            "initialization", initialization, ("empty", "clone"), "empty"
        )
        return development_deployment_payload(
            runtime,
            runtime.development_deployments.create(
                experience_revision_id,
                application_revision_ids,
                ttl_seconds=3600 if ttl_seconds is None else ttl_seconds,
                initialization=initialization,
                clone_specs=clone_specs,
            ),
            public_origin,
        )

    @mcp.tool(**_READ)
    def inspect_development_deployment(
        deployment_id: str,
    ) -> dict[str, Any]:
        """Inspect one logical single-stack development deployment."""
        return development_deployment_payload(
            runtime,
            runtime.development_deployments.get(deployment_id),
            public_origin,
        )

    @mcp.tool(**_READ)
    def list_development_objects(
        deployment_id: str,
        application_id: str,
        object_type_id: str | None = None,
        offset: int = 0,
        limit: int = INSPECT_DEFAULT_LIMIT,
        include_values: bool = False,
        object_id: str | None = None,
    ) -> dict[str, Any]:
        """List candidate-visible objects from a deployment DataSpace."""
        return page_inspect(
            runtime.development_deployments.list_objects(
                deployment_id,
                application_id,
                object_type_id,
            ),
            offset=offset,
            limit=limit,
            include_values=include_values,
            dump=lambda item: item.model_dump(mode="json"),
            summary=model_summary,
            item_id=object_id,
            id_of=lambda item: item.id,
        )

    @mcp.tool(**_READ)
    def list_development_relations(
        deployment_id: str,
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
        offset: int = 0,
        limit: int = INSPECT_DEFAULT_LIMIT,
        include_values: bool = False,
        relation_id: str | None = None,
    ) -> dict[str, Any]:
        """List candidate-visible relations from a deployment DataSpace."""
        return page_inspect(
            runtime.development_deployments.list_relations(
                deployment_id,
                application_id,
                relation_type_id,
                from_object_id,
                to_object_id,
            ),
            offset=offset,
            limit=limit,
            include_values=include_values,
            dump=lambda item: item.model_dump(mode="json"),
            summary=model_summary,
            item_id=relation_id,
            id_of=lambda item: item.id,
        )

    @mcp.tool(**_WRITE)
    def run_development_action(
        deployment_id: str,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a candidate Action in its deployment-bound DataSpace."""
        return runtime.development_deployments.run_action(
            deployment_id,
            application_id,
            action_id,
            input_value,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def submit_development_action(
        deployment_id: str,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ) -> dict[str, Any]:
        """Queue a candidate Action in its deployment-bound DataSpace."""
        return runtime.development_deployments.submit_action(
            deployment_id,
            application_id,
            action_id,
            input_value,
        ).model_dump(mode="json")

    @mcp.tool(**_DESTROY)
    def expire_development_deployment(
        deployment_id: str,
    ) -> dict[str, Any]:
        """Expire a development deployment and reject further execution."""
        return runtime.development_deployments.expire(deployment_id).model_dump(
            mode="json"
        )

    @mcp.tool(**_WRITE)
    def create_application_revision(
        application_id: str,
        ui_profile: Literal["n4x-default", "custom", "none"] | None = None,
        parent_revision_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a backend Application draft."""
        prior = next(
            (
                item
                for item in runtime.uow.applications.list_revisions(application_id)
                if item.status == "draft"
            ),
            None,
        )
        revision = runtime.applications.create_revision(
            application_id,
            ui_profile=ui_profile,
            parent_revision_id=parent_revision_id,
        )
        result = runtime.source.bindings.payload(revision.id)
        result["reused"] = prior is not None and prior.id == revision.id
        result["authoring_next_step"] = APPLICATION_AUTHORING_NEXT_STEP
        return result

    @mcp.tool(**_DESTROY)
    def discard_application_revision(application_revision_id: str) -> dict[str, Any]:
        """Discard a draft ApplicationRevision and its working tree."""
        runtime.applications.discard_revision(application_revision_id)
        return {
            "discarded": True,
            "application_revision_id": application_revision_id,
        }

    @mcp.tool(**_WRITE)
    def create_runtime_dependency(
        application_revision_id: str,
        ecosystem: str,
        package: str,
        spec: str,
    ) -> dict[str, Any]:
        """Declare a Python dependency on a draft ApplicationRevision."""
        return runtime.applications.create_runtime_dependency(
            application_revision_id, ecosystem, package, spec
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def create_object_type(
        application_revision_id: str,
        object_type_id: str,
        name: str,
        properties: dict[str, Any] | None = None,
        required: list[str] | None = None,
    ) -> dict[str, Any]:
        """Declare an ObjectType on a draft ApplicationRevision."""
        return runtime.schema.create_object_type(
            application_revision_id,
            object_type_id,
            name=name,
            properties=properties,
            required=required,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def create_relation_type(
        application_revision_id: str,
        relation_type_id: str,
        name: str,
        from_object_type_id: str,
        to_object_type_id: str,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Declare a RelationType on a draft ApplicationRevision."""
        return runtime.schema.create_relation_type(
            application_revision_id,
            relation_type_id,
            name=name,
            from_object_type_id=from_object_type_id,
            to_object_type_id=to_object_type_id,
            properties=properties,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def create_action(
        application_revision_id: str,
        action_id: str,
        kind: str,
        entrypoint: str,
        source_paths: list[str],
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        dependency_ids: list[str] | None = None,
        secret_ref_ids: list[str] | None = None,
        migration_metadata: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
        concurrency_policy: Literal["default", "reject_if_running"] | None = None,
    ) -> dict[str, Any]:
        """Create an ActionRevision."""
        kind = _optional_enum(
            "kind", kind, ("normal", "migration", "test_helper")
        )
        return runtime.definitions.create_action(
            application_revision_id,
            action_id,
            kind=kind,
            entrypoint=entrypoint,
            source_paths=source_paths,
            input_schema=input_schema,
            output_schema=output_schema,
            dependency_ids=dependency_ids,
            secret_ref_ids=secret_ref_ids,
            migration_metadata=migration_metadata,
            timeout_seconds=30 if timeout_seconds is None else timeout_seconds,
            concurrency_policy=_optional_enum(
                "concurrency_policy",
                concurrency_policy,
                ("default", "reject_if_running"),
                "default",
            ),
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def create_trigger(
        application_revision_id: str,
        trigger_id: str,
        trigger_type: str,
        action_id: str,
        config: dict[str, Any] | None = None,
        input_template: dict[str, Any] | None = None,
        overlap_policy: str | None = None,
        misfire_policy: str = "run_once",
        max_attempts: int = 3,
        retry_policy: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create a TriggerRevision for schedule, event, or external dispatch."""
        return runtime.definitions.create_trigger(
            application_revision_id,
            trigger_id,
            trigger_type=trigger_type,
            action_id=action_id,
            config=config,
            input_template=input_template,
            overlap_policy=overlap_policy,
            misfire_policy=misfire_policy,
            max_attempts=max_attempts,
            retry_policy=retry_policy,
            enabled=enabled,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def run_draft_action(
        application_revision_id: str,
        action_id: str,
        input_value: dict[str, Any],
        data_space_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a draft action from the ApplicationRevision tree.

        Use before activation. For production data, use run_active_action on the
        active Application revision.
        """
        return runtime.invocations.run_draft_action(
            application_revision_id,
            action_id,
            input_value,
            data_space_id=data_space_id,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def run_active_action(
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the active ActionRevision for an Application.

        Do not use this for an unactivated draft; use run_draft_action instead.
        """
        return runtime.invocations.run_active_action(
            application_id, action_id, input_value
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def submit_draft_action(
        application_revision_id: str,
        action_id: str,
        input_value: dict[str, Any],
        data_space_id: str | None = None,
    ) -> dict[str, Any]:
        """Queue a draft action without waiting.

        Use before activation. For production data, use submit_active_action.
        """
        return runtime.invocations.submit_draft_action(
            application_revision_id,
            action_id,
            input_value,
            data_space_id=data_space_id,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def submit_active_action(
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ) -> dict[str, Any]:
        """Queue the active ActionRevision without waiting.

        Do not use this for an unactivated draft; use submit_draft_action instead.
        """
        return runtime.invocations.submit_active_action(
            application_id, action_id, input_value
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def cancel_invocation(invocation_id: str) -> dict[str, Any]:
        """Cancel a queued or running invocation."""
        return runtime.invocations.cancel_invocation(invocation_id).model_dump(
            mode="json"
        )

    @mcp.tool(**_WRITE)
    def run_application_tests(application_revision_id: str) -> list[dict[str, Any]]:
        """Run all TestCases for an application revision."""
        return [
            invocation.model_dump(mode="json")
            for invocation in runtime.invocations.run_application_tests(
                application_revision_id
            )
        ]

    @mcp.tool(**_WRITE)
    def create_callback_route(
        application_id: str,
        target_action_revision_id: str,
        state: str,
        expires_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a generic local callback route targeting an ActionRevision."""
        from datetime import datetime

        parsed_expires_at = datetime.fromisoformat(expires_at) if expires_at else None
        return runtime.invocations.create_callback_route(
            application_id,
            target_action_revision_id,
            state=state,
            expires_at=parsed_expires_at,
            metadata=metadata,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def dispatch_callback_route(
        route_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a callback payload to its target ActionRevision."""
        return runtime.invocations.dispatch_callback_route(
            route_id, payload
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def run_trigger(
        trigger_id: str,
        input_value: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Run an active Trigger immediately and persist a JobRecord."""
        return runtime.jobs.run_trigger(
            trigger_id, input_value, idempotency_key
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def dispatch_event(
        application_id: str, event_type: str, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Dispatch an application event to active event TriggerRevisions."""
        return [
            job.model_dump(mode="json")
            for job in runtime.jobs.dispatch_event(
                application_id, event_type, payload
            )
        ]

    @mcp.tool(**_READ)
    def inspect_scheduler() -> dict[str, Any]:
        """Inspect active triggers, mounted schedule jobs, and persisted JobRecords."""
        return runtime.jobs.inspect()

    @mcp.tool(**_READ)
    def inspect_callback_routes(
        application_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inspect callback route metadata. Secret values are never returned."""
        return [
            route.model_dump(mode="json")
            for route in runtime.invocations.inspect_callback_routes(application_id)
        ]

    @mcp.tool(**_WRITE)
    def create_test_case(
        application_revision_id: str,
        action_id: str,
        input_value: dict[str, Any],
        expected_output: Any,
    ) -> dict[str, Any]:
        """Create a TestCase for a draft application revision."""
        return runtime.definitions.create_test_case(
            application_revision_id,
            action_id,
            input_value,
            expected_output,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def create_blueprint(
        blueprint_id: str, name: str, description: str = ""
    ) -> dict[str, Any]:
        """Create graph metadata for an AppBlueprint."""
        return runtime.blueprints.create(
            blueprint_id, name, description
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def create_blueprint_revision(
        blueprint_id: str,
        instructions: str,
        content: dict[str, Any],
        activate: bool = False,
    ) -> dict[str, Any]:
        """Create a graph-owned BlueprintRevision."""
        return runtime.blueprints.create_revision(
            blueprint_id,
            instructions=instructions,
            content=content,
            activate=activate,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def instantiate_blueprint(
        blueprint_revision_id: str,
        application_id: str,
        name: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """Instantiate a BlueprintRevision into an ordinary draft ApplicationRevision."""
        return runtime.blueprints.instantiate(
            blueprint_revision_id,
            application_id=application_id,
            name=name,
            description=description,
        )

    @mcp.tool(**_READ)
    def inspect_authoring_guide(
        guide_id: str | None = None,
    ) -> dict[str, Any]:
        """Inspect the active immutable graph-owned platform UI authoring guide.

        Do not use this for System MCP catalog usage; call inspect_client_guide
        instead. Use after inspect_experience_design_context when you need the
        full UI guide body.
        """
        return runtime.platform_authoring.inspect_authoring_guide(guide_id)

    @mcp.tool(**_READ)
    def inspect_component_palette() -> dict[str, Any]:
        """Inspect advisory Experience-owned shadcn/ui components."""
        return runtime.platform_authoring.inspect_component_palette()

    @mcp.tool(**_READ)
    def inspect_experience_design_context(
        experience_revision_id: str,
        include_content: bool = True,
        last_seen_hash: str | None = None,
    ) -> dict[str, Any]:
        """Inspect advisory UI authority and workflow for an Experience revision."""
        return runtime.platform_authoring.inspect_experience_design_context(
            experience_revision_id,
            include_content=include_content,
            last_seen_hash=last_seen_hash,
        )

    @mcp.tool(**_READ)
    def inspect_surface_theme() -> dict[str, Any]:
        """Inspect the active immutable graph-owned N4X UI theme revision."""
        return runtime.platform_authoring.inspect_surface_theme()

    @mcp.tool(**_READ)
    def inspect_experience_bridge() -> dict[str, Any]:
        """Inspect the canonical browser and MCP App Surface bridge contract."""
        return runtime.platform_authoring.inspect_experience_bridge()

    @mcp.tool(**_READ)
    def validate_experience_revision(experience_revision_id: str) -> dict[str, Any]:
        """Validate Application access, source, dependencies, and Surfaces."""
        return runtime.experience_activation.validate(
            experience_revision_id
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    async def activate_experience_revision(
        experience_revision_id: str, ctx: Context
    ) -> dict[str, Any]:
        """Activate an Experience and notify this MCP session of catalog changes."""
        activated = runtime.experience_activation.activate(experience_revision_id)
        if ctx.request_context is not None:
            await ctx.send_notification(ResourceListChangedNotification())
            await ctx.send_notification(ToolListChangedNotification())
        return activated.model_dump(mode="json")

    @mcp.tool(**_READ)
    def validate_application_revision(application_revision_id: str) -> dict[str, Any]:
        """Validate an application revision."""
        return runtime.activation.validate(application_revision_id).model_dump(
            mode="json"
        )

    @mcp.tool(**_WRITE)
    def activate_application_revision(application_revision_id: str) -> dict[str, Any]:
        """Activate an application revision."""
        return runtime.activation.activate(application_revision_id).model_dump(
            mode="json"
        )

    @mcp.tool(**_DESTROY)
    def rollback_application(
        application_id: str, target_revision_id: str
    ) -> dict[str, Any]:
        """Rollback an application to a previously active revision."""
        return runtime.activation.rollback(
            application_id, target_revision_id
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def create_checkpoint(
        application_id: str,
        level: str = "revision",
        reason: str = "manual checkpoint",
    ) -> dict[str, Any]:
        """Create an immutable revision or application-data checkpoint."""
        return runtime.checkpoints.create(
            application_id, level=level, reason=reason
        ).model_dump(mode="json")

    @mcp.tool(**_DESTROY)
    def restore_checkpoint(checkpoint_id: str) -> dict[str, Any]:
        """Restore revision pointers and, when captured, application data."""
        return runtime.checkpoints.restore(checkpoint_id).model_dump(mode="json")

    @mcp.tool(**_READ)
    def inspect_checkpoints(
        application_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inspect graph-owned checkpoints."""
        return [
            item.model_dump(mode="json")
            for item in runtime.uow.records.checkpoints.values()
            if application_id is None or item.application_id == application_id
        ]

    @mcp.tool(**_READ)
    def preview_package(
        root_kind: Literal["application", "experience"],
        root_id: str,
        include_data: bool = False,
    ) -> dict[str, Any]:
        """Preview the active Package closure without writing an archive."""
        return runtime.packages.preview(root_kind, root_id, include_data=include_data)

    @mcp.tool(**_WRITE)
    def export_package(
        root_kind: Literal["application", "experience"],
        root_id: str,
        archive_name: str,
        include_data: bool = False,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Write a deterministic Package under the configured Package directory."""
        return runtime.packages.export(
            root_kind,
            root_id,
            archive_name,
            include_data=include_data,
            overwrite=overwrite,
        )

    @mcp.tool(**_DESTROY)
    def delete_working_set(
        root_kind: Literal["application", "experience"],
        root_id: str,
        archive_name: str,
        confirmation_root_id: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Export a Package with data, then delete that working set from the graph."""
        return runtime.packages.delete_working_set(
            root_kind,
            root_id,
            archive_name,
            confirmation_root_id=confirmation_root_id,
            overwrite=overwrite,
        )

    @mcp.tool(**_READ)
    def list_packages() -> dict[str, Any]:
        """List staged Package archives. Does not import.

        On a remote instance, confirm the local path with the operator, then run
        `n4x package stage --origin <instance> --file <path>`. Do not read or
        inline the archive. Then inspect_package and import_package.
        Same-instance export_package already writes into packages/.
        """
        origin = public_origin.rstrip("/")
        archives = []
        for item in runtime.list_packages():
            archives.append(
                {
                    **item,
                    "url": f"{origin}/packages/{item['archive_name']}",
                }
            )
        return {"upload_url": f"{origin}/packages", "archives": archives}

    @mcp.tool(**_READ)
    def inspect_package(archive_name: str) -> dict[str, Any]:
        """Validate a Package archive and report compatibility and conflicts."""
        return runtime.packages.inspect(archive_name)

    @mcp.tool(**_WRITE)
    def import_package(
        archive_name: str,
        dry_run: bool = False,
        restore_data: bool | None = None,
        allow_incompatible: bool = False,
    ) -> dict[str, Any]:
        """Install or resume one Package. Compatible imports activate; outdated imports stay disabled when allow_incompatible is set."""
        return runtime.packages.import_package(
            archive_name,
            dry_run=dry_run,
            restore_data=restore_data,
            allow_incompatible=allow_incompatible,
        )

    @mcp.tool(**_READ)
    def inspect_package_import(attempt_id: str) -> dict[str, Any]:
        """Inspect durable Package import progress and generated-id mappings."""
        return runtime.packages.inspect_import(attempt_id)

    @mcp.tool(**_WRITE)
    def resume_application_triggers(application_id: str) -> dict[str, Any]:
        """Explicitly enable Triggers after imported bindings are configured."""
        return runtime.applications.set_status(
            application_id,
            "active",
            expected_status="triggers_paused",
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def build_experience_surface(
        experience_revision_id: str, surface_id: str
    ) -> dict[str, Any]:
        """Build a graph-owned Experience Surface into a runtime artifact."""
        result = runtime.build_experience_surface(
            experience_revision_id, surface_id
        )
        return with_development_preview(
            {
                "environment": result.environment.model_dump(mode="json"),
                "build_invocation": result.build_invocation.model_dump(mode="json"),
                "artifact": result.artifact.model_dump(mode="json"),
            },
            runtime,
            experience_revision_id,
            public_origin,
        )

    @mcp.tool(**_DESTROY)
    def reset_dev_graph(confirmation_database: str) -> dict[str, Any]:
        """Wipe a configured dedicated development database after exact confirmation.

        Destructive. Call only with explicit user intent. confirmation_database
        must exactly match the attached Neo4j database name.
        """
        if reset_database is None:
            raise ValueError(
                "reset refused: no configured Neo4j database is attached to "
                "this MCP server"
            )
        if confirmation_database != reset_database:
            raise ValueError(
                "reset refused: confirmation must exactly match configured "
                f"database {reset_database!r}; this operation deletes every node"
            )
        runtime.reset_dev_graph()
        return {"reset": True, "database": reset_database}

    @mcp.tool(**_READ)
    def inspect_invocations(
        action_revision_id: str | None = None,
        invocation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inspect Action invocation records."""
        invocations = list(runtime.uow.records.invocations.values())
        if invocation_id is not None:
            invocations = [
                invocation
                for invocation in invocations
                if invocation.id == invocation_id
            ]
        if action_revision_id is not None:
            invocations = [
                invocation
                for invocation in invocations
                if invocation.action_revision_id == action_revision_id
            ]
        return [invocation.model_dump(mode="json") for invocation in invocations]

    @mcp.tool(**_READ)
    def inspect_job_attempts(job_id: str | None = None) -> list[dict[str, Any]]:
        """Inspect durable job attempt, lease, and heartbeat records."""
        return [
            item.model_dump(mode="json")
            for item in runtime.uow.records.job_attempts.values()
            if job_id is None or item.job_id == job_id
        ]

    @mcp.tool(**_WRITE)
    def process_due_job_work() -> list[dict[str, Any]]:
        """Claim and run due job retries, recovering expired leases first."""
        return [
            job.model_dump(mode="json")
            for job in runtime.scheduler.process_due_work()
        ]

    @mcp.tool(**_READ)
    def inspect_objects(
        application_id: str,
        object_type_id: str | None = None,
        offset: int = 0,
        limit: int = INSPECT_DEFAULT_LIMIT,
        include_values: bool = False,
        object_id: str | None = None,
    ) -> dict[str, Any]:
        """Inspect persisted graph ApplicationObject records. Authoring bound."""
        return page_inspect(
            runtime.objects.list(application_id, object_type_id),
            offset=offset,
            limit=limit,
            include_values=include_values,
            dump=lambda item: item.model_dump(mode="json"),
            summary=model_summary,
            item_id=object_id,
            id_of=lambda item: item.id,
        )

    @mcp.tool(**_READ)
    def inspect_relations(
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
        offset: int = 0,
        limit: int = INSPECT_DEFAULT_LIMIT,
        include_values: bool = False,
        relation_id: str | None = None,
    ) -> dict[str, Any]:
        """Inspect app-owned runtime relationships. Authoring bound."""
        return page_inspect(
            runtime.relations.list(
                application_id,
                relation_type_id=relation_type_id,
                from_object_id=from_object_id,
                to_object_id=to_object_id,
            ),
            offset=offset,
            limit=limit,
            include_values=include_values,
            dump=lambda item: item.model_dump(mode="json"),
            summary=model_summary,
            item_id=relation_id,
            id_of=lambda item: item.id,
        )

    @mcp.tool(**_READ)
    def validate_graph_shape() -> dict[str, Any]:
        """Validate graph-native N4X shape and report integrity issues."""
        return runtime.validate_graph_shape()

    @mcp.tool(**_DESTROY)
    def repair_graph_edges() -> dict[str, Any]:
        """Admin-only repair for damaged explicit graph edges.

        Destructive. Call only with explicit user intent after
        validate_graph_shape reports repairable edge damage.
        """
        return runtime.repair_graph_edges()

    @mcp.tool(**_WRITE)
    def create_secret_reference(
        application_id: str,
        uri: str,
        name: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """Create graph metadata for a secret value stored outside Neo4j."""
        return runtime.secrets.create_reference(
            application_id,
            uri,
            name=name,
            description=description,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def set_secret_value(uri: str, value: str) -> dict[str, Any]:
        """Store a secret value in the configured secret backend."""
        reference = runtime.secrets.set_secret(uri, value)
        return {
            "secret_reference_id": reference.id,
            "uri": reference.uri,
            "stored": True,
        }

    @mcp.tool(**_WRITE)
    def create_credential_record(
        application_id: str,
        provider: str,
        account_name: str,
        secret_reference_ids: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create graph metadata for application-owned credentials."""
        return runtime.secrets.create_credential_record(
            application_id,
            provider,
            account_name,
            secret_reference_ids=secret_reference_ids,
            metadata=metadata,
        ).model_dump(mode="json")

    @mcp.tool(**_READ)
    def inspect_secret_references(
        application_id: str,
    ) -> list[dict[str, Any]]:
        """Inspect secret metadata for one Application. Values are never returned."""
        return [
            runtime.secrets.secret_status(reference.application_id, reference.id)
            | reference.model_dump(mode="json")
            for reference in runtime.uow.records.secret_references.values()
            if reference.application_id == application_id
        ]

    @mcp.tool(**_READ)
    def inspect_credential_records(
        application_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inspect credential metadata, optionally filtered by application."""
        return [
            record.model_dump(mode="json")
            for record in runtime.uow.records.credential_records.values()
            if application_id is None or record.application_id == application_id
        ]

    @mcp.tool(**_WRITE)
    def create_experience(
        experience_id: str, name: str, description: str = ""
    ) -> dict[str, Any]:
        """Create a generic frontend Experience identity."""
        return runtime.experiences.create(
            experience_id, name, description
        ).model_dump(mode="json")

    @mcp.tool(**_DESTROY)
    def retire_experience(
        experience_id: str,
        expected_active_revision_id: str | None = None,
    ) -> dict[str, Any]:
        """Disable an Experience without deleting revisions, source, or builds."""
        return runtime.experiences.retire(
            experience_id,
            expected_active_revision_id=expected_active_revision_id,
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def create_experience_revision(
        experience_id: str,
        ui_profile: Literal["n4x-default", "custom", "none"] | None = None,
        parent_revision_id: str | None = None,
        application_access: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a frontend draft, then inspect its advisory design context."""
        prior = next(
            (
                item
                for item in runtime.uow.experiences.list_revisions(experience_id)
                if item.status == "draft"
            ),
            None,
        )
        revision = runtime.experiences.create_revision(
            experience_id,
            ui_profile=ui_profile,
            parent_revision_id=parent_revision_id,
            application_access=application_access,
        )
        result = runtime.source.bindings.payload(revision.id)
        result["reused"] = prior is not None and prior.id == revision.id
        result["authoring_next_step"] = {
            "tool": "inspect_experience_design_context",
            "arguments": {
                "experience_revision_id": revision.id,
                "include_content": True,
            },
            "reason": (
                "Load the active graph-owned UI profile before writing or editing "
                "Experience Surface source."
            ),
        }
        return result

    @mcp.tool(**_DESTROY)
    def discard_experience_revision(experience_revision_id: str) -> dict[str, Any]:
        """Discard a draft ExperienceRevision and its working tree."""
        runtime.experiences.discard_revision(experience_revision_id)
        return {
            "discarded": True,
            "experience_revision_id": experience_revision_id,
        }

    @mcp.tool(**_WRITE)
    def set_experience_application_access(
        experience_revision_id: str,
        application_access: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Replace a draft ExperienceRevision's declared Application allowlists."""
        return runtime.experiences.set_application_access(
            experience_revision_id, application_access
        ).model_dump(mode="json")

    @mcp.tool(**_READ)
    def inspect_experience_revision(
        experience_revision_id: str,
    ) -> dict[str, Any]:
        """Inspect an Experience draft, source, dependencies, Surfaces, and builds."""
        return with_development_preview(
            runtime.inspect_experience_revision(experience_revision_id),
            runtime,
            experience_revision_id,
            public_origin,
        )

    @mcp.tool(**_WRITE)
    def create_experience_surface(
        experience_revision_id: str,
        surface_id: str,
        surface_type: Literal["browser", "mcp_app"],
        entrypoint: str,
        source_paths: list[str],
        surface_type_version: int = 1,
        title: str = "",
        description: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Declare a versioned Surface on a draft ExperienceRevision."""
        return runtime.experiences.surfaces.create(
            experience_revision_id,
            surface_id,
            surface_type=surface_type,
            surface_type_version=surface_type_version,
            entrypoint=entrypoint,
            source_paths=source_paths,
            title=title,
            description=description,
            config=config,
            created_by="mcp",
        ).model_dump(mode="json")

    @mcp.tool(**_READ)
    def inspect_experience_surface(
        experience_revision_id: str, surface_id: str
    ) -> dict[str, Any]:
        """Inspect one Experience Surface declaration."""
        return runtime.experiences.surfaces.inspect(
            experience_revision_id, surface_id
        ).model_dump(mode="json")

    @mcp.tool(**_READ)
    def list_experience_surfaces(
        experience_revision_id: str,
    ) -> list[dict[str, Any]]:
        """List Surface declarations on one ExperienceRevision."""
        return [
            surface.model_dump(mode="json")
            for surface in runtime.experiences.surfaces.list(experience_revision_id)
        ]

    @mcp.tool(**_WRITE)
    def update_experience_surface(
        experience_revision_id: str,
        surface_id: str,
        surface_type: Literal["browser", "mcp_app"] | None = None,
        surface_type_version: int | None = None,
        entrypoint: str | None = None,
        source_paths: list[str] | None = None,
        title: str | None = None,
        description: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update a Surface declaration on a draft ExperienceRevision."""
        return runtime.experiences.surfaces.update(
            experience_revision_id,
            surface_id,
            surface_type=surface_type,
            surface_type_version=surface_type_version,
            entrypoint=entrypoint,
            source_paths=source_paths,
            title=title,
            description=description,
            config=config,
        ).model_dump(mode="json")

    @mcp.tool(**_DESTROY)
    def delete_experience_surface(
        experience_revision_id: str, surface_id: str
    ) -> dict[str, Any]:
        """Delete a Surface declaration from a draft ExperienceRevision."""
        runtime.experiences.surfaces.delete(experience_revision_id, surface_id)
        return {
            "deleted": True,
            "experience_revision_id": experience_revision_id,
            "surface_id": surface_id,
        }

    @mcp.tool(**_WRITE)
    def write_source_file(
        revision_id: str,
        path: str,
        content: str,
        role: str,
        language: str,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        """Write a source file on a draft revision. Pass expected_hash from the last read."""
        return SourceFileSummary.from_source_file(
            runtime.source.write_source_file(
                revision_id,
                path,
                content,
                role=role,  # type: ignore[arg-type]
                language=language,
                expected_hash=expected_hash,
                tool="mcp",
            )
        ).model_dump(mode="json")

    @mcp.tool(**_WRITE)
    def apply_source_patch(
        revision_id: str,
        path: str,
        patch: str,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        """Apply a few surgical hunks by unique context. For a rewrite, use write_source_file. Never writes a partial file."""
        from n4x.kernel.errors import SourceConflictError

        try:
            updated = runtime.source.apply_source_patch(
                revision_id,
                path,
                patch,
                expected_hash=expected_hash,
                tool="mcp",
            )
        except SourceConflictError as exc:
            return exc.as_conflict()
        result = SourceFileSummary.from_source_file(updated).model_dump(mode="json")
        result["status"] = "applied"
        return result

    @mcp.tool(**_WRITE)
    def rename_source_file(
        revision_id: str,
        path: str,
        new_path: str,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        """Rename a source file on a draft revision."""
        return SourceFileSummary.from_source_file(
            runtime.source.rename_source_file(
                revision_id,
                path,
                new_path,
                expected_hash=expected_hash,
                tool="mcp",
            )
        ).model_dump(mode="json")

    @mcp.tool(**_DESTROY)
    def delete_source_file(
        revision_id: str, path: str, expected_hash: str | None = None
    ) -> dict[str, Any]:
        """Delete a source file from a draft revision."""
        runtime.source.delete_source_file(
            revision_id, path, expected_hash=expected_hash, tool="mcp"
        )
        return {
            "deleted": True,
            "revision_id": revision_id,
            "path": path,
        }

    @mcp.tool(**_READ)
    def read_source_file(
        revision_id: str,
        path: str,
        offset: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Read a source file on a revision, optionally a 1-based line range."""
        return runtime.source.read_source_file_range(
            runtime.source.bindings.tree_id(revision_id),
            path,
            offset=offset,
            limit=limit,
        )

    @mcp.tool(**_READ)
    def search_source_tree(
        revision_id: str,
        pattern: str,
        glob: str | None = None,
        limit: int = 50,
        context: int = 2,
    ) -> list[dict[str, Any]]:
        """Search source file contents on a revision. Cap results; pattern is a regex."""
        return runtime.source.search_source_tree(
            runtime.source.bindings.tree_id(revision_id),
            pattern,
            glob=glob,
            limit=limit,
            context=context,
        )

    @mcp.tool(**_READ)
    def list_source_tree(revision_id: str) -> list[dict[str, Any]]:
        """List files on a revision's current SourceTree."""
        return [
            SourceFileSummary.from_source_file(file).model_dump(mode="json")
            for file in runtime.source.list_source_tree(
                runtime.source.bindings.tree_id(revision_id)
            )
        ]

    @mcp.tool(**_WRITE)
    def create_experience_runtime_dependency(
        experience_revision_id: str,
        package: str,
        spec: str,
        ecosystem: str = "javascript",
    ) -> dict[str, Any]:
        """Declare a JavaScript dependency on a draft ExperienceRevision."""
        return runtime.experiences.create_runtime_dependency(
            experience_revision_id, ecosystem, package, spec
        ).model_dump(mode="json")

    return mcp
