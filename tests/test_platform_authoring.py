from __future__ import annotations

from dataclasses import replace

import pytest
from n4x.graph.store import node_ref
from n4x.kernel.errors import PlatformReleaseConflictError
from n4x.kernel.models import (
    AuthoringGuide,
    AuthoringGuideRevision,
    UiTheme,
    UiThemeRevision,
)
from n4x.system.authoring import PlatformAuthoringService
from n4x.runtime.surface_theme import load_packaged_authoring_release
from n4x.testing import create_test_runtime


def test_packaged_authoring_release_is_idempotent_and_graph_backed() -> None:
    system = create_test_runtime()

    first = system.platform_authoring_release
    second = system.platform_authoring_service.bootstrap_packaged_release()
    theme = system.inspect_surface_theme()
    guide = system.inspect_authoring_guide()

    assert first == second
    assert first["theme_revision_id"] == "n4x.ui-theme@n4x-ui-v8"
    assert first["guide_revision_id"] == ("n4x.authoring-guide@n4x-ui-v8")
    assert len(system.uow.records.ui_theme_revisions) == 1
    assert len(system.uow.records.authoring_guide_revisions) == 1
    assert theme["css_text"] == theme["active_revision"]["css_text"]
    assert "--background: oklch(0.9881 0 0);" in theme["css_text"]
    assert "--background: oklch(0.3225 0.0107 278.2517);" in theme["css_text"]
    assert "--font-sans: Noto Sans JP, ui-sans-serif" in theme["css_text"]
    assert guide["content"] == guide["active_revision"]["content"]
    assert "application_id" not in UiTheme.model_fields
    assert "application_id" not in UiThemeRevision.model_fields
    assert {
        "application_id",
        "content",
        "version",
    }.isdisjoint(AuthoringGuide.model_fields)
    assert "application_id" not in AuthoringGuideRevision.model_fields
    assert system.store.list_edges(
        node_ref("N4XRoot", id="n4x"),
        "HAS_UI_THEME",
        node_ref("UiTheme", id="n4x.ui-theme"),
    )
    assert system.store.list_edges(
        node_ref("N4XRoot", id="n4x"),
        "HAS_AUTHORING_GUIDE",
        node_ref("AuthoringGuide", id="n4x.authoring-guide"),
    )


@pytest.mark.parametrize("content_field", ["css_text", "guide_content"])
def test_same_release_version_rejects_changed_packaged_content(
    content_field: str,
) -> None:
    system = create_test_runtime()
    seed = load_packaged_authoring_release()
    changed = replace(
        seed,
        **{content_field: (f"{getattr(seed, content_field)}\n<!-- changed -->\n")},
    )
    conflicting = PlatformAuthoringService(system.uow, changed)
    active_before = system.inspect_surface_theme()["active_revision_id"]
    guide_active_before = system.inspect_authoring_guide()["active_revision_id"]

    with pytest.raises(
        PlatformReleaseConflictError,
        match="publish a new release version",
    ):
        conflicting.bootstrap_packaged_release()

    assert system.inspect_surface_theme()["active_revision_id"] == active_before
    assert system.inspect_authoring_guide()["active_revision_id"] == guide_active_before
    assert len(system.uow.records.ui_theme_revisions) == 1
    assert len(system.uow.records.authoring_guide_revisions) == 1


def test_version_bump_publishes_and_activates_theme_and_guide_atomically() -> None:
    system = create_test_runtime()
    seed = load_packaged_authoring_release()
    original_theme = system.uow.records.ui_theme_revisions["n4x.ui-theme@n4x-ui-v8"]
    original_guide = system.uow.records.authoring_guide_revisions[
        "n4x.authoring-guide@n4x-ui-v8"
    ]
    upgraded = replace(
        seed,
        release_version="n4x-ui-v9",
        css_text=f"{seed.css_text}\n/* release v9 */\n",
        guide_content=f"{seed.guide_content}\n\nRelease v9 guidance.\n",
    )

    published = PlatformAuthoringService(
        system.uow, upgraded
    ).bootstrap_packaged_release()

    assert published["theme_revision_id"] == "n4x.ui-theme@n4x-ui-v9"
    assert published["guide_revision_id"] == ("n4x.authoring-guide@n4x-ui-v9")
    assert (
        system.uow.records.ui_themes["n4x.ui-theme"].active_revision_id
        == published["theme_revision_id"]
    )
    assert (
        system.uow.records.authoring_guides["n4x.authoring-guide"].active_revision_id
        == published["guide_revision_id"]
    )
    assert (
        system.uow.records.ui_theme_revisions["n4x.ui-theme@n4x-ui-v8"].status
        == "superseded"
    )
    assert (
        system.uow.records.authoring_guide_revisions[
            "n4x.authoring-guide@n4x-ui-v8"
        ].status
        == "superseded"
    )
    assert system.uow.records.ui_theme_revisions["n4x.ui-theme@n4x-ui-v8"].model_dump(
        exclude={"status"}
    ) == original_theme.model_dump(exclude={"status"})
    assert system.uow.records.authoring_guide_revisions[
        "n4x.authoring-guide@n4x-ui-v8"
    ].model_dump(exclude={"status"}) == original_guide.model_dump(exclude={"status"})
    assert len(system.uow.records.ui_theme_revisions) == 2
    assert len(system.uow.records.authoring_guide_revisions) == 2


def test_packaged_guide_uses_pinned_dashboard_and_mail_references() -> None:
    seed = load_packaged_authoring_release()
    references = {item["id"]: item for item in seed.references}

    assert (
        references["dashboard-tweakcn"]["commit_sha"]
        == "d21c8e3d4071f7f738e46c92470abf9e15bfda04"
    )
    assert (
        references["dashboard-shadcn"]["commit_sha"]
        == "4e88ab81ae1d1550165db949a903c691a04f699c"
    )
    assert (
        references["mail-tweakcn"]["commit_sha"]
        == "d21c8e3d4071f7f738e46c92470abf9e15bfda04"
    )
    assert 'Sidebar` with `variant="inset"`' in seed.guide_content
    assert "SidebarInset" in seed.guide_content
    assert "list/detail/compose" in seed.guide_content
    assert "Application, Marketing, and Music" in seed.guide_content
    assert "Never copy demo information architecture" in seed.guide_content
    assert "Every visible control" in seed.guide_content
    assert "config.pwa: {}" in seed.guide_content
    assert "create_experience" in seed.guide_content


def test_experience_design_context_is_advisory_and_profile_aware() -> None:
    system = create_test_runtime()
    system.create_experience("design-context", "Design Context")
    default_revision = system.create_experience_revision("design-context")

    full = system.inspect_experience_design_context(default_revision.id)
    compact = system.inspect_experience_design_context(
        default_revision.id, include_content=False
    )

    assert full["design_context_version"] == "n4x.experience.design-context.v1"
    assert full["advisory_only"] is True
    assert full["revision"]["ui_profile"] == "n4x-default"
    assert full["platform_release"]["release_version"] == "n4x-ui-v8"
    assert "content" in full["platform_release"]["authoring_guide"]
    assert "css_text" in full["platform_release"]["surface_theme"]
    assert "content" not in compact["platform_release"]["authoring_guide"]
    assert "css_text" not in compact["platform_release"]["surface_theme"]
    assert "shadcn/ui" in full["profile_guidance"]
    assert full["full_inspection_tools"]["validation"] == (
        "validate_experience_revision"
    )
    assert any(item["id"] == "review-visually" for item in full["workflow"])
    assert full["visual_review_checklist"]
    assert full["content_hash"]
    skipped = system.inspect_experience_design_context(
        default_revision.id, last_seen_hash=full["content_hash"]
    )
    assert skipped["unchanged"] is True
    assert skipped["content_hash"] == full["content_hash"]
    assert "platform_release" not in skipped
    assert "content" not in skipped.get("authoring_guide", {})

    system.discard_experience_revision(default_revision.id)
    custom_revision = system.create_experience_revision(
        "design-context", ui_profile="custom"
    )
    custom = system.inspect_experience_design_context(custom_revision.id)
    assert "user-selected custom design system" in custom["profile_guidance"]
    assert custom["advisory_only"] is True
    assert custom["content_hash"] != full["content_hash"]
    stale_profile = system.inspect_experience_design_context(
        custom_revision.id, last_seen_hash=full["content_hash"]
    )
    assert stale_profile.get("unchanged") is not True
    assert "user-selected custom design system" in stale_profile["profile_guidance"]


