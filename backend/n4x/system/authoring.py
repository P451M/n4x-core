from __future__ import annotations

from typing import Any

from n4x.contracts import (
    ACTION_CONTEXT_VERSION,
    ACTION_SUPERVISOR_SCHEMA,
    ACTION_SUPERVISOR_VERSION,
    CALLBACK_CONTRACT_SCHEMA,
    CALLBACK_CONTRACT_VERSION,
    EXPERIENCE_BRIDGE_SCHEMA,
    EXPERIENCE_BRIDGE_VERSION,
    EXECUTION_CONTEXT_SCHEMA,
    EXECUTION_CONTEXT_VERSION,
    FILE_DELIVERY_CONTRACT_SCHEMA,
    FILE_DELIVERY_CONTRACT_VERSION,
    GRAPH_METAMODEL_SCHEMA,
    GRAPH_METAMODEL_VERSION,
    MCP_AUTHORING_SCHEMA,
    MCP_AUTHORING_VERSION,
    SUBPROCESS_PROTOCOL_VERSION,
)
from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import PlatformReleaseConflictError
from n4x.kernel.hash import sha256_text
from n4x.kernel.models import (
    AuthoringGuide,
    AuthoringGuideRevision,
    UiTheme,
    UiThemeRevision,
    now_utc,
)
from n4x.graph.service_base import ServiceBase, transactional
from n4x.runtime.surface_theme import (
    PackagedAuthoringRelease,
    load_packaged_authoring_release,
)

COMPONENT_PALETTE = (
    "Accordion",
    "Alert",
    "AlertDialog",
    "AspectRatio",
    "Avatar",
    "Badge",
    "Breadcrumb",
    "Button",
    "Calendar",
    "Card",
    "Carousel",
    "Checkbox",
    "Collapsible",
    "Command",
    "ContextMenu",
    "DataTable",
    "DatePicker",
    "Dialog",
    "Drawer",
    "DropdownMenu",
    "Form",
    "HoverCard",
    "Input",
    "InputOTP",
    "Label",
    "Menubar",
    "NavigationMenu",
    "Pagination",
    "Popover",
    "Progress",
    "RadioGroup",
    "Resizable",
    "ScrollArea",
    "Select",
    "Separator",
    "Sheet",
    "Sidebar",
    "Skeleton",
    "Slider",
    "Sonner",
    "Switch",
    "Table",
    "Tabs",
    "Textarea",
    "Toggle",
    "ToggleGroup",
    "Tooltip",
)

EXPERIENCE_DESIGN_CONTEXT_VERSION = "n4x.experience.design-context.v1"

EXPERIENCE_AUTHORING_WORKFLOW = (
    {
        "id": "inspect-authority",
        "instruction": (
            "Use this design context as the active graph-owned authority before "
            "writing or editing Experience Surface source."
        ),
    },
    {
        "id": "inspect-existing-source",
        "instruction": (
            "Inspect the Experience revision's existing source files and reuse its "
            "component foundation, shell, and conventions before adding new source."
        ),
    },
    {
        "id": "establish-component-foundation",
        "instruction": (
            "For n4x-default, establish or reuse Experience-owned shadcn/ui "
            "components mapped to the active theme before broad feature work."
        ),
        "applies_when": {"ui_profile": "n4x-default"},
    },
    {
        "id": "compose-product-ui",
        "instruction": (
            "Compose product-specific screens from the selected design system. "
            "Do not treat semantic token usage alone as component-system compliance."
        ),
    },
    {
        "id": "paint-first",
        "instruction": (
            "A click opens the next screen from local state. Persist with invoke "
            "in the background and roll back on failure. Do not await create or "
            "mark to navigate, and do not use a global busy for those gestures."
        ),
    },
    {
        "id": "build-incrementally",
        "instruction": (
            "Build changed Surfaces incrementally and resolve layout, dependency, "
            "and runtime issues before expanding the change."
        ),
    },
    {
        "id": "review-visually",
        "instruction": (
            "After Surfaces are built, create or reuse a DevelopmentDeployment and "
            "open its preview_url. Do not activate to preview or to share a URL. "
            "On a public origin the URL is a deployment-capability path; do not "
            "complete OIDC to open it. Local loopback has no sign-in. When "
            "browser capability is available, capture and inspect screenshots "
            "of every changed Surface in relevant desktop, narrow, light, and dark "
            "states. Report any state that could not be visually verified."
        ),
    },
    {
        "id": "validate-separately",
        "instruction": (
            "Use validate_experience_revision for structural validation before "
            "activation; this advisory design context does not produce pass/fail "
            "UI judgments."
        ),
    },
)

EXPERIENCE_VISUAL_REVIEW_CHECKLIST = (
    "Visual hierarchy clearly identifies the primary work and actions.",
    "Spacing, density, typography, borders, and elevation are internally consistent.",
    "Navigation, selected, hover, focus, disabled, loading, empty, and error states are coherent.",
    "Content does not overflow, clip, or collapse at the reviewed viewport sizes.",
    "Light and dark appearances retain readable contrast and the intended hierarchy.",
    "Visible controls and statuses correspond to graph data or active actions.",
    "Create, mark, and navigate show the next state without waiting on invoke.",
    "MCP App / widget HTML is not the / browser Surface and is not visual proof of it.",
)

UI_PROFILE_GUIDANCE = {
    "n4x-default": (
        "Use the active N4X theme and Experience-owned shadcn/ui components. "
        'Use Sidebar variant="inset" with SidebarInset for standalone browser '
        "application chrome. Do not replace the component system with ad-hoc "
        "lookalike primitives."
    ),
    "custom": (
        "The user-selected custom design system is authoritative. Do not mix it "
        "with N4X default theme or component conventions unless the user explicitly "
        "requests that combination."
    ),
    "none": (
        "No platform design system is selected. The Experience owns its complete "
        "visual implementation; platform theme and component guidance are optional."
    ),
}


class PlatformAuthoringService(ServiceBase):
    """Publishes and inspects the System-owned UI authoring release."""

    def __init__(
        self,
        uow: GraphUnitOfWork,
        packaged_release: PackagedAuthoringRelease | None = None,
    ) -> None:
        super().__init__(uow)
        self.packaged_release = (
            packaged_release or load_packaged_authoring_release()
        )

    @property
    def theme_id(self) -> str:
        return self.packaged_release.theme_id

    @property
    def guide_id(self) -> str:
        return self.packaged_release.guide_id

    @transactional
    def bootstrap_packaged_release(self) -> dict[str, str]:
        seed = self.packaged_release
        if self.store.get_node("N4XRoot", {"id": "n4x"}) is None:
            raise PlatformReleaseConflictError(
                "platform authoring release requires N4XRoot id=n4x"
            )

        theme_hash = sha256_text(seed.css_text)
        guide_hash = sha256_text(seed.guide_content)
        theme_revision_id = f"{seed.theme_id}@{seed.release_version}"
        guide_revision_id = f"{seed.guide_id}@{seed.release_version}"

        theme_revision = self._existing_release_revision(
            collection=self.records.ui_theme_revisions,
            owner_field="theme_id",
            owner_id=seed.theme_id,
            release_version=seed.release_version,
            deterministic_id=theme_revision_id,
            expected_hash=theme_hash,
            content_field="css_text",
            kind="UI theme",
        )
        guide_revision = self._existing_release_revision(
            collection=self.records.authoring_guide_revisions,
            owner_field="guide_id",
            owner_id=seed.guide_id,
            release_version=seed.release_version,
            deterministic_id=guide_revision_id,
            expected_hash=guide_hash,
            content_field="content",
            kind="authoring guide",
        )
        if theme_revision is not None and (
            theme_revision.provenance != seed.theme_provenance
            or theme_revision.license != seed.theme_license
        ):
            raise PlatformReleaseConflictError(
                f"UI theme release {seed.release_version!r} metadata changed; "
                "publish a new release version"
            )
        if guide_revision is not None and (
            guide_revision.references != seed.references
            or guide_revision.provenance != seed.guide_provenance
            or guide_revision.license != seed.guide_license
        ):
            raise PlatformReleaseConflictError(
                "authoring guide release "
                f"{seed.release_version!r} metadata changed; publish a new "
                "release version"
            )

        theme = self.records.ui_themes.get(seed.theme_id)
        if theme is None:
            theme = UiTheme(id=seed.theme_id)
            self.records.ui_themes.save(theme)
        guide = self.records.authoring_guides.get(seed.guide_id)
        if guide is None:
            guide = AuthoringGuide(
                id=seed.guide_id,
                title=seed.guide_title,
            )
            self.records.authoring_guides.save(guide)

        self.store.create_edge(
            node_ref("N4XRoot", id="n4x"),
            "HAS_UI_THEME",
            node_ref("UiTheme", id=theme.id),
        )
        self.store.create_edge(
            node_ref("N4XRoot", id="n4x"),
            "HAS_AUTHORING_GUIDE",
            node_ref("AuthoringGuide", id=guide.id),
        )

        if theme_revision is None:
            theme_revision = UiThemeRevision(
                id=theme_revision_id,
                theme_id=seed.theme_id,
                release_version=seed.release_version,
                status="active",
                css_text=seed.css_text,
                content_hash=theme_hash,
                provenance=seed.theme_provenance,
                license=seed.theme_license,
            )
            self.records.ui_theme_revisions.save(theme_revision)
        if guide_revision is None:
            guide_revision = AuthoringGuideRevision(
                id=guide_revision_id,
                guide_id=seed.guide_id,
                release_version=seed.release_version,
                status="active",
                content=seed.guide_content,
                content_hash=guide_hash,
                references=seed.references,
                provenance=seed.guide_provenance,
                license=seed.guide_license,
            )
            self.records.authoring_guide_revisions.save(guide_revision)

        self.store.create_edge(
            node_ref("UiTheme", id=theme.id),
            "HAS_REVISION",
            node_ref("UiThemeRevision", id=theme_revision.id),
        )
        self.store.create_edge(
            node_ref("AuthoringGuide", id=guide.id),
            "HAS_REVISION",
            node_ref("AuthoringGuideRevision", id=guide_revision.id),
        )

        self._activate_theme(theme, theme_revision)
        self._activate_guide(guide, guide_revision)
        return {
            "release_version": seed.release_version,
            "theme_revision_id": theme_revision.id,
            "guide_revision_id": guide_revision.id,
            "theme_content_hash": theme_hash,
            "guide_content_hash": guide_hash,
        }

    def inspect_authoring_guide(
        self, guide_id: str | None = None
    ) -> dict[str, Any]:
        self.bootstrap_packaged_release()
        requested_id = self._canonical_guide_id(guide_id)
        with self.uow:
            guide = self.records.authoring_guides[requested_id]
            if guide.active_revision_id is None:
                raise PlatformReleaseConflictError(
                    f"authoring guide has no active revision: {requested_id}"
                )
            revision = self.records.authoring_guide_revisions[
                guide.active_revision_id
            ]
            return {
                **guide.model_dump(mode="json"),
                "release_version": revision.release_version,
                "version": revision.release_version,
                "content": revision.content,
                "content_hash": revision.content_hash,
                "references": revision.references,
                "provenance": revision.provenance,
                "license": revision.license,
                "active_revision": revision.model_dump(mode="json"),
            }

    def inspect_surface_theme(self) -> dict[str, Any]:
        revision = self.active_theme_revision()
        with self.uow:
            theme = self.records.ui_themes[revision.theme_id]
            return {
                **theme.model_dump(mode="json"),
                "release_version": revision.release_version,
                "version": revision.release_version,
                "css_text": revision.css_text,
                "content_hash": revision.content_hash,
                "provenance": revision.provenance,
                "license": revision.license,
                "active_revision": revision.model_dump(mode="json"),
            }

    def active_theme_revision(self) -> UiThemeRevision:
        self.bootstrap_packaged_release()
        with self.uow:
            theme = self.records.ui_themes[self.theme_id]
            if theme.active_revision_id is None:
                raise PlatformReleaseConflictError(
                    f"UI theme has no active revision: {theme.id}"
                )
            return self.records.ui_theme_revisions[theme.active_revision_id]

    def inspect_component_palette(self) -> dict[str, Any]:
        theme = self.inspect_surface_theme()
        return {
            "release_version": theme["release_version"],
            "theme_revision_id": theme["active_revision_id"],
            "theme_content_hash": theme["content_hash"],
            "ui_profile": "n4x-default",
            "ownership": "experience_owned",
            "theme_import": "import './n4x-theme.css';",
            "guidance": (
                "Create Experience-owned shadcn/ui components in Experience source. "
                "Declare Radix, Lucide, chart, calendar, table, editor, and other "
                "component dependencies on the Experience revision."
            ),
            "design_guidance": (
                "Inspect the active authoring guide and UI theme before writing "
                "Surface source. Use inset Sidebar chrome for standalone apps, "
                "and expose only graph-backed data or active actions."
            ),
            "components": [
                {
                    "name": name,
                    "ownership": "experience_owned",
                    "recommended_import": (
                        f"import {{ {name} }} from './components/ui';"
                    ),
                }
                for name in COMPONENT_PALETTE
            ],
        }

    def inspect_experience_design_context(
        self,
        experience_revision_id: str,
        *,
        include_content: bool = True,
        last_seen_hash: str | None = None,
    ) -> dict[str, Any]:
        """Return advisory graph-owned UI context for one Experience revision."""
        self.bootstrap_packaged_release()
        with self.uow:
            experience_revision = self.records.experience_revisions[
                experience_revision_id
            ]
            guide = self.records.authoring_guides[self.guide_id]
            theme = self.records.ui_themes[self.theme_id]
            if guide.active_revision_id is None:
                raise PlatformReleaseConflictError(
                    f"authoring guide has no active revision: {guide.id}"
                )
            if theme.active_revision_id is None:
                raise PlatformReleaseConflictError(
                    f"UI theme has no active revision: {theme.id}"
                )
            guide_revision = self.records.authoring_guide_revisions[
                guide.active_revision_id
            ]
            theme_revision = self.records.ui_theme_revisions[
                theme.active_revision_id
            ]
            content_hash = (
                f"{guide_revision.content_hash}:{theme_revision.content_hash}:"
                f"{experience_revision.ui_profile}"
            )
            revision_payload = {
                "id": experience_revision.id,
                "experience_id": experience_revision.experience_id,
                "status": experience_revision.status,
                "ui_profile": experience_revision.ui_profile,
            }
            if last_seen_hash is not None and last_seen_hash == content_hash:
                return {
                    "design_context_version": EXPERIENCE_DESIGN_CONTEXT_VERSION,
                    "advisory_only": True,
                    "unchanged": True,
                    "content_hash": content_hash,
                    "revision": revision_payload,
                    "authoring_guide": {
                        "content_hash": guide_revision.content_hash,
                    },
                    "surface_theme": {
                        "content_hash": theme_revision.content_hash,
                    },
                }

            guide_context: dict[str, Any] = {
                "id": guide.id,
                "active_revision_id": guide_revision.id,
                "release_version": guide_revision.release_version,
                "content_hash": guide_revision.content_hash,
                "references": guide_revision.references,
            }
            theme_context: dict[str, Any] = {
                "id": theme.id,
                "active_revision_id": theme_revision.id,
                "release_version": theme_revision.release_version,
                "content_hash": theme_revision.content_hash,
            }
            if include_content:
                guide_context["content"] = guide_revision.content
                theme_context["css_text"] = theme_revision.css_text

            return {
                "design_context_version": EXPERIENCE_DESIGN_CONTEXT_VERSION,
                "advisory_only": True,
                "content_hash": content_hash,
                "revision": revision_payload,
                "profile_guidance": UI_PROFILE_GUIDANCE[
                    experience_revision.ui_profile
                ],
                "platform_release": {
                    "release_version": guide_revision.release_version,
                    "authoring_guide": guide_context,
                    "surface_theme": theme_context,
                    "component_palette": {
                        "ui_profile": "n4x-default",
                        "ownership": "experience_owned",
                        "theme_import": "import './n4x-theme.css';",
                        "components": list(COMPONENT_PALETTE),
                    },
                    "experience_bridge": {
                        "version": EXPERIENCE_BRIDGE_VERSION,
                        "hosts": EXPERIENCE_BRIDGE_SCHEMA["hosts"],
                        "authoring": EXPERIENCE_BRIDGE_SCHEMA["authoring"],
                    },
                },
                "workflow": list(EXPERIENCE_AUTHORING_WORKFLOW),
                "visual_review_checklist": list(
                    EXPERIENCE_VISUAL_REVIEW_CHECKLIST
                ),
                "full_inspection_tools": {
                    "revision": "inspect_experience_revision",
                    "authoring_guide": "inspect_authoring_guide",
                    "surface_theme": "inspect_surface_theme",
                    "component_palette": "inspect_component_palette",
                    "experience_bridge": "inspect_experience_bridge",
                    "validation": "validate_experience_revision",
                },
            }

    def inspect_experience_bridge(self) -> dict[str, Any]:
        return {
            **EXPERIENCE_BRIDGE_SCHEMA,
            "action_context_version": ACTION_CONTEXT_VERSION,
            "action_supervisor_version": ACTION_SUPERVISOR_VERSION,
            "action_supervisor": ACTION_SUPERVISOR_SCHEMA,
            "execution_context_version": EXECUTION_CONTEXT_VERSION,
            "execution_context": EXECUTION_CONTEXT_SCHEMA,
            "subprocess_protocol_version": SUBPROCESS_PROTOCOL_VERSION,
            "mcp_authoring_version": MCP_AUTHORING_VERSION,
            "mcp_authoring": MCP_AUTHORING_SCHEMA,
            "callback_contract_version": CALLBACK_CONTRACT_VERSION,
            "callback": CALLBACK_CONTRACT_SCHEMA,
            "file_delivery_contract_version": FILE_DELIVERY_CONTRACT_VERSION,
            "file_delivery": FILE_DELIVERY_CONTRACT_SCHEMA,
            "graph_metamodel_version": GRAPH_METAMODEL_VERSION,
            "graph_metamodel": GRAPH_METAMODEL_SCHEMA,
            "theme": self.inspect_surface_theme(),
        }

    def _existing_release_revision(
        self,
        *,
        collection,
        owner_field: str,
        owner_id: str,
        release_version: str,
        deterministic_id: str,
        expected_hash: str,
        content_field: str,
        kind: str,
    ):
        matching = [
            revision
            for revision in collection.values()
            if getattr(revision, owner_field) == owner_id
            and revision.release_version == release_version
        ]
        if len(matching) > 1 or (
            matching and matching[0].id != deterministic_id
        ):
            raise PlatformReleaseConflictError(
                f"{kind} release {release_version!r} does not use deterministic "
                f"revision id {deterministic_id!r}"
            )
        revision = matching[0] if matching else collection.get(deterministic_id)
        if revision is not None and (
            getattr(revision, owner_field) != owner_id
            or revision.release_version != release_version
        ):
            raise PlatformReleaseConflictError(
                f"{kind} deterministic revision id is already in use: "
                f"{deterministic_id}"
            )
        if revision is not None and (
            revision.content_hash != expected_hash
            or sha256_text(getattr(revision, content_field))
            != expected_hash
        ):
            raise PlatformReleaseConflictError(
                f"{kind} release {release_version!r} content hash changed; "
                "publish a new release version"
            )
        return revision

    def _activate_theme(
        self, theme: UiTheme, revision: UiThemeRevision
    ) -> None:
        if theme.active_revision_id not in {None, revision.id}:
            previous = self.records.ui_theme_revisions.get(
                theme.active_revision_id
            )
            if previous is not None and previous.status != "superseded":
                self.records.ui_theme_revisions.save(
                    previous.model_copy(update={"status": "superseded"})
                )
        if revision.status != "active":
            revision = revision.model_copy(update={"status": "active"})
            self.records.ui_theme_revisions.save(revision)
        if theme.active_revision_id != revision.id:
            theme = theme.model_copy(
                update={
                    "active_revision_id": revision.id,
                    "updated_at": now_utc(),
                }
            )
            self.records.ui_themes.save(theme)
        self.store.replace_single_edge(
            node_ref("UiTheme", id=theme.id),
            "ACTIVE_REVISION",
            node_ref("UiThemeRevision", id=revision.id),
        )

    def _activate_guide(
        self,
        guide: AuthoringGuide,
        revision: AuthoringGuideRevision,
    ) -> None:
        if guide.active_revision_id not in {None, revision.id}:
            previous = self.records.authoring_guide_revisions.get(
                guide.active_revision_id
            )
            if previous is not None and previous.status != "superseded":
                self.records.authoring_guide_revisions.save(
                    previous.model_copy(update={"status": "superseded"})
                )
        if revision.status != "active":
            revision = revision.model_copy(update={"status": "active"})
            self.records.authoring_guide_revisions.save(revision)
        if guide.active_revision_id != revision.id:
            guide = guide.model_copy(
                update={
                    "active_revision_id": revision.id,
                    "updated_at": now_utc(),
                }
            )
            self.records.authoring_guides.save(guide)
        self.store.replace_single_edge(
            node_ref("AuthoringGuide", id=guide.id),
            "ACTIVE_REVISION",
            node_ref("AuthoringGuideRevision", id=revision.id),
        )

    def _canonical_guide_id(self, guide_id: str | None) -> str:
        if guide_id in {None, "n4x.v1", self.guide_id}:
            return self.guide_id
        raise KeyError(guide_id)
