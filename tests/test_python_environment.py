from __future__ import annotations

import pytest

from n4x.kernel.errors import ValidationFailure
from n4x.testing import create_test_runtime


def test_action_runs_with_per_revision_uv_environment_and_dependency_lock() -> None:
    system = create_test_runtime()
    app = system.create_application("deps", "Dependencies")
    revision = system.create_application_revision(app.id)
    dependency = system.create_runtime_dependency(
        revision.id, "python", "packaging", "==25.0"
    )
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/check_packaging.py",
        (
            "from packaging.version import parse\n\n"
            "def run(ctx, input):\n"
            "    return {'normalized': str(parse(input['version']))}\n"
        ),
        role="action",
        language="python",
    )
    action = system.create_action(
        revision.id,
        "deps.check_packaging",
        kind="normal",
        entrypoint="actions/check_packaging.py:run",
        source_paths=["actions/check_packaging.py"],
        dependency_ids=[dependency.id],
        input_schema={"type": "object", "required": ["version"]},
    )

    invocation = system.run_draft_action(action.id, {"version": "1.0.0"})

    assert invocation.status == "succeeded"
    assert invocation.output == {"normalized": "1.0.0"}
    records = system.uow.records
    environment = next(iter(records.python_environments.values()))
    assert environment.status == "ready"
    assert environment.dependency_ids == [dependency.id]
    assert "packaging==25.0" in environment.lock_metadata["freeze"]
    assert any(
        artifact.artifact_type == "python_environment"
        for artifact in records.build_artifacts.values()
    )
    assert any(
        artifact.artifact_type == "materialized_source"
        for artifact in records.build_artifacts.values()
    )


def test_dependency_resolution_failure_blocks_activation() -> None:
    system = create_test_runtime()
    app = system.create_application("broken-deps", "Broken Dependencies")
    revision = system.create_application_revision(app.id)
    dependency = system.create_runtime_dependency(
        revision.id, "python", "not a valid requirement", ""
    )
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/noop.py",
        "def run(ctx, input):\n    return {'ok': True}\n",
        role="action",
        language="python",
    )
    system.create_action(
        revision.id,
        "broken-deps.noop",
        kind="normal",
        entrypoint="actions/noop.py:run",
        source_paths=["actions/noop.py"],
        dependency_ids=[dependency.id],
    )

    with pytest.raises(ValidationFailure):
        system.activate_application_revision(revision.id)

    records = system.uow.records
    assert records.revisions[revision.id].status == "rejected"
    environments = records.python_environments.values()
    assert environments
    assert all(environment.status == "failed" for environment in environments)
