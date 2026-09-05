"""Development deployments owned by the System."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Literal, Protocol

from n4x.graph.bindings import RevisionBindings
from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import FileDeliveryError, ValidationFailure
from n4x.kernel.models import (
    ActionRevision,
    ApplicationAccessDeclaration,
    ApplicationObject,
    ApplicationRelation,
    DataSpaceCloneSpec,
    DevelopmentDeployment,
    ExecutionContext,
    ExperienceRevision,
    Invocation,
    now_utc,
)
from n4x.graph.service_base import transactional
from n4x.system.data_spaces import DataSpaceService
from n4x.system.file_delivery import (
    DeliveredFile,
    FileDeliveryService,
)


class DevelopmentProcessPort(Protocol):
    def evict_data_space(self, data_space_id: str) -> None: ...


class DevelopmentActionSupervisor(Protocol):
    def run(
        self,
        action_revision: ActionRevision,
        input_value: dict[str, Any],
        invocation_kind: str = "draft",
        *,
        execution_context: ExecutionContext | None = None,
        **kwargs: Any,
    ) -> Invocation: ...

    def submit(
        self,
        action_revision: ActionRevision,
        input_value: dict[str, Any],
        invocation_kind: str = "draft",
        *,
        execution_context: ExecutionContext | None = None,
        **kwargs: Any,
    ) -> Invocation: ...

    def cancel_deployment(
        self, deployment_id: str
    ) -> list[Invocation]: ...


class DevelopmentDeploymentService:
    """Owns single-stack candidate bindings and isolated execution contexts."""

    def __init__(
        self,
        uow: GraphUnitOfWork,
        data_spaces: DataSpaceService,
        supervisor: DevelopmentActionSupervisor,
        file_delivery: FileDeliveryService,
        process_pool: DevelopmentProcessPort | None = None,
    ) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.data_spaces = data_spaces
        self.supervisor = supervisor
        self.file_delivery = file_delivery
        self.process_pool = process_pool
        self.bindings = RevisionBindings(uow)

    @transactional
    def create(
        self,
        experience_revision_id: str,
        application_revision_ids: dict[str, str],
        *,
        ttl_seconds: int = 3600,
        initialization: Literal["empty", "clone"] = "empty",
        clone_specs: dict[
            str, DataSpaceCloneSpec | dict[str, Any]
        ] | None = None,
    ) -> DevelopmentDeployment:
        if ttl_seconds < 1:
            raise ValidationFailure("ttl_seconds must be positive")
        if initialization not in {"empty", "clone"}:
            raise ValidationFailure(
                f"unsupported DataSpace initialization: {initialization}"
            )
        experience_revision = self.records.experience_revisions[
            experience_revision_id
        ]
        if experience_revision.status != "draft":
            raise ValidationFailure(
                "development deployment requires a draft ExperienceRevision"
            )
        declared_applications = {
            declaration.application_id
            for declaration in experience_revision.application_access
        }
        if set(application_revision_ids) != declared_applications:
            raise ValidationFailure(
                "candidate Applications must exactly match Experience access"
            )

        application_revisions = {}
        candidate_hashes = {
            f"experience:{experience_revision.id}": self.bindings.tree(
                experience_revision.id
            ).tree_hash
        }
        for application_id, revision_id in application_revision_ids.items():
            revision = self.records.revisions[revision_id]
            if revision.application_id != application_id:
                raise ValidationFailure(
                    f"ApplicationRevision owner mismatch: {revision_id}"
                )
            if revision.status not in {"draft", "active"}:
                raise ValidationFailure(
                    f"ApplicationRevision is not deployable: {revision_id}"
                )
            application_revisions[application_id] = revision
            candidate_hashes[f"application:{application_id}"] = (
                self.bindings.tree(revision.id).tree_hash
            )

        deployment_id = str(uuid.uuid4())
        expires_at = now_utc() + timedelta(seconds=ttl_seconds)
        data_space_ids = {}
        for application_id in sorted(application_revisions):
            data_space = self.data_spaces.create_development(
                application_id,
                data_space_id=deployment_id,
                expires_at=expires_at,
            )
            data_space_ids[application_id] = data_space.id
        if initialization == "clone":
            specs = clone_specs or {}
            unknown_specs = set(specs) - set(application_revisions)
            if unknown_specs:
                raise ValidationFailure(
                    "clone_specs contains undeclared Applications: "
                    + ", ".join(sorted(unknown_specs))
                )
            for application_id in sorted(application_revisions):
                self.data_spaces.clone_from_production(
                    application_id,
                    data_space_ids[application_id],
                    specs.get(application_id),
                )

        secret_reference_ids = (
            self._clone_secret_reference_ids(experience_revision)
            if initialization == "clone"
            else []
        )
        deployment = DevelopmentDeployment(
            id=deployment_id,
            experience_revision_id=experience_revision_id,
            application_revision_ids=dict(application_revision_ids),
            data_space_ids=data_space_ids,
            candidate_hashes=candidate_hashes,
            secret_reference_ids=secret_reference_ids,
            expires_at=expires_at,
        )
        self.records.development_deployments.save(deployment)
        deployment_ref = node_ref(
            "DevelopmentDeployment", id=deployment.id
        )
        self.store.create_edge(
            node_ref("N4XRoot", id="n4x"),
            "HAS_DEVELOPMENT_DEPLOYMENT",
            deployment_ref,
        )
        self.store.create_edge(
            deployment_ref,
            "PINS_EXPERIENCE_REVISION",
            node_ref("ExperienceRevision", id=experience_revision_id),
        )
        for application_id, revision in application_revisions.items():
            self.store.create_edge(
                deployment_ref,
                "PINS_APPLICATION_REVISION",
                node_ref("ApplicationRevision", id=revision.id),
            )
            self.store.create_edge(
                deployment_ref,
                "BINDS_DATA_SPACE",
                node_ref(
                    "DataSpace",
                    application_id=application_id,
                    id=data_space_ids[application_id],
                ),
            )
        return deployment

    def get(self, deployment_id: str) -> DevelopmentDeployment:
        deployment = self.records.development_deployments.get(deployment_id)
        if deployment is None:
            raise KeyError(deployment_id)
        return deployment

    def execution_context(
        self,
        deployment_id: str,
        application_id: str,
        *,
        correlation_id: str | None = None,
    ) -> ExecutionContext:
        deployment = self.require_available(deployment_id)
        revision_id = deployment.application_revision_ids.get(application_id)
        data_space_id = deployment.data_space_ids.get(application_id)
        if revision_id is None or data_space_id is None:
            raise ValidationFailure(
                f"Application is not bound to deployment: {application_id}"
            )
        return ExecutionContext(
            mode="development",
            deployment_id=deployment.id,
            correlation_id=correlation_id or str(uuid.uuid4()),
            experience_revision_id=deployment.experience_revision_id,
            application_revision_id=revision_id,
            application_id=application_id,
            data_space_id=data_space_id,
        )

    def require_available(
        self, deployment_id: str
    ) -> DevelopmentDeployment:
        deployment = self.get(deployment_id)
        if deployment.status != "active" or deployment.expires_at <= now_utc():
            raise ValidationFailure(
                f"development deployment is expired: {deployment_id}"
            )
        self._require_unchanged(deployment)
        self._require_current_pins(deployment)
        return deployment

    def experience_revision(
        self,
        deployment_id: str,
        experience_id: str,
    ) -> ExperienceRevision:
        deployment = self.require_available(deployment_id)
        revision = self.records.experience_revisions[
            deployment.experience_revision_id
        ]
        if revision.experience_id != experience_id:
            raise ValidationFailure(
                "Experience is not bound to development deployment"
            )
        return revision

    def run_action(
        self,
        deployment_id: str,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ) -> Invocation:
        context = self.execution_context(deployment_id, application_id)
        revision = self._resolve_action(context, action_id)
        invocation = self.supervisor.run(
            revision,
            input_value,
            invocation_kind="draft",
            execution_context=context,
        )
        try:
            output = self.file_delivery.prepare_output(
                invocation.output,
                application_id=application_id,
                experience_id=self.records.experience_revisions[
                    context.experience_revision_id or ""
                ].experience_id,
                experience_revision_id=context.experience_revision_id or "",
                data_space_id=context.data_space_id,
                deployment_id=deployment_id,
            )
        except FileDeliveryError:
            output = self.file_delivery.strip_delivery_fields(
                invocation.output
            )
        if output != invocation.output:
            invocation = invocation.model_copy(update={"output": output})
            with self.uow:
                self.records.invocations.save(invocation)
        return invocation

    def list_objects(
        self,
        deployment_id: str,
        application_id: str,
        object_type_id: str | None = None,
    ) -> list[ApplicationObject]:
        context = self.execution_context(deployment_id, application_id)
        access = self._access(deployment_id, application_id)
        if (
            object_type_id is not None
            and access.object_type_ids is not None
            and object_type_id not in access.object_type_ids
        ):
            raise ValidationFailure(
                f"ObjectType is not allowed by Experience: {object_type_id}"
            )
        objects = self.uow.objects.list(
            application_id,
            object_type_id,
            data_space_id=context.data_space_id,
        )
        if access.object_type_ids is None:
            return objects
        allowed = set(access.object_type_ids)
        return [item for item in objects if item.object_type_id in allowed]

    def list_relations(
        self,
        deployment_id: str,
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
    ) -> list[ApplicationRelation]:
        context = self.execution_context(deployment_id, application_id)
        access = self._access(deployment_id, application_id)
        if (
            relation_type_id is not None
            and access.relation_type_ids is not None
            and relation_type_id not in access.relation_type_ids
        ):
            raise ValidationFailure(
                "RelationType is not allowed by Experience: "
                f"{relation_type_id}"
            )
        relations = self.uow.relations.list(
            application_id,
            relation_type_id,
            from_object_id,
            to_object_id,
            data_space_id=context.data_space_id,
        )
        if access.relation_type_ids is None:
            return relations
        allowed = set(access.relation_type_ids)
        return [
            item for item in relations if item.relation_type_id in allowed
        ]

    def submit_action(
        self,
        deployment_id: str,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ) -> Invocation:
        context = self.execution_context(deployment_id, application_id)
        revision = self._resolve_action(context, action_id)
        return self.supervisor.submit(
            revision,
            input_value,
            invocation_kind="draft",
            execution_context=context,
        )

    def deliver_file(
        self,
        deployment_id: str,
        application_id: str,
        token: str,
    ) -> DeliveredFile:
        context = self.execution_context(deployment_id, application_id)
        experience_revision = self.records.experience_revisions[
            context.experience_revision_id or ""
        ]
        return self.file_delivery.resolve(
            token,
            application_id=application_id,
            experience_id=experience_revision.experience_id,
            experience_revision_id=experience_revision.id,
            data_space_id=context.data_space_id,
            deployment_id=deployment_id,
        )

    def _resolve_action(
        self, context: ExecutionContext, action_id: str
    ) -> ActionRevision:
        access = self._access(
            context.deployment_id or "", context.application_id
        )
        if (
            access.action_ids is not None
            and action_id not in access.action_ids
        ):
            raise ValidationFailure(
                f"Action is not allowed by Experience: {action_id}"
            )
        with self.uow:
            action = self.records.actions.get(action_id)
            if (
                action is None
                or action.application_id != context.application_id
            ):
                raise ValidationFailure(
                    f"Action does not belong to Application: {action_id}"
                )
            try:
                revision = self.bindings.action_revision(
                    context.application_revision_id, action_id
                )
            except KeyError as exc:
                raise ValidationFailure(
                    f"Action has no candidate revision: {action_id}"
                ) from exc
            if revision.kind == "migration":
                raise ValidationFailure(
                    "migration revisions run only through activation"
                )
            return revision

    def _clone_secret_reference_ids(
        self, experience_revision: ExperienceRevision
    ) -> list[str]:
        bound: list[str] = []
        seen: set[str] = set()
        for declaration in experience_revision.application_access:
            for reference_id in declaration.secret_reference_ids or []:
                if not reference_id or reference_id in seen:
                    continue
                reference = self.records.secret_references.get(reference_id)
                if reference is None:
                    raise ValidationFailure(
                        f"unknown secret reference: {reference_id}"
                    )
                if reference.application_id != declaration.application_id:
                    raise ValidationFailure(
                        "secret reference owner mismatch: " + reference_id
                    )
                seen.add(reference_id)
                bound.append(reference_id)
        return bound

    def bound_secret_reference_ids(
        self, deployment_id: str, application_id: str
    ) -> list[str]:
        deployment = self.require_available(deployment_id)
        access = self._access(deployment_id, application_id)
        declared = access.secret_reference_ids or []
        allowed = set(deployment.secret_reference_ids)
        return [reference_id for reference_id in declared if reference_id in allowed]

    def _access(
        self, deployment_id: str, application_id: str
    ) -> ApplicationAccessDeclaration:
        deployment = self.require_available(deployment_id)
        revision = self.records.experience_revisions[
            deployment.experience_revision_id
        ]
        access = next(
            (
                declaration
                for declaration in revision.application_access
                if declaration.application_id == application_id
            ),
            None,
        )
        if access is None:
            raise ValidationFailure(
                f"Application is not declared by Experience: {application_id}"
            )
        return access

    def expire(self, deployment_id: str) -> DevelopmentDeployment:
        with self.uow:
            deployment = self.get(deployment_id)
            if deployment.status == "expired":
                return deployment
            expired = deployment.model_copy(update={"status": "expired"})
            self.records.development_deployments.save(expired)
        self.supervisor.cancel_deployment(deployment_id)
        for application_id, data_space_id in (
            deployment.data_space_ids.items()
        ):
            if self.process_pool is not None:
                self.process_pool.evict_data_space(data_space_id)
            self.data_spaces.purge_development(
                application_id, data_space_id
            )
        return expired

    def _require_unchanged(
        self, deployment: DevelopmentDeployment
    ) -> None:
        experience = self.records.experience_revisions[
            deployment.experience_revision_id
        ]
        current = {
            f"experience:{experience.id}": self.bindings.tree(experience.id).tree_hash
        }
        for application_id, revision_id in (
            deployment.application_revision_ids.items()
        ):
            revision = self.records.revisions[revision_id]
            current[f"application:{application_id}"] = self.bindings.tree(
                revision.id
            ).tree_hash
        if current != deployment.candidate_hashes:
            raise ValidationFailure(
                "development deployment candidate changed; redeploy required"
            )

    def _require_current_pins(self, deployment: DevelopmentDeployment) -> None:
        experience = self.records.experience_revisions[
            deployment.experience_revision_id
        ]
        if experience.status not in {"draft", "active"}:
            raise ValidationFailure(
                "development deployment candidate superseded; redeploy required"
            )
        for revision_id in deployment.application_revision_ids.values():
            revision = self.records.revisions[revision_id]
            if revision.status not in {"draft", "active"}:
                raise ValidationFailure(
                    "development deployment candidate superseded; redeploy required"
                )
