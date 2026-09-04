class N4XError(Exception):
    """Base N4X exception."""


class SourceConflictError(N4XError):
    """Raised when an expected source hash or patch context does not match."""

    def __init__(
        self,
        message: str,
        *,
        path: str | None = None,
        expected_hash: str | None = None,
        current_hash: str | None = None,
        failing_hunk: str | None = None,
        nearest_lines: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.expected_hash = expected_hash
        self.current_hash = current_hash
        self.failing_hunk = failing_hunk
        self.nearest_lines = nearest_lines or []

    def as_conflict(self) -> dict[str, object]:
        return {
            "status": "conflict",
            "error": str(self),
            "path": self.path,
            "expected_hash": self.expected_hash,
            "current_hash": self.current_hash,
            "failing_hunk": self.failing_hunk,
            "nearest_lines": list(self.nearest_lines),
        }


class ImmutableRevisionError(N4XError):
    """Raised when a caller attempts to modify a locked or immutable revision."""


class ValidationFailure(N4XError):
    """Raised when validation or activation fails."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


KNOWN_ACTION_EXEC_CODES = frozenset(
    {
        "exec_argument_list_too_long",
        "action_cancelled",
        "action_timed_out",
        "cypher_gateway_unavailable",
    }
)


def public_invocation_error(invocation: object) -> str | None:
    """Experience/MCP-visible error: known System codes only."""
    status = getattr(invocation, "status", None)
    if status == "succeeded":
        return None
    metadata = getattr(invocation, "metadata", None) or {}
    code = metadata.get("error_code")
    if code in KNOWN_ACTION_EXEC_CODES:
        return str(code)
    return "action invocation failed"


class ActionExecutionError(N4XError):
    """Raised when an Action fails during execution."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class SecretBackendError(N4XError):
    """Raised when a secret backend cannot read or write a value."""


class SecretNotFoundError(SecretBackendError):
    """Raised when declared secret metadata has no configured backend value."""


class ConcurrentGraphUpdateError(N4XError):
    """Raised when an optimistic graph pointer replacement loses a race."""


class GraphUnitOfWorkError(N4XError):
    """Raised when a graph unit of work cannot commit."""


class GraphMetamodelVersionError(N4XError):
    """Raised when the durable graph is not the runtime's metamodel version."""


class CypherGatewayError(N4XError):
    """Raised when privileged Cypher access is unavailable or rejected."""


class ExperienceAccessError(N4XError):
    """Safe, classified failure from an Experience-scoped runtime boundary."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class FileDeliveryError(N4XError):
    """Safe, classified failure from generic Application file delivery."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class PlatformReleaseConflictError(N4XError):
    """Raised when a packaged platform release reuses a changed version."""
