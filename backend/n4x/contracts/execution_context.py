EXECUTION_CONTEXT_VERSION = "n4x.execution.context.v1"

EXECUTION_CONTEXT_SCHEMA = {
    "version": EXECUTION_CONTEXT_VERSION,
    "required": [
        "mode",
        "correlation_id",
        "application_revision_id",
        "application_id",
        "data_space_id",
    ],
    "mode": {
        "production": {
            "data_space_kind": "production",
            "deployment_id": None,
        },
        "development": {
            "data_space_kind": "development",
            "deployment_id": "required",
        },
    },
    "propagation": [
        "invocation",
        "action_subprocess",
        "application_data_mount",
        "cypher_audit",
    ],
}
