from __future__ import annotations

import pytest
from n4x.kernel.errors import GraphUnitOfWorkError
from n4x.secrets.backends import InMemorySecretBackend
from n4x.testing import create_test_runtime


def test_secret_metadata_excludes_values_and_actions_can_read_declared_secret() -> None:
    backend = InMemorySecretBackend()
    system = create_test_runtime(secret_backend=backend)
    app = system.create_application("secret-app", "Secret App")
    revision = system.create_application_revision(app.id)
    reference = system.secrets.create_reference(
        app.id, "secret://secret-app/test-password", name="Test Password"
    )
    system.secrets.set_secret(reference.uri, "very-secret-value")
    system.source.write_source_file(
        revision.id,
        "actions/read_secret.py",
        (
            "def run(ctx, input):\n"
            "    value = ctx.secrets.get('secret://secret-app/test-password')\n"
            "    print(value)\n"
            "    return {'length': len(value), 'value': value}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "secret-app.read",
        kind="normal",
        entrypoint="actions/read_secret.py:run",
        source_paths=["actions/read_secret.py"],
        secret_ref_ids=[reference.id],
    )

    invocation = system.run_draft_action(revision.id, action.action_id, {})

    assert invocation.status == "succeeded"
    assert invocation.output == {"length": 17, "value": "[REDACTED]"}
    assert "very-secret-value" not in invocation.stdout
    assert "[REDACTED]" in invocation.stdout
    assert (
        "very-secret-value"
        not in system.uow.records.secret_references[reference.id].model_dump_json()
    )


def test_action_cannot_read_undeclared_secret() -> None:
    backend = InMemorySecretBackend()
    system = create_test_runtime(secret_backend=backend)
    app = system.create_application("secret-app", "Secret App")
    revision = system.create_application_revision(app.id)
    reference = system.secrets.create_reference(
        app.id, "secret://secret-app/test-password"
    )
    system.secrets.set_secret(reference.uri, "very-secret-value")
    system.source.write_source_file(
        revision.id,
        "actions/read_secret.py",
        (
            "def run(ctx, input):\n"
            "    return {'value': ctx.secrets.get('secret://secret-app/test-password')}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "secret-app.read",
        kind="normal",
        entrypoint="actions/read_secret.py:run",
        source_paths=["actions/read_secret.py"],
    )

    invocation = system.run_draft_action(revision.id, action.action_id, {})

    assert invocation.status == "failed"
    assert "KeyError" in invocation.error


def test_activation_import_check_does_not_require_secret_value() -> None:
    backend = InMemorySecretBackend()
    system = create_test_runtime(secret_backend=backend)
    app = system.create_application("secret-later", "Secret Later")
    revision = system.create_application_revision(app.id)
    reference = system.secrets.create_reference(
        app.id, "secret://secret-later/provider-password"
    )
    system.source.write_source_file(
        revision.id,
        "actions/provider.py",
        (
            "def run_local(ctx, input):\n"
            "    return {'configured': True}\n\n"
            "def run_provider(ctx, input):\n"
            "    return {'value': ctx.secrets.get("
            "'secret://secret-later/provider-password')}\n"
        ),
        role="action",
        language="python",
    )
    local_action = system.create_action(
        revision.id,
        "secret-later.local",
        kind="normal",
        entrypoint="actions/provider.py:run_local",
        source_paths=["actions/provider.py"],
        secret_ref_ids=[reference.id],
    )
    provider_action = system.create_action(
        revision.id,
        "secret-later.provider",
        kind="normal",
        entrypoint="actions/provider.py:run_provider",
        source_paths=["actions/provider.py"],
        secret_ref_ids=[reference.id],
    )

    activated = system.activate_application_revision(revision.id)
    local_invocation = system.run_active_action(app.id, local_action.action_id, {})
    provider_invocation = system.run_active_action(
        app.id, provider_action.action_id, {}
    )

    assert activated.status == "active"
    assert local_invocation.status == "succeeded"
    assert provider_invocation.status == "failed"
    assert "KeyError" in (provider_invocation.error or "")


def test_secret_backend_calls_run_without_active_uow() -> None:
    class ObservedBackend(InMemorySecretBackend):
        def __init__(self) -> None:
            super().__init__()
            self.is_uow_active = lambda: False
            self.observed_states: list[bool] = []

        def set(self, uri: str, value: str) -> None:
            self.observed_states.append(self.is_uow_active())
            super().set(uri, value)

        def get(self, uri: str) -> str:
            self.observed_states.append(self.is_uow_active())
            return super().get(uri)

        def delete(self, uri: str) -> None:
            self.observed_states.append(self.is_uow_active())
            super().delete(uri)

    backend = ObservedBackend()
    system = create_test_runtime(secret_backend=backend)
    backend.is_uow_active = lambda: system.uow.is_active
    app = system.create_application("secret-uow", "Secret UoW")
    reference = system.secrets.create_reference(app.id, "secret://secret-uow/value")

    system.secrets.set_secret(reference.uri, "value")
    assert system.secrets.get_secret(reference.uri) == "value"
    assert system.secrets.allowed_secret_values([reference.id])[0].value == "value"
    system.secrets.delete_secret_value(reference.uri)

    assert backend.observed_states
    assert not any(backend.observed_states)


def test_secret_backend_rejects_caller_owned_uow_without_closing_it() -> None:
    backend = InMemorySecretBackend()
    system = create_test_runtime(secret_backend=backend)
    app = system.create_application("secret-caller", "Secret Caller")
    reference = system.secrets.create_reference(app.id, "secret://secret-caller/value")
    system.secrets.set_secret(reference.uri, "value")

    with system.uow:
        with pytest.raises(
            GraphUnitOfWorkError,
            match="get secret backend value requires an inactive",
        ):
            system.secrets.get_secret(reference.uri)
        assert system.uow.is_active is True
    assert backend.get(reference.uri) == "value"


def test_reference_scoped_secret_management_returns_only_status() -> None:
    backend = InMemorySecretBackend()
    system = create_test_runtime(secret_backend=backend)
    app = system.create_application("managed-secret", "Managed Secret")
    reference = system.secrets.create_reference(
        app.id, "secret://managed-secret/password"
    )

    missing = system.secrets.secret_status(app.id, reference.id)
    configured = system.secrets.set_secret_reference(
        app.id, reference.id, "sentinel-secret-value"
    )
    present = system.secrets.secret_status(app.id, reference.id)
    deleted = system.secrets.delete_secret_reference_value(app.id, reference.id)

    assert missing["configured"] is False
    assert configured["configured"] is True
    assert present["configured"] is True
    assert deleted["configured"] is False
    assert set(configured) == {
        "secret_reference_id",
        "configured",
        "backend",
        "updated_at",
    }
    assert "sentinel-secret-value" not in repr([missing, configured, present, deleted])
    assert reference.uri not in repr(configured)


def test_secret_reference_uri_cannot_cross_application_ownership() -> None:
    system = create_test_runtime(secret_backend=InMemorySecretBackend())
    first = system.create_application("secret-owner-a", "Owner A")
    second = system.create_application("secret-owner-b", "Owner B")
    system.secrets.create_reference(first.id, "secret://shared/password")

    with pytest.raises(ValueError, match="another Application"):
        system.secrets.create_reference(second.id, "secret://shared/password")
