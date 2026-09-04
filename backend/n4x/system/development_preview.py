"""Instance-prefixed DevelopmentDeployment browser URLs."""

from __future__ import annotations

from typing import Any

from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import DevelopmentDeployment, now_utc
from n4x.system.origins import instance_link


def browser_path_suffix(mount_path: str) -> str:
    return "" if mount_path in {"", "/"} else mount_path


def development_browser_path(
    deployment_id: str,
    experience_id: str,
    mount_path: str = "/",
) -> str:
    return (
        f"/development/{deployment_id}/experience/{experience_id}"
        f"{browser_path_suffix(mount_path)}"
    )


def development_preview_fields(
    runtime: Any,
    deployment: DevelopmentDeployment,
    public_origin: str,
) -> dict[str, Any]:
    revision = runtime.uow.records.experience_revisions[
        deployment.experience_revision_id
    ]
    experience_id = revision.experience_id
    surfaces: list[dict[str, str]] = []
    default: dict[str, str] | None = None
    for surface in runtime.experiences.surfaces.list(revision.id):
        if surface.surface_type != "browser":
            continue
        mount = "/"
        if isinstance(surface.config, dict):
            mount = str(surface.config.get("mount_path") or "/")
        link = instance_link(
            public_origin,
            development_browser_path(deployment.id, experience_id, mount),
        )
        entry = {
            "surface_id": surface.surface_id,
            "preview_url": link["url"],
            "origin_kind": link["origin_kind"],
            "host_bind_url": link["host_bind_url"],
        }
        surfaces.append(entry)
        if default is None or mount in {"", "/"}:
            default = entry
    if default is None:
        default = instance_link(
            public_origin,
            development_browser_path(deployment.id, experience_id),
        )
    origin_kind = default["origin_kind"]
    return {
        "preview_url": default.get("preview_url") or default["url"],
        "origin_kind": origin_kind,
        "preview_auth": "none" if origin_kind == "host_bind" else "deployment",
        "host_bind_url": default["host_bind_url"],
        "browser_surface_urls": surfaces,
    }


def matching_development_deployment(
    runtime: Any, experience_revision_id: str
) -> DevelopmentDeployment | None:
    now = now_utc()
    candidates = [
        item
        for item in runtime.uow.records.development_deployments.values()
        if item.experience_revision_id == experience_revision_id
        and item.status == "active"
        and item.expires_at > now
    ]
    candidates.sort(key=lambda item: item.created_at, reverse=True)
    for item in candidates:
        try:
            return runtime.development_deployments.require_available(item.id)
        except ValidationFailure:
            continue
    return None


def with_development_preview(
    payload: dict[str, Any],
    runtime: Any,
    experience_revision_id: str,
    public_origin: str,
) -> dict[str, Any]:
    deployment = matching_development_deployment(runtime, experience_revision_id)
    if deployment is None:
        return payload
    payload.update(development_preview_fields(runtime, deployment, public_origin))
    payload["development_deployment_id"] = deployment.id
    return payload


def development_deployment_payload(
    runtime: Any,
    deployment: DevelopmentDeployment,
    public_origin: str,
) -> dict[str, Any]:
    payload = deployment.model_dump(mode="json")
    try:
        runtime.development_deployments.require_available(deployment.id)
    except ValidationFailure:
        return payload
    payload.update(development_preview_fields(runtime, deployment, public_origin))
    return payload
