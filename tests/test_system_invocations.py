from __future__ import annotations

from n4x.runtime.actions import RuntimePaths
from n4x.system.runtime import SystemRuntime
from n4x.testing.graph_store import InMemoryGraphStore


def test_system_runs_draft_action_and_records_callback() -> None:
    runtime = SystemRuntime(
        InMemoryGraphStore(), runtime_paths=RuntimePaths.temporary()
    )
    try:
        runtime.applications.create("mail", "Mail")
        draft = runtime.applications.create_revision("mail")
        runtime.source.write_source_file(
            draft.id,
            "actions/echo.py",
            "def run(ctx, input):\n    return {'value': input['value']}\n",
            role="action",
            language="python",
        )
        action = runtime.definitions.create_action(
            draft.id,
            "mail.echo",
            kind="normal",
            entrypoint="actions/echo.py:run",
            source_paths=["actions/echo.py"],
        )
        runtime.definitions.create_test_case(
            draft.id, action.action_id, {"value": "ok"}, {"value": "ok"}
        )
        invocation = runtime.invocations.run_draft_action(
            draft.id, action.action_id, {"value": "ok"}
        )
        assert invocation.status == "succeeded"
        assert invocation.output == {"value": "ok"}
        tests = runtime.invocations.run_application_tests(draft.id)
        assert [item.status for item in tests] == ["succeeded"]
        route = runtime.invocations.create_callback_route(
            "mail", action.id, state="oauth"
        )
        assert route.target_action_revision_id == action.id
        listed = runtime.invocations.inspect_callback_routes("mail")
        assert [item.id for item in listed] == [route.id]
        report = runtime.activation.validate(draft.id)
        assert report.status == "passed"
        activated = runtime.activation.activate(draft.id)
        assert activated.status == "active"
        assert runtime.uow.records.applications["mail"].active_revision_id == draft.id
        active = runtime.invocations.run_active_action(
            "mail", "mail.echo", {"value": "live"}
        )
        assert active.status == "succeeded"
        assert active.output == {"value": "live"}
    finally:
        runtime.close()
