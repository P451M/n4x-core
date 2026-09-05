"""Content-addressed ids for interned source and declaration nodes."""

from __future__ import annotations

from typing import Any

from n4x.kernel.hash import sha256_json, sha256_text


def intern_id(payload: Any) -> str:
    return sha256_json(payload)


def empty_tree_hash() -> str:
    return listing_hash([])


def listing_hash(files: list[dict[str, str]]) -> str:
    return sha256_json(
        [
            {
                "path": item["path"],
                "hash": item["hash"],
                "role": item["role"],
                "language": item["language"],
            }
            for item in sorted(files, key=lambda item: item["path"])
        ]
    )


def source_content_id(content: str) -> str:
    return sha256_text(content)


def runtime_dependency_id(*, ecosystem: str, package: str, spec: str) -> str:
    return intern_id(
        {"ecosystem": ecosystem, "package": package, "spec": spec}
    )


def action_declaration(
    *,
    action_id: str,
    kind: str,
    entrypoint: str,
    source_paths: list[str],
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    runtime_dependency_ids: list[str],
    secret_refs: list[str],
    callback_refs: list[str],
    declared_capabilities: list[str],
    timeout_seconds: int,
    concurrency_policy: str,
    retry_policy: dict[str, Any],
    idempotency_key_policy: str | None,
    migration_metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "kind": kind,
        "entrypoint": entrypoint,
        "source_paths": list(source_paths),
        "input_schema": input_schema,
        "output_schema": output_schema,
        "runtime_dependency_ids": list(runtime_dependency_ids),
        "secret_refs": list(secret_refs),
        "callback_refs": list(callback_refs),
        "declared_capabilities": list(declared_capabilities),
        "timeout_seconds": timeout_seconds,
        "concurrency_policy": concurrency_policy,
        "retry_policy": retry_policy,
        "idempotency_key_policy": idempotency_key_policy,
        "migration_metadata": migration_metadata,
    }


def action_revision_id(payload: dict[str, Any]) -> str:
    return intern_id(payload)


def object_type_revision_id(payload: dict[str, Any]) -> str:
    return intern_id(payload)


def relation_type_revision_id(payload: dict[str, Any]) -> str:
    return intern_id(payload)


def trigger_revision_id(payload: dict[str, Any]) -> str:
    return intern_id(payload)


def test_case_id(payload: dict[str, Any]) -> str:
    return intern_id(payload)


def experience_surface_id(payload: dict[str, Any]) -> str:
    return intern_id(payload)
