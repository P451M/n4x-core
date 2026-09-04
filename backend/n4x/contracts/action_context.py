ACTION_CONTEXT_VERSION = "n4x.action.context.v2"

ACTION_CONTEXT_SCHEMA = {
    "version": ACTION_CONTEXT_VERSION,
    "ctx": {
        "application_id": "string",
        "data_space_id": "string",
        "secrets": ["get"],
        "graph": [
            "run_app_cypher_read",
            "run_app_cypher_write",
            "run_cypher",
            "transaction",
        ],
    },
    "graph_access": {
        "transport": "authenticated_local_ipc",
        "modes": ["read", "write"],
        "transaction": "begin_query_commit_or_rollback",
        "raw_driver": False,
    },
}
