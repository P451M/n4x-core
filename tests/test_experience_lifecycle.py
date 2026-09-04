from __future__ import annotations

import pytest

from n4x.graph.store import node_ref
from n4x.kernel.errors import ConcurrentGraphUpdateError, ValidationFailure
from n4x.system.runtime import SystemRuntime
from n4x.runtime.actions import RuntimePaths
from n4x.testing import create_test_runtime


def test_new_dependency_writes_enforce_revision_kind() -> None:
    system = create_test_runtime()
    app = system.create_application("ownership", "Ownership")
    app_revision = system.create_application_revision(app.id)
    experience = system.create_experience("ownership-ui", "Ownership UI")
    experience_revision = system.create_experience_revision(experience.id)
    with pytest.raises(ValueError, match="ApplicationRevision.*python"):
        system.application_service.create_runtime_dependency(app_revision.id, "javascript", "react", "^19")
    with pytest.raises(ValueError, match="ExperienceRevision.*javascript"):
        system.create_experience_runtime_dependency(experience_revision.id, "python", "httpx", ">=0.28")
    javascript = system.create_experience_runtime_dependency(experience_revision.id, "javascript", "react", "^19")
    with pytest.raises(ValueError, match="Action dependencies must be Python"):
        system.create_action(app_revision.id, "ownership.invalid", kind="normal", entrypoint="actions/run.py:run", source_paths=[], dependency_ids=[javascript.id])


def test_experience_validation_requires_active_declared_applications() -> None:
    system = create_test_runtime()
    app = system.create_application("declared", "Declared")
    app_revision = system.create_application_revision(app.id)
    experience = system.create_experience("declared-ui", "Declared UI")
    revision = system.create_experience_revision(experience.id, ui_profile="none", application_access=[{"application_id": app.id}])
    failed = system.validate_experience_revision(revision.id)
    system.activate_application_revision(app_revision.id)
    passed = system.validate_experience_revision(revision.id)
    assert failed.status == "failed"
    assert "application is not active" in failed.errors[0]
    assert passed.status == "passed"


def test_retire_experience_is_idempotent_preserves_history_and_survives_restart() -> None:
    system = create_test_runtime()
    app = system.create_application("backend-data", "Backend Data")
    app_revision = system.create_application_revision(app.id)
    system.activate_application_revision(app_revision.id)
    experience = system.create_experience("staging-ui", "Staging UI")
    revision = system.create_experience_revision(experience.id, ui_profile="none", application_access=[{"application_id": app.id}])
    system.activate_experience_revision(revision.id)
    application_nodes = system.store.list_nodes("Application")
    with pytest.raises(ConcurrentGraphUpdateError, match="changed"):
        system.retire_experience(experience.id, expected_active_revision_id="stale-revision")
    retired = system.retire_experience(experience.id, expected_active_revision_id=revision.id)
    assert system.retire_experience(experience.id) == retired
    assert retired.status == "disabled"
    assert retired.active_revision_id is None
    assert not system.store.list_edges(from_ref=node_ref("Experience", id=experience.id), edge_type="ACTIVE_REVISION")
    assert system.store.list_nodes("Application") == application_nodes
    assert system.inspect_experience_revision(revision.id)["source_files"] == []
    restarted = SystemRuntime(system.store, runtime_paths=RuntimePaths.temporary())
    assert restarted.experiences.inspect(experience.id)["experience"]["status"] == "disabled"
    assert restarted.validate_graph_shape()["ok"] is True
    with pytest.raises(ValidationFailure, match="draft"):
        restarted.activate_experience_revision(revision.id)


def test_disabled_experience_draft_can_be_revised_and_activated() -> None:
    system = create_test_runtime()
    app = system.create_application("office-notes", "Notes")
    app_revision = system.create_application_revision(app.id)
    system.activate_application_revision(app_revision.id)
    experience = system.create_experience("office", "Office")
    imported = system.create_experience_revision(
        experience.id,
        ui_profile="none",
        application_access=[{"application_id": app.id}],
    )
    with system.uow:
        current = system.graph.experiences[experience.id]
        system.uow.experiences.save(
            current.model_copy(
                update={"status": "disabled", "active_revision_id": None}
            )
        )
    child = system.create_experience_revision(experience.id)
    assert child.parent_revision_id == imported.id
    assert child.application_access[0].application_id == app.id
    activated = system.activate_experience_revision(child.id)
    assert activated.status == "active"
    live = system.experiences.inspect(experience.id)["experience"]
    assert live["status"] == "active"
    assert live["active_revision_id"] == child.id
