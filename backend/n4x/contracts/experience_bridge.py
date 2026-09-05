EXPERIENCE_BRIDGE_VERSION = "n4x.experience.bridge.v1"

_BROWSER_CAPABILITIES = {
    "objects": "/api/experiences/{experience_id}/apps/{application_id}/objects",
    "relations": "/api/experiences/{experience_id}/apps/{application_id}/relations",
    "invoke": (
        "/api/experiences/{experience_id}/apps/{application_id}"
        "/actions/{action_id}/invoke"
    ),
    "secrets": "/api/experiences/{experience_id}/apps/{application_id}/secrets",
    "secret": (
        "/api/experiences/{experience_id}/apps/{application_id}"
        "/secrets/{secret_reference_id}"
    ),
    "file": (
        "/api/experiences/{experience_id}/apps/{application_id}"
        "/files/{token}"
    ),
}

EXPERIENCE_BRIDGE_SCHEMA = {
    "version": EXPERIENCE_BRIDGE_VERSION,
    "hosts": ["browser", "mcp_app"],
    "ownership": {
        "surface": "ExperienceRevision",
        "build_artifact": "ExperienceRevision",
    },
    "browser": {
        "surface": "/experience/{experience_id}{mount_path}",
        **_BROWSER_CAPABILITIES,
    },
    "development_browser": {
        "surface": (
            "/development/{deployment_id}/experience/"
            "{experience_id}{mount_path}"
        ),
        "runtime_context_global": "__N4X_EXECUTION_CONTEXT__",
        "api_base": "/api/development/{deployment_id}",
        "objects": (
            "/api/development/{deployment_id}/apps/"
            "{application_id}/objects"
        ),
        "relations": (
            "/api/development/{deployment_id}/apps/"
            "{application_id}/relations"
        ),
        "invoke": (
            "/api/development/{deployment_id}/apps/{application_id}"
            "/actions/{action_id}/invoke"
        ),
        "submit": (
            "/api/development/{deployment_id}/apps/{application_id}"
            "/actions/{action_id}/submit"
        ),
        "secrets": (
            "/api/development/{deployment_id}/apps/{application_id}/secrets"
        ),
        "secret": (
            "/api/development/{deployment_id}/apps/{application_id}"
            "/secrets/{secret_reference_id}"
        ),
    },
    "mcp_app": {
        "transport": "window.parent.postMessage",
        "tool_call_method": "tools/call",
        "tool_call_params": ["name", "arguments"],
        "compatibility_fallback": "window.openai.callTool",
    },
    "authoring": {
        "guide": "/bridge/authoring-guide",
        "theme": "/bridge/theme",
        "palette": "/bridge/component-palette",
        "contract": "/bridge/contract",
    },
    "access": {
        "source": "active ExperienceRevision.application_access",
        "application_serving_statuses": ["active", "triggers_paused"],
        "missing_allowlist": "unrestricted_within_declared_application",
        "empty_allowlist": "denied",
        "secret_allowlist": "explicit_only_missing_or_empty_denies",
        "definition_must_be_active": True,
        "composed_operations": False,
    },
    "artifacts": {
        "content_addressed": True,
        "immutable": True,
        "built_on": ["explicit_build", "activation"],
        "missing_artifact_status": 503,
    },
    "file_delivery": {
        "authorization": "successful_allowlisted_action",
        "path_visibility": "kernel_private",
        "token": "short_lived_signed",
        "supports": ["GET", "HEAD", "range"],
    },
}
