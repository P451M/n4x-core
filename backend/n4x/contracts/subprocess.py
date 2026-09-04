SUBPROCESS_PROTOCOL_VERSION = "n4x.action.subprocess.v3"

SUBPROCESS_PROTOCOL_SCHEMA = {
    "version": SUBPROCESS_PROTOCOL_VERSION,
    "env": [
        "N4X_APPLICATION_DATA_ROOT",
        "N4X_ACTION_CONTEXT_VERSION",
        "N4X_SUBPROCESS_PROTOCOL_VERSION",
        "N4X_CYPHER_SOCKET",
        "N4X_CYPHER_TOKEN",
        "N4X_APPLICATION_ID",
        "N4X_DATA_SPACE_ID",
        "N4X_ACTION_REVISION_ID",
        "N4X_CHECKPOINT_ID",
        "N4X_ACTOR",
    ],
    "rpc": [
        "module",
        "function",
        "input_path",
        "secrets_path",
        "result_path",
        "check_only",
    ],
    "result": {
        "ok": "bool",
        "output": "any",
        "error": "string",
        "traceback": "string",
        "pid": "int",
    },
    "cypher_ipc": {
        "framing": "u32be_length_prefixed_json",
        "request": [
            "id",
            "token",
            "op",
            "mode",
            "query",
            "params",
            "contract_version",
        ],
        "ops": ["query", "begin", "commit", "rollback"],
        "response": ["id", "ok", "rows", "error"],
    },
}
