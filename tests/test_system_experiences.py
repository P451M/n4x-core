from __future__ import annotations

import pytest

from n4x.kernel.errors import ConcurrentGraphUpdateError, ValidationFailure
from n4x.runtime.actions import RuntimePaths
from n4x.system.runtime import SystemRuntime
from n4x.testing import tree_id
from n4x.testing.graph_store import InMemoryGraphStore


def test_system_creates_and_retires_experience() -> None:
    runtime = SystemRuntime(InMemoryGraphStore())
    runtime.applications.create("mail", "Mail")
    experience = runtime.experiences.create("mail-ui", "Mail UI", "Inbox")
    assert experience.id == "mail-ui"
    revision = runtime.experiences.create_revision(
        "mail-ui",
        ui_profile="none",
        application_access=[{"application_id": "mail"}],
    )
    assert revision.id.startswith("mail-ui.experience@")
    assert revision.ui_profile == "none"
    assert revision.application_access[0].application_id == "mail"
    runtime.uow.experiences.replace_active_revision(
        "mail-ui", revision.id, expected_revision_id=None
    )
    runtime.uow.experiences.save(
        experience.model_copy(update={"active_revision_id": revision.id})
    )
    with pytest.raises(ConcurrentGraphUpdateError, match="changed"):
        runtime.experiences.retire(
            "mail-ui", expected_active_revision_id="stale-revision"
        )
    retired = runtime.experiences.retire(
        "mail-ui", expected_active_revision_id=revision.id
    )
    assert retired.status == "disabled"
    assert retired.active_revision_id is None
    assert runtime.experiences.retire("mail-ui") == retired


def test_system_experience_draft_inherits_source_and_surface() -> None:
    runtime = SystemRuntime(InMemoryGraphStore())
    runtime.applications.create("mail", "Mail")
    experience = runtime.experiences.create("mail-ui", "Mail UI")
    first = runtime.experiences.create_revision("mail-ui", ui_profile="none")
    runtime.source.write_source_file(
        first.id,
        "surfaces/inbox.tsx",
        "export default function Inbox() { return null }\n",
        role="surface",
        language="tsx",
    )
    runtime.experiences.create_runtime_dependency(
        first.id, "javascript", "react", "^19"
    )
    surface = runtime.experiences.surfaces.create(
        first.id,
        "inbox",
        surface_type="browser",
        surface_type_version=1,
        entrypoint="surfaces/inbox.tsx",
        source_paths=["surfaces/inbox.tsx"],
        title="Inbox",
    )
    runtime.source.intern_tree(first.id)
    runtime.uow.experiences.save_revision(
        first.model_copy(update={"status": "active"})
    )
    runtime.uow.experiences.replace_active_revision(
        "mail-ui", first.id, expected_revision_id=None
    )
    runtime.uow.experiences.save(
        experience.model_copy(update={"active_revision_id": first.id})
    )

    draft = runtime.experiences.create_revision("mail-ui")
    assert draft.parent_revision_id == first.id
    assert draft.ui_profile == "none"
    cloned = runtime.source.read_source_file(
        tree_id(runtime, draft), "surfaces/inbox.tsx"
    )
    assert "Inbox" in cloned.content
    cloned_surfaces = runtime.experiences.surfaces.list(draft.id)
    assert [item.surface_id for item in cloned_surfaces] == ["inbox"]
    assert cloned_surfaces[0].id == surface.id
    assert tree_id(runtime, draft) == tree_id(runtime, first)
    inspected = runtime.experiences.inspect_revision(draft.id)
    assert inspected["surfaces"][0]["surface_id"] == "inbox"
    assert inspected["dependencies"][0]["package"] == "react"


def test_system_validates_and_activates_experience() -> None:
    runtime = SystemRuntime(
        InMemoryGraphStore(), runtime_paths=RuntimePaths.temporary()
    )
    try:
        runtime.applications.create("mail", "Mail")
        app_revision = runtime.applications.create_revision("mail")
        runtime.activation.activate(app_revision.id)
        runtime.experiences.create("mail-ui", "Mail UI")
        revision = runtime.experiences.create_revision(
            "mail-ui",
            ui_profile="none",
            application_access=[{"application_id": "mail"}],
        )
        report = runtime.experience_activation.validate(revision.id)
        assert report.status == "passed"
        activated = runtime.experience_activation.activate(revision.id)
        assert activated.status == "active"
        assert (
            runtime.uow.records.experiences["mail-ui"].active_revision_id
            == revision.id
        )
        context = runtime.platform_authoring.inspect_experience_design_context(
            revision.id, include_content=False
        )
        assert context["advisory_only"] is True
        assert context["revision"]["ui_profile"] == "none"
    finally:
        runtime.close()


def test_inspect_experience_names_unknown_id() -> None:
    runtime = SystemRuntime(InMemoryGraphStore())
    runtime.experiences.create("office", "Office")
    with pytest.raises(ValidationFailure, match="office.experience"):
        runtime.experiences.inspect("office.experience")
