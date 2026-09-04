from __future__ import annotations

import asyncio
import errno
import subprocess

import pytest
from fastmcp.exceptions import ToolError

from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import ApplicationObject
from n4x.secrets.backends import InMemorySecretBackend
from n4x.system.mcp import create_system_mcp
from n4x.testing import create_test_runtime
from tests.cypher_source import action_source


def test_action_may_inject_another_applications_existing_secret() -> None:
    backend = InMemorySecretBackend()
    system = create_test_runtime(secret_backend=backend)
    owner = system.create_application("secret-owner", "Secret Owner")
    reader = system.create_application("secret-reader", "Secret Reader")
    reference = system.secrets.create_reference(
        owner.id, "secret://secret-owner/shared", name="Shared"
    )
    system.secrets.set_secret(reference.uri, "shared-secret-value")
    revision = system.create_application_revision(reader.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/read.py",
        (
            "def run(ctx, input):\n"
            "    value = ctx.secrets.get('secret://secret-owner/shared')\n"
            "    return {'length': len(value)}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "secret-reader.read",
        kind="normal",
        entrypoint="actions/read.py:run",
        source_paths=["actions/read.py"],
        secret_ref_ids=[reference.id],
    )

    invocation = system.run_draft_action(action.id, {})
    inspected = [
        item.model_dump(mode="json")
        for item in system.inspect_secret_references()
        if item.application_id == owner.id
    ]
    status = system.secrets.secret_status(owner.id, reference.id)

    assert invocation.status == "succeeded", invocation.error
    assert invocation.output == {"length": len("shared-secret-value")}
    assert all("value" not in item for item in inspected)
    assert "value" not in status
    assert "shared-secret-value" not in repr([inspected, status])


def test_create_action_rejects_unknown_secret_reference() -> None:
    system = create_test_runtime()
    app = system.create_application("secret-unknown", "Secret Unknown")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/read.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )

    with pytest.raises(ValidationFailure, match="unknown secret reference"):
        system.create_action(
            revision.id,
            "secret-unknown.read",
            kind="normal",
            entrypoint="actions/read.py:run",
            source_paths=["actions/read.py"],
            secret_ref_ids=["missing-secret"],
        )


def test_experience_cannot_attach_foreign_secret_to_access_declaration() -> None:
    backend = InMemorySecretBackend()
    system = create_test_runtime(secret_backend=backend)
    mail = system.create_application("mail-secrets", "Mail Secrets")
    calendar = system.create_application("calendar-secrets", "Calendar Secrets")
    foreign = system.secrets.create_reference(
        mail.id, "secret://mail-secrets/password"
    )
    system.create_experience("calendar-ui", "Calendar UI")

    with pytest.raises(ValueError, match="secret reference"):
        system.create_experience_revision(
            "calendar-ui",
            application_access=[
                {
                    "application_id": calendar.id,
                    "secret_reference_ids": [foreign.id],
                }
            ],
        )


def test_create_action_accepts_required_fields_and_null_optionals() -> None:
    system = create_test_runtime()
    server = create_system_mcp(system)
    app = system.create_application("action-nulls", "Action Nulls")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/echo.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )

    result = asyncio.run(
        server.call_tool(
            "create_action",
            {
                "application_revision_id": revision.id,
                "action_id": "action-nulls.echo",
                "kind": "normal",
                "entrypoint": "actions/echo.py:run",
                "source_paths": ["actions/echo.py"],
                "timeout_seconds": None,
                "concurrency_policy": None,
            },
        )
    )
    action = result.structured_content

    assert action["kind"] == "normal"
    assert action["timeout_seconds"] == 30
    assert action["concurrency_policy"] == "default"


def test_create_action_rejects_python_kind_at_field_level() -> None:
    system = create_test_runtime()
    app = system.create_application("action-kind", "Action Kind")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/echo.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )

    with pytest.raises(ValidationFailure) as captured:
        system.create_action(
            revision.id,
            "action-kind.echo",
            kind="python",
            entrypoint="actions/echo.py:run",
            source_paths=["actions/echo.py"],
        )
    assert captured.value.field == "kind"

    server = create_system_mcp(system)
    with pytest.raises(ToolError, match="kind"):
        asyncio.run(
            server.call_tool(
                "create_action",
                {
                    "application_revision_id": revision.id,
                    "action_id": "action-kind.bad",
                    "kind": "python",
                    "entrypoint": "actions/echo.py:run",
                    "source_paths": ["actions/echo.py"],
                },
            )
        )


def test_application_revision_parent_is_latest_draft_then_active() -> None:
    system = create_test_runtime()
    draft_app = system.create_application("parents-draft", "Parents Draft")
    draft_one = system.create_application_revision(draft_app.id)
    draft_two = system.create_application_revision(draft_app.id)
    assert draft_two.parent_revision_id == draft_one.id

    app = system.create_application("parents", "Parents")
    first = system.create_application_revision(app.id)
    system.source.write_source_file(
        first.source_tree_id,
        "actions/v1.py",
        "def run(ctx, input):\n    return {'version': 1}\n",
        role="action",
        language="python",
    )
    system.create_action(
        first.id,
        "parents.run",
        kind="normal",
        entrypoint="actions/v1.py:run",
        source_paths=["actions/v1.py"],
    )
    system.activate_application_revision(first.id)
    second = system.create_application_revision(app.id)
    system.source.write_source_file(
        second.source_tree_id,
        "actions/extra.py",
        "def run(ctx, input):\n    return {'extra': True}\n",
        role="action",
        language="python",
    )
    system.create_action(
        second.id,
        "parents.extra",
        kind="normal",
        entrypoint="actions/extra.py:run",
        source_paths=["actions/extra.py"],
    )
    system.activate_application_revision(second.id)
    forked = system.create_application_revision(
        app.id, parent_revision_id=first.id
    )
    assert second.parent_revision_id == first.id
    assert forked.parent_revision_id == first.id
    assert not any(
        item.action_id == "parents.extra"
        and item.application_revision_id == forked.id
        for item in system.uow.records.action_revisions.values()
    )

    rollback_app = system.create_application("parents-rollback", "Parents Rollback")
    rollback_first = system.create_application_revision(rollback_app.id)
    system.source.write_source_file(
        rollback_first.source_tree_id,
        "actions/v1.py",
        "def run(ctx, input):\n    return {'version': 1}\n",
        role="action",
        language="python",
    )
    system.create_action(
        rollback_first.id,
        "parents-rollback.run",
        kind="normal",
        entrypoint="actions/v1.py:run",
        source_paths=["actions/v1.py"],
    )
    system.activate_application_revision(rollback_first.id)
    rollback_second = system.create_application_revision(rollback_app.id)
    system.activate_application_revision(rollback_second.id)
    system.rollback_application(rollback_app.id, rollback_first.id)
    after_rollback = system.create_application_revision(rollback_app.id)

    assert after_rollback.parent_revision_id == rollback_first.id
    assert after_rollback.parent_revision_id != rollback_second.id
    assert system.inspect_application(rollback_app.id).active_revision_id == (
        rollback_first.id
    )


def test_draft_action_on_deployment_data_space_does_not_read_production() -> None:
    system = create_test_runtime()
    app = system.create_application("draft-space", "Draft Space")
    revision = system.create_application_revision(app.id)
    object_type = system.create_object_type(
        revision.id, "draft-space.Item", name="Item"
    )
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/count.py",
        action_source(
            "def run(ctx, input):\n"
            "    before = len(list_objects(ctx))\n"
            "    created = upsert_object("
            "ctx, 'draft-space.Item', {'scope': ctx.data_space_id}, "
            "object_id='draft-item')\n"
            "    return {'before': before, 'id': created['id'], "
            "'data_space_id': ctx.data_space_id}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "draft-space.count",
        kind="normal",
        entrypoint="actions/count.py:run",
        source_paths=["actions/count.py"],
    )
    with system.uow:
        system.uow.objects.save(
            ApplicationObject(
                id="production-item",
                application_id=app.id,
                object_type_id=object_type.object_type_id,
                object_type_revision_id=object_type.id,
                values={"scope": "production"},
            )
        )
    system.create_experience("draft-space-ui", "Draft Space UI")
    experience_revision = system.create_experience_revision(
        "draft-space-ui",
        application_access=[{"application_id": app.id}],
    )
    deployment = system.create_development_deployment(
        experience_revision.id,
        {app.id: revision.id},
    )
    preview_id = deployment.data_space_ids[app.id]

    invocation = system.run_draft_action(
        action.id, {}, data_space_id=preview_id
    )

    assert invocation.status == "succeeded", invocation.error
    assert invocation.output == {
        "before": 0,
        "id": "draft-item",
        "data_space_id": preview_id,
    }
    assert system.uow.objects.get(
        app.id, "production-item", "production"
    ).values == {"scope": "production"}
    assert system.uow.objects.get(app.id, "draft-item", preview_id).values == {
        "scope": preview_id
    }
    assert system.uow.objects.get(app.id, "production-item", preview_id) is None


def test_e2big_maps_to_known_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    system = create_test_runtime()
    app = system.create_application("e2big", "E2BIG")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/echo.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "e2big.echo",
        kind="normal",
        entrypoint="actions/echo.py:run",
        source_paths=["actions/echo.py"],
    )

    original = subprocess.Popen

    def boom(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, (list, tuple)) and any(
            "action_runner.py" in str(part) for part in command
        ):
            raise OSError(errno.E2BIG, "Argument list too long")
        return original(*args, **kwargs)

    monkeypatch.setattr("n4x.runtime.actions.subprocess.Popen", boom)
    invocation = system.run_draft_action(action.id, {})

    assert invocation.status == "failed"
    assert invocation.metadata["error_code"] == "exec_argument_list_too_long"
