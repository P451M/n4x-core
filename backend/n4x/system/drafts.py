"""Draft-only mutation policy owned by the System."""

from __future__ import annotations

from n4x.kernel.errors import ImmutableRevisionError
from n4x.kernel.models import ApplicationRevision, ExperienceRevision


class Drafts:
    def __init__(self, records) -> None:
        self.records = records

    def require_application(self, revision_id: str) -> ApplicationRevision:
        revision = self.records.revisions[revision_id]
        if revision.status != "draft":
            raise ImmutableRevisionError(
                f"operation requires a draft revision, got {revision.status}"
            )
        return revision

    def require_experience(self, revision_id: str) -> ExperienceRevision:
        revision = self.records.experience_revisions[revision_id]
        if revision.status != "draft":
            raise ValueError(f"ExperienceRevision {revision_id} is not draft")
        return revision
