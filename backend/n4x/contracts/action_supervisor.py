ACTION_SUPERVISOR_VERSION = "n4x.action.supervisor.v1"

ACTION_SUPERVISOR_SCHEMA = {
    "version": ACTION_SUPERVISOR_VERSION,
    "invocation_status": [
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    ],
    "terminal_status": ["succeeded", "failed", "cancelled"],
    "admission": {
        "worker_bound": "host_configured_positive_integer",
        "queue_capacity": "host_configured_non_negative_integer",
        "overflow": "explicit_rejection",
    },
    "execution": {
        "process_model": (
            "one_running_invoke_per_child_reuse_same_revision_space_and_python_env"
        ),
        "transaction_wait": "forbidden",
        "late_result_commit": "must_be_fenced_after_cancel_or_timeout",
    },
    "operations": ["submit", "inspect", "await", "cancel"],
}
