from __future__ import annotations

import pytest
from n4x.contracts import CALLBACK_CONTRACT_SCHEMA, CALLBACK_CONTRACT_VERSION
from n4x.system.http import create_system_http_app
from n4x.kernel.errors import ValidationFailure
from n4x.system.runtime import SystemRuntime
from n4x.testing import create_test_runtime
from starlette.testclient import TestClient


@pytest.mark.contract
def test_callback_contract_version_is_stable() -> None:
    assert CALLBACK_CONTRACT_VERSION == "n4x.callback.v1"
    assert CALLBACK_CONTRACT_SCHEMA["version"] == CALLBACK_CONTRACT_VERSION
    assert CALLBACK_CONTRACT_SCHEMA["http"]["path"] == "/callback/{route_id}"


def test_callback_route_dispatches_to_target_action_and_marks_used() -> None:
    system, action_revision_id = _callback_app()
    route = system.create_callback_route(
        "callbacks", action_revision_id, state="expected-state"
    )

    invocation = system.dispatch_callback_route(
        route.id,
        {"state": "expected-state", "query": {"code": "abc"}, "body": None},
    )

    assert invocation.status == "succeeded"
    assert invocation.invocation_kind == "callback"
    assert invocation.output == {"code": "abc", "state": "expected-state"}
    routes = system.uow.records.callback_routes
    assert routes[route.id].status == "used"
    assert (
        routes[route.id].metadata["invocation_id"]
        == invocation.id
    )


def test_callback_route_rejects_state_mismatch() -> None:
    system, action_revision_id = _callback_app()
    route = system.create_callback_route(
        "callbacks", action_revision_id, state="expected-state"
    )

    with pytest.raises(ValidationFailure):
        system.dispatch_callback_route(route.id, {"state": "wrong"})

    assert system.uow.records.callback_routes[route.id].status == "pending"


def test_http_callback_route_dispatches_request_payload() -> None:
    system, action_revision_id = _callback_app()
    route = system.create_callback_route(
        "callbacks", action_revision_id, state="expected-state"
    )

    client = TestClient(create_system_http_app(system))
    response = client.get(f"/callback/{route.id}?state=expected-state&code=abc")

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["output"] == {"code": "abc", "state": "expected-state"}


def _callback_app() -> tuple[SystemRuntime, str]:
    system = create_test_runtime()
    app = system.create_application("callbacks", "Callbacks")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.id,
        "actions/callback.py",
        (
            "def handle(ctx, input):\n"
            "    return {'code': input['query'].get('code'), 'state': input.get('state')}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "callbacks.handle",
        kind="normal",
        entrypoint="actions/callback.py:handle",
        source_paths=["actions/callback.py"],
        input_schema={"type": "object", "required": ["state", "query"]},
    )
    system.activate_application_revision(revision.id)
    return system, action.id
