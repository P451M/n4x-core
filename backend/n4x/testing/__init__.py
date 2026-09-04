"""Test support that is never selected by production composition."""

from typing import TYPE_CHECKING

from n4x.testing.graph_store import InMemoryGraphStore

if TYPE_CHECKING:
    from n4x.runtime.actions import RuntimePaths
    from n4x.secrets.backends import SecretBackend
    from n4x.system.runtime import SystemRuntime


def create_test_runtime(
    secret_backend: "SecretBackend | None" = None,
    runtime_paths: "RuntimePaths | None" = None,
) -> "SystemRuntime":
    from n4x.runtime.actions import RuntimePaths
    from n4x.system.runtime import SystemRuntime

    return SystemRuntime(
        InMemoryGraphStore(),
        secret_backend,
        runtime_paths or RuntimePaths.temporary(),
    )


__all__ = ["InMemoryGraphStore", "create_test_runtime"]
