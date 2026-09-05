from __future__ import annotations

import pytest

from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import now_utc
from n4x.system.runtime import SystemRuntime
from n4x.testing import create_test_runtime
from tests.cypher_source import action_source


def test_external_trigger_run_persists_job_and_invocation(
    monkeypatch,
) -> None:
    system, revision_id, action_id = _app_with_counter_action()
    trigger = system.create_trigger(
        revision_id,
        "triggered.external",
        trigger_type="external",
        action_id=action_id,
        input_template={"title": "From trigger"},
        max_attempts=2,
    )
    system.activate_application_revision(revision_id)
    original_run = system.scheduler.actions.run
    action_uow_states: list[bool] = []

    def observe_action_uow(*args, **kwargs):
        action_uow_states.append(system.uow.is_active)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(system.scheduler.actions, "run", observe_action_uow)

    job = system.run_trigger("triggered.external", idempotency_key="same-job")
    repeated = system.run_trigger("triggered.external", idempotency_key="same-job")

    assert job.status == "succeeded"
    assert action_uow_states == [False]
    assert repeated.id == job.id
    assert job.trigger_revision_id == trigger.id
    assert job.invocation_id in system.uow.records.invocations
    assert (
        system.list_application_objects("triggered", "triggered.Counter")[0].values[
            "title"
        ]
        == "From trigger"
    )


def test_event_trigger_dispatches_matching_event_type_only() -> None:
    system, revision_id, action_id = _app_with_counter_action()
    system.create_trigger(
        revision_id,
        "triggered.event",
        trigger_type="event",
        action_id=action_id,
        config={"event_type": "counter.requested"},
        input_template={"title": "Default"},
    )
    system.activate_application_revision(revision_id)

    misses = system.dispatch_event("triggered", "other.event", {"title": "Miss"})
    jobs = system.dispatch_event("triggered", "counter.requested", {"title": "Event"})

    assert misses == []
    assert len(jobs) == 1
    assert jobs[0].status == "succeeded"
    assert (
        system.list_application_objects("triggered", "triggered.Counter")[0].values[
            "title"
        ]
        == "Event"
    )


def test_schedule_trigger_mounts_with_apscheduler_and_records_missed_jobs() -> None:
    system, revision_id, action_id = _app_with_counter_action()
    trigger = system.create_trigger(
        revision_id,
        "triggered.schedule",
        trigger_type="schedule",
        action_id=action_id,
        config={"cron": "*/5 * * * *"},
        input_template={"title": "Scheduled"},
    )
    system.activate_application_revision(revision_id)

    system.scheduler.start()
    try:
        state = system.inspect_scheduler()
        missed = system.scheduler.mark_missed_jobs()
    finally:
        system.scheduler.shutdown()

        assert state["running"] is True
        assert state["active_triggers"][0]["id"] == trigger.id
        assert any(job["id"] == trigger.id for job in state["scheduled_jobs"])
    assert missed[0].status == "missed"


def test_failed_trigger_records_retry_wait_and_then_executes() -> None:
    system = create_test_runtime()
    app = system.create_application("broken-trigger", "Broken Trigger")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.id,
        "actions/broken.py",
        "def run(ctx, input):\n    raise RuntimeError('boom')\n",
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "broken-trigger.run",
        kind="normal",
        entrypoint="actions/broken.py:run",
        source_paths=["actions/broken.py"],
    )
    system.create_trigger(
        revision.id,
        "broken-trigger.external",
        trigger_type="external",
        action_id=action.action_id,
        max_attempts=2,
        retry_policy={"base_seconds": 1},
    )
    system.activate_application_revision(revision.id)

    job = system.run_trigger("broken-trigger.external")

    assert job.status == "retry_wait"
    assert job.error is not None
    assert job.next_retry_at is not None
    assert system.inspect_job_attempts(job.id)

    system.uow.records.job_records.save(
        job.model_copy(update={"next_retry_at": job.scheduled_at})
    )
    retried = system.process_due_job_work()
    assert retried[0].status == "failed"
    assert retried[0].id == job.id
    assert retried[0].attempt == 2


def test_triggers_paused_blocks_every_new_trigger_path_but_not_actions() -> None:
    system, revision_id, action_revision_id = _app_with_counter_action()
    system.create_trigger(
        revision_id,
        "triggered.external",
        trigger_type="external",
        action_id=action_revision_id,
    )
    system.create_trigger(
        revision_id,
        "triggered.event",
        trigger_type="event",
        action_id=action_revision_id,
        config={"event_type": "counter.requested"},
    )
    system.activate_application_revision(revision_id)
    system.application_service.set_status("triggered", "triggers_paused")
    system.scheduler.remount()

    assert system.scheduler.active_trigger_revisions() == []
    assert system.dispatch_event("triggered", "counter.requested", {}) == []
    with pytest.raises(ValidationFailure, match="triggers are paused"):
        system.run_trigger("triggered.external")

    invocation = system.run_active_action(
        "triggered", "triggered.create_counter", {"title": "manual"}
    )
    assert invocation.status == "succeeded"
    assert system.resume_application_triggers("triggered").status == "active"
    assert len(system.scheduler.active_trigger_revisions()) == 2


def _app_with_counter_action() -> tuple[SystemRuntime, str, str]:
    system = create_test_runtime()
    app = system.create_application("triggered", "Triggered")
    revision = system.create_application_revision(app.id)
    system.create_object_type(
        revision.id,
        "triggered.Counter",
        name="Counter",
        properties={"title": {"type": "string"}},
        required=["title"],
    )
    system.source.write_source_file(
        revision.id,
        "actions/counter.py",
        action_source(
            "def run(ctx, input):\n"
            "    obj = upsert_object(ctx, 'triggered.Counter', "
            "{'title': input['title']})\n"
            "    return {'id': obj['id'], 'title': input['title']}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "triggered.create_counter",
        kind="normal",
        entrypoint="actions/counter.py:run",
        source_paths=["actions/counter.py"],
        input_schema={"type": "object", "required": ["title"]},
    )
    return system, revision.id, action.action_id


def test_queued_job_uses_enqueued_revision_tree_after_later_activate() -> None:
    system = create_test_runtime()
    app = system.create_application("markers", "Markers")
    first = system.create_application_revision(app.id)
    system.source.write_source_file(
        first.id,
        "actions/mark.py",
        "def run(ctx, input):\n    return {'marker': 'v1'}\n",
        role="action",
        language="python",
    )
    action = system.create_action(
        first.id,
        "markers.mark",
        kind="normal",
        entrypoint="actions/mark.py:run",
        source_paths=["actions/mark.py"],
    )
    trigger = system.create_trigger(
        first.id,
        "markers.external",
        trigger_type="external",
        action_id=action.action_id,
    )
    system.activate_application_revision(first.id)

    with system.uow:
        job = system.scheduler._record_job(
            app.id,
            trigger,
            {},
            "queued-across-activate",
            status="scheduled",
            scheduled_at=now_utc(),
            queued_at=now_utc(),
            application_revision_id=first.id,
        )

    second = system.create_application_revision(app.id)
    system.source.write_source_file(
        second.id,
        "actions/mark.py",
        "def run(ctx, input):\n    return {'marker': 'v2'}\n",
        role="action",
        language="python",
    )
    system.create_action(
        second.id,
        "markers.mark",
        kind="normal",
        entrypoint="actions/mark.py:run",
        source_paths=["actions/mark.py"],
    )
    system.activate_application_revision(second.id)
    assert system.inspect_application(app.id).active_revision_id == second.id

    completed = system.scheduler._execute_job(job, trigger)
    invocation = system.graph.invocations[completed.invocation_id]
    assert completed.status == "succeeded"
    assert completed.application_revision_id == first.id
    assert invocation.output == {"marker": "v1"}
