from __future__ import annotations

import asyncio
import os
import time

import httpx
import pytest

from n4x.system.http import create_system_http_app
from n4x.kernel.errors import ValidationFailure
from n4x.system.runtime import SystemRuntime
from n4x.runtime.action_supervisor import ActionSupervisor
from n4x.runtime.actions import RuntimePaths
from n4x.kernel.models import ExecutionContext
from n4x.testing import InMemoryGraphStore


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _ctx(application_revision_id: str, application_id: str = "supervised") -> ExecutionContext:
    return ExecutionContext(
        correlation_id=f"supervised-{application_revision_id}",
        application_revision_id=application_revision_id,
        application_id=application_id,
    )


def _supervised_action(
    *,
    workers: int = 1,
    queue_capacity: int = 1,
    heartbeat_interval_seconds: float = 5,
    concurrency_policy: str = "default",
    timeout_seconds: int = 30,
) -> tuple[SystemRuntime, ActionSupervisor, str]:
    system = SystemRuntime(
        InMemoryGraphStore(), runtime_paths=RuntimePaths.temporary()
    )
    system.action_supervisor.shutdown()
    supervisor = ActionSupervisor(
        system.runtime,
        system.uow,
        max_workers=workers,
        queue_capacity=queue_capacity,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )
    system.action_supervisor = supervisor
    system.invocation_service.runtime = supervisor
    system.scheduler.actions = supervisor
    system.development_deployment_service.supervisor = supervisor
    system.create_application("supervised", "Supervised")
    revision = system.create_application_revision("supervised")
    system.source.write_source_file(
        revision.id,
        "actions/wait.py",
        (
            "import time\n\n"
            "def run(ctx, input):\n"
            "    if input.get('log'):\n"
            "        print(input['log'], flush=True)\n"
            "    time.sleep(input['seconds'])\n"
            "    return {'value': input['value']}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "supervised.wait",
        kind="normal",
        entrypoint="actions/wait.py:run",
        source_paths=["actions/wait.py"],
        concurrency_policy=concurrency_policy,
        timeout_seconds=timeout_seconds,
    )
    return system, supervisor, revision.id, action.id


def test_supervisor_persists_queued_running_and_terminal_lifecycle() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action()
    revision = system.graph.action_revisions[action_revision_id]

    queued = supervisor.submit(
        revision,
        {"seconds": 0.2, "value": "done"},
        invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )

    assert queued.status == "queued"
    deadline = time.monotonic() + 2
    while supervisor.inspect(queued.id).status == "queued":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert supervisor.inspect(queued.id).status == "running"

    completed = supervisor.await_invocation(queued.id, timeout=3)
    assert completed.status == "succeeded"
    assert completed.output == {"value": "done"}
    assert completed.started_at is not None
    assert completed.completed_at is not None
    system.close()


def test_supervisor_rejects_work_beyond_worker_and_queue_capacity() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action(
        workers=1, queue_capacity=0
    )
    revision = system.graph.action_revisions[action_revision_id]
    first = supervisor.submit(
        revision,
        {"seconds": 0.2, "value": "first"},
        invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )

    with pytest.raises(ValidationFailure, match="queue is full"):
        supervisor.submit(
            revision,
            {"seconds": 0, "value": "rejected"},
            invocation_kind="draft",
            execution_context=_ctx(application_revision_id),
        )

    assert supervisor.await_invocation(first.id, timeout=3).status == "succeeded"
    system.close()


def test_http_event_loop_remains_responsive_while_action_runs() -> None:
    system, _, application_revision_id, _ = _supervised_action()
    system.activate_application_revision(
        application_revision_id
    )
    app = create_system_http_app(system)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            action_request = asyncio.create_task(
                client.post(
                    "/api/apps/supervised/actions/supervised.wait/invoke",
                    json={"input": {"seconds": 0.4, "value": "done"}},
                )
            )
            await asyncio.sleep(0.05)
            health = await asyncio.wait_for(client.get("/health"), timeout=0.2)
            assert health.status_code == 200
            response = await action_request
            assert response.status_code == 200
            assert response.json()["status"] == "succeeded"

    asyncio.run(scenario())
    system.close()


def test_running_action_streams_logs_and_cancels_process_group() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action()
    revision = system.graph.action_revisions[action_revision_id]
    queued = supervisor.submit(
        revision,
        {"seconds": 10, "value": "late", "log": "started-child"},
        invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )
    deadline = time.monotonic() + 3
    while "started-child" not in supervisor.inspect(queued.id).stdout:
        assert time.monotonic() < deadline
        time.sleep(0.02)

    started_cancel = time.monotonic()
    cancelled = supervisor.cancel(queued.id)

    assert time.monotonic() - started_cancel < 2
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is not None
    assert "started-child" in cancelled.stdout
    system.close()


def test_supervisor_renews_linked_heartbeat_during_long_action() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action(
        heartbeat_interval_seconds=0.05
    )
    revision = system.graph.action_revisions[action_revision_id]
    heartbeats: list[float] = []

    completed = supervisor.run(
        revision,
        {"seconds": 0.25, "value": "done"},
        heartbeat_callback=lambda: heartbeats.append(time.monotonic()),
        execution_context=_ctx(application_revision_id),
    )

    assert completed.status == "succeeded"
    assert len(heartbeats) >= 2
    system.close()


def test_http_submit_inspect_logs_and_cancel_invocation() -> None:
    system, _, application_revision_id, _ = _supervised_action()
    system.activate_application_revision(
        application_revision_id
    )
    app = create_system_http_app(system)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            submitted = await client.post(
                (
                    "/api/apps/supervised/actions/"
                    "supervised.wait/submit"
                ),
                json={
                    "input": {
                        "seconds": 10,
                        "value": "late",
                        "log": "remote-log",
                    }
                },
            )
            assert submitted.status_code == 202
            invocation_id = submitted.json()["id"]
            deadline = time.monotonic() + 3
            while True:
                inspected = await client.get(
                    f"/api/invocations/{invocation_id}"
                )
                if "remote-log" in inspected.json()["stdout"]:
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(0.02)
            cancelled = await client.delete(
                f"/api/invocations/{invocation_id}"
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"

    asyncio.run(scenario())
    system.close()


def test_reject_if_running_policy_is_scoped_to_stable_action() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action(
        workers=2,
        queue_capacity=2,
        concurrency_policy="reject_if_running",
    )
    revision = system.graph.action_revisions[action_revision_id]
    first = supervisor.submit(
        revision, {"seconds": 0.25, "value": "first"}
    , execution_context=_ctx(application_revision_id))
    deadline = time.monotonic() + 2
    while supervisor.inspect(first.id).status == "queued":
        assert time.monotonic() < deadline
        time.sleep(0.01)

    with pytest.raises(
        ValidationFailure, match="rejects overlapping invocation"
    ):
        supervisor.submit(
            revision, {"seconds": 0, "value": "rejected"}
        , execution_context=_ctx(application_revision_id))

    assert supervisor.await_invocation(first.id).status == "succeeded"
    next_invocation = supervisor.run(
        revision, {"seconds": 0, "value": "next"}
    , execution_context=_ctx(application_revision_id))
    assert next_invocation.status == "succeeded"
    system.close()


def test_scheduled_long_action_renews_job_lease() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action(
        heartbeat_interval_seconds=0.05
    )
    action_revision = system.graph.action_revisions[action_revision_id]
    system.create_trigger(
        application_revision_id,
        "supervised.external",
        trigger_type="external",
        action_id=action_revision.action_id,
        input_template={"seconds": 0.25, "value": "done"},
    )
    system.activate_application_revision(
        application_revision_id
    )
    observed = []
    heartbeat_job = system.scheduler._heartbeat_job

    def capture_heartbeat(job_id: str, attempt_id: str) -> None:
        heartbeat_job(job_id, attempt_id)
        observed.append(
            system.graph.job_records[job_id].lease_expires_at
        )

    system.scheduler._heartbeat_job = capture_heartbeat
    completed = system.run_trigger("supervised.external")

    assert completed.status == "succeeded"
    assert len(observed) >= 2
    assert all(value is not None for value in observed)
    system.close()


def test_parallel_action_does_not_redirect_host_process_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action()
    revision = system.graph.action_revisions[action_revision_id]
    queued = supervisor.submit(
        revision, {"seconds": 0.2, "value": "done"}
    , execution_context=_ctx(application_revision_id))
    deadline = time.monotonic() + 2
    while supervisor.inspect(queued.id).status == "queued":
        assert time.monotonic() < deadline
        time.sleep(0.01)

    print("host-process-output")
    completed = supervisor.await_invocation(queued.id)

    assert "host-process-output" in capsys.readouterr().out
    assert "host-process-output" not in completed.stdout
    system.close()


def test_cold_parallel_actions_share_materialization_and_environment() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action(
        workers=8,
        queue_capacity=0,
    )
    revision = system.graph.action_revisions[action_revision_id]

    queued = [
        supervisor.submit(
            revision, {"seconds": 0.05, "value": index}
        , execution_context=_ctx(application_revision_id))
        for index in range(8)
    ]
    completed = [
        supervisor.await_invocation(item.id) for item in queued
    ]

    assert [item.status for item in completed] == ["succeeded"] * 8
    assert {item.output["value"] for item in completed} == set(range(8))
    system.close()


def test_serial_invokes_reuse_one_pid_for_draft_and_active() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action()
    revision = system.graph.action_revisions[action_revision_id]
    first = supervisor.run(
        revision, {"seconds": 0, "value": "one"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )
    second = supervisor.run(
        revision, {"seconds": 0, "value": "two"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )
    system.activate_application_revision(application_revision_id)
    third = supervisor.run(
        revision,
        {"seconds": 0, "value": "three"},
        invocation_kind="active",
        execution_context=_ctx(application_revision_id),
    )

    assert first.status == second.status == third.status == "succeeded"
    assert first.metadata["pid"] == second.metadata["pid"] == third.metadata["pid"]
    system.close()


def test_overlapping_invokes_burst_two_pids() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action(
        workers=2, queue_capacity=2
    )
    revision = system.graph.action_revisions[action_revision_id]
    first = supervisor.submit(
        revision, {"seconds": 0.3, "value": "a"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )
    second = supervisor.submit(
        revision, {"seconds": 0.3, "value": "b"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )
    completed = [
        supervisor.await_invocation(first.id),
        supervisor.await_invocation(second.id),
    ]

    assert [item.status for item in completed] == ["succeeded", "succeeded"]
    assert completed[0].metadata["pid"] != completed[1].metadata["pid"]
    system.close()


def test_activate_kills_superseded_revision_child() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action()
    revision = system.graph.action_revisions[action_revision_id]
    first = supervisor.run(
        revision, {"seconds": 0, "value": "a"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )
    pid = first.metadata["pid"]
    system.activate_application_revision(application_revision_id)
    assert system.runtime.pool.contains_pid(pid)
    next_revision = system.create_application_revision("supervised")
    system.activate_application_revision(next_revision.id)

    assert first.status == "succeeded"
    assert not system.runtime.pool.contains_pid(pid)
    assert not _pid_alive(pid)
    replacement = system.source.bindings.action_revision(
        next_revision.id, "supervised.wait"
    )
    second = supervisor.run(
        replacement, {"seconds": 0, "value": "b"}, invocation_kind="draft",
        execution_context=_ctx(next_revision.id),
    )
    assert second.status == "succeeded"
    assert second.metadata["pid"] != pid
    system.close()


def test_expire_development_deployment_kills_data_space_child() -> None:
    system, supervisor, application_revision_id, _ = _supervised_action()
    application_id = system.graph.revisions[application_revision_id].application_id
    system.create_experience("supervised-ui", "Supervised UI")
    experience_revision = system.create_experience_revision(
        "supervised-ui",
        application_access=[{"application_id": application_id}],
    )
    deployment = system.create_development_deployment(
        experience_revision.id,
        {application_id: application_revision_id},
    )
    first = system.run_development_action(
        deployment.id,
        application_id,
        "supervised.wait",
        {"seconds": 0, "value": "preview"},
    )
    pid = first.metadata["pid"]
    assert first.status == "succeeded", first.error
    assert system.runtime.pool.contains_pid(pid)
    system.expire_development_deployment(deployment.id)

    assert not system.runtime.pool.contains_pid(pid)
    assert not _pid_alive(pid)
    system.close()


def test_timeout_drops_child_from_pool() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action(timeout_seconds=1)
    revision = system.graph.action_revisions[action_revision_id]
    queued = supervisor.submit(
        revision, {"seconds": 10, "value": "late"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )
    deadline = time.monotonic() + 2
    while supervisor.inspect(queued.id).status == "queued":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    while not system.runtime.pool._children:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    pids = [child.process.pid for child in system.runtime.pool._children]
    completed = supervisor.await_invocation(queued.id, timeout=5)

    assert completed.status == "failed"
    assert completed.metadata.get("error_code") == "action_timed_out"
    assert pids
    assert all(not system.runtime.pool.contains_pid(pid) for pid in pids)
    assert all(not _pid_alive(pid) for pid in pids)
    system.close()


def test_pool_size_zero_never_retains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("N4X_ACTION_POOL_SIZE", "0")
    system, supervisor, application_revision_id, action_revision_id = _supervised_action()
    revision = system.graph.action_revisions[action_revision_id]
    first = supervisor.run(
        revision, {"seconds": 0, "value": "one"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )
    second = supervisor.run(
        revision, {"seconds": 0, "value": "two"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )

    assert first.status == second.status == "succeeded"
    assert first.metadata["pid"] != second.metadata["pid"]
    system.close()


def test_idle_reclaim_drops_unused_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("N4X_ACTION_POOL_IDLE_SECONDS", "0.2")
    system, supervisor, application_revision_id, action_revision_id = _supervised_action()
    revision = system.graph.action_revisions[action_revision_id]
    first = supervisor.run(
        revision, {"seconds": 0, "value": "one"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )
    time.sleep(0.4)
    second = supervisor.run(
        revision, {"seconds": 0, "value": "two"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )

    assert first.status == second.status == "succeeded"
    assert first.metadata["pid"] != second.metadata["pid"]
    system.close()


def test_new_python_environment_does_not_reuse_prior_interpreter() -> None:
    system, supervisor, application_revision_id, action_revision_id = _supervised_action()
    revision = system.graph.action_revisions[action_revision_id]
    first = supervisor.run(
        revision, {"seconds": 0, "value": "one"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )
    pid = first.metadata["pid"]
    system.create_runtime_dependency(
        application_revision_id, "python", "packaging", "==25.0"
    )
    second = supervisor.run(
        revision, {"seconds": 0, "value": "two"}, invocation_kind="draft",
        execution_context=_ctx(application_revision_id),
    )

    assert first.status == second.status == "succeeded"
    assert second.metadata["pid"] != pid
    assert not system.runtime.pool.contains_pid(pid)
    assert not _pid_alive(pid)
    system.close()
