"""System-owned MCP client-usage index and playbooks.

Why this cannot live in a trusted Application: initialize instructions and the
System tool catalog are MCP hosting. Remote clients pointed only at `/mcp` never
see a git checkout skill.
"""

from __future__ import annotations

from importlib.resources import files
from typing import Any, Literal

from n4x.kernel.errors import ValidationFailure

ClientGuideTopic = Literal[
    "orientation",
    "application",
    "experience",
    "mcp_app",
    "operate",
]

CLIENT_GUIDE_TOPICS: tuple[ClientGuideTopic, ...] = (
    "orientation",
    "application",
    "experience",
    "mcp_app",
    "operate",
)

_TOPIC_META: dict[ClientGuideTopic, dict[str, str]] = {
    "orientation": {
        "description": "N4X Application versus Experience split and inspect-first rules.",
        "when_to_use": (
            "Call first on a new session, or when unsure which catalog tools apply."
        ),
    },
    "application": {
        "description": "Backend Application revision, schema, actions, tests, and activation.",
        "when_to_use": (
            "Creating or editing Applications, object types, actions, or tests."
        ),
    },
    "experience": {
        "description": "Experience UI authoring that defers to live design context.",
        "when_to_use": (
            "Creating or editing Experiences, Surfaces, or frontend SourceFiles."
        ),
    },
    "mcp_app": {
        "description": "MCP App Surfaces versus browser Surfaces and the bridge contract.",
        "when_to_use": (
            "Declaring, building, or activating surface_type=mcp_app."
        ),
    },
    "operate": {
        "description": "Development deployments, jobs, callbacks, secrets, and packages.",
        "when_to_use": (
            "Running isolated drafts, scheduling, secrets, or Package import/export."
        ),
    },
}

INSTRUCTIONS = (
    "Applications are backend-only. Author UI in an ExperienceRevision. Before "
    "Application or Experience authoring, call inspect_client_guide; omit topic "
    "for the catalog. For Experience Surface source, also call "
    "inspect_experience_design_context and follow its active graph-owned guide, "
    "theme, profile precedence, component workflow, and visual-review checklist. "
    "The guidance is advisory: use validate_application_revision and "
    "validate_experience_revision separately for structural validation."
)

APPLICATION_AUTHORING_NEXT_STEP = {
    "tool": "inspect_client_guide",
    "arguments": {"topic": "application"},
    "reason": (
        "Load the Application authoring workflow before declaring schema, "
        "actions, or activating the draft."
    ),
}


def inspect_client_guide(topic: ClientGuideTopic | None = None) -> dict[str, Any]:
    """Return the client-guide catalog or one playbook body."""
    if topic is None:
        return {
            "topics": [
                {"topic": name, **_TOPIC_META[name]}
                for name in CLIENT_GUIDE_TOPICS
            ]
        }
    if topic not in _TOPIC_META:
        raise ValidationFailure(f"unknown client guide topic {topic!r}")
    content = (
        files("n4x.mcp.client_guides")
        .joinpath(f"{topic}.md")
        .read_text(encoding="utf-8")
    )
    return {"topic": topic, "content": content, **_TOPIC_META[topic]}
