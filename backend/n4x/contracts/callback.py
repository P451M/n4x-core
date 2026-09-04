CALLBACK_CONTRACT_VERSION = "n4x.callback.v1"

CALLBACK_CONTRACT_SCHEMA = {
    "version": CALLBACK_CONTRACT_VERSION,
    "http": {
        "path": "/callback/{route_id}",
        "methods": ["GET", "POST"],
        "payload": {
            "method": "string",
            "query": "object",
            "state": "string|null",
            "body": "json|string|null",
            "headers": "object",
        },
    },
    "route": {
        "target": "action_revision",
        "state_required": True,
        "single_use": True,
        "statuses": ["pending", "used", "expired", "revoked"],
    },
}
