"""Test support that is never selected by production composition."""

from typing import TYPE_CHECKING

from n4x.testing.graph_store import InMemoryGraphStore

if TYPE_CHECKING:
    from n4x.kernel.models import (
        ApplicationRevision,
        ExperienceRevision,
        SystemRevision,
    )
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


def tree_id(
    runtime: "SystemRuntime",
    revision: "str | ApplicationRevision | ExperienceRevision | SystemRevision",
) -> str:
    revision_id = revision if isinstance(revision, str) else revision.id
    return runtime.source.bindings.tree_id(revision_id)


__all__ = ["InMemoryGraphStore", "create_test_runtime", "tree_id"]
