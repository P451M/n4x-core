from __future__ import annotations

from pathlib import Path

from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.models import (
    Application,
    BuildArtifact,
    Experience,
    ExperienceRevision,
    ExperienceSurface,
    RuntimeDependency,
    CallbackRoute,
    CredentialRecord,
    CypherAuditRecord,
    Invocation,
    JobAttempt,
    SecretReference,
)



class QueryService:
    def __init__(
        self,
        uow: GraphUnitOfWork,
    ) -> None:
        self.uow = uow
        self.records = uow.records

    def refresh(self) -> None:
        # Repository reads are live; there is no process-local state to refresh.
        return None

    def application(self, application_id: str) -> Application | None:
        return self.records.applications.get(application_id)

    def experience(self, experience_id: str) -> Experience | None:
        return self.records.experiences.get(experience_id)

    def experiences(self) -> list[Experience]:
        return sorted(
            (
                item
                for item in self.records.experiences.values()
                if item.status != "disabled"
            ),
            key=lambda item: item.id,
        )

    def experience_revision(self, revision_id: str) -> ExperienceRevision:
        return self.records.experience_revisions[revision_id]

    def experience_revisions(
        self, experience_id: str | None = None
    ) -> list[ExperienceRevision]:
        revisions = self.records.experience_revisions.values()
        if experience_id is not None:
            revisions = [
                item for item in revisions if item.experience_id == experience_id
            ]
        return sorted(revisions, key=lambda item: (item.experience_id, item.created_at))

    def experience_dependencies(
        self, experience_revision_id: str
    ) -> list[RuntimeDependency]:
        return sorted(
            (
                item
                for item in self.records.runtime_dependencies.values()
                if item.owner_kind == "ExperienceRevision"
                and item.owner_id == experience_revision_id
            ),
            key=lambda item: (item.package, item.id),
        )

    def experience_build_artifacts(
        self, experience_revision_id: str
    ) -> list[BuildArtifact]:
        return sorted(
            (
                item
                for item in self.records.build_artifacts.values()
                if item.owner_kind == "ExperienceRevision"
                and item.owner_id == experience_revision_id
            ),
            key=lambda item: item.created_at,
        )

    def experience_surfaces(
        self, experience_revision_id: str
    ) -> list[ExperienceSurface]:
        return sorted(
            (
                item
                for item in self.records.experience_surfaces.values()
                if item.experience_revision_id == experience_revision_id
            ),
            key=lambda item: item.surface_id,
        )

    def surface_artifact(
        self,
        experience_revision_id: str,
        surface_id: str,
        input_hash: str,
    ) -> BuildArtifact | None:
        artifacts = [
            artifact
            for artifact in self.records.build_artifacts.values()
            if artifact.owner_kind == "ExperienceRevision"
            and artifact.owner_id == experience_revision_id
            and artifact.surface_id == surface_id
            and artifact.input_hash == input_hash
        ]
        existing = [
            artifact for artifact in artifacts if Path(artifact.path).exists()
        ]
        selected = existing or artifacts
        return selected[-1] if selected else None

    def build_artifacts(self) -> list[BuildArtifact]:
        return self.records.build_artifacts.values()

    def python_environments(self):
        return self.records.python_environments.values()

    def build_invocations(self):
        return self.records.build_invocations.values()

    def invocations(self) -> list[Invocation]:
        return self.records.invocations.values()

    def secret_references(self) -> list[SecretReference]:
        return self.records.secret_references.values()

    def credential_records(self) -> list[CredentialRecord]:
        return self.records.credential_records.values()

    def cypher_audits(
        self, invocation_id: str | None = None
    ) -> list[CypherAuditRecord]:
        audits = self.records.cypher_audits.values()
        if invocation_id is not None:
            audits = [
                audit for audit in audits if audit.invocation_id == invocation_id
            ]
        return sorted(audits, key=lambda item: item.created_at)

    def job_attempts(self, job_id: str | None = None) -> list[JobAttempt]:
        attempts = self.records.job_attempts.values()
        if job_id is not None:
            attempts = [item for item in attempts if item.job_id == job_id]
        return sorted(attempts, key=lambda item: (item.job_id, item.attempt))

    def callback_routes(
        self, application_id: str | None = None
    ) -> list[CallbackRoute]:
        routes = self.records.callback_routes.values()
        if application_id is not None:
            routes = [
                route
                for route in routes
                if route.application_id == application_id
            ]
        return sorted(routes, key=lambda route: route.created_at)
