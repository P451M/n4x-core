"""Experience authorization owned by the System."""

from __future__ import annotations

from typing import Any, Protocol

from n4x.graph.bindings import RevisionBindings
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ExperienceAccessError, FileDeliveryError
from n4x.kernel.models import (
    ApplicationAccessDeclaration,
    ApplicationObject,
    ApplicationRelation,
    ExperienceRevision,
    Invocation,
)
from n4x.system.file_delivery import DeliveredFile, FileDeliveryService


class ObjectReader(Protocol):
    def list(
        self, application_id: str, object_type_id: str | None = None
    ) -> list[ApplicationObject]: ...


class RelationReader(Protocol):
    def list(
        self,
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
    ) -> list[ApplicationRelation]: ...


class ActionInvoker(Protocol):
    def run_active_action(
        self,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ) -> Invocation: ...



class SecretManager(Protocol):
    def secret_status(
        self, application_id: str, reference_id: str
    ) -> dict[str, object]: ...

    def set_secret_reference(
        self, application_id: str, reference_id: str, value: str
    ) -> dict[str, object]: ...

    def delete_secret_reference_value(
        self, application_id: str, reference_id: str
    ) -> dict[str, object]: ...


class ExperienceAccessService:
    """Authorizes the Experience bridge before delegating application work."""

    def __init__(
        self,
        uow: GraphUnitOfWork,
        objects: ObjectReader,
        relations: RelationReader,
        invocations: ActionInvoker,
        secrets: SecretManager,
        file_delivery: FileDeliveryService,
    ) -> None:
        self.uow = uow
        self.records = uow.records
        self.bindings = RevisionBindings(uow)
        self.objects = objects
        self.relations = relations
        self.invocations = invocations
        self.secrets = secrets
        self.file_delivery = file_delivery

    def list_objects(
        self,
        experience_id: str,
        application_id: str,
        object_type_id: str | None = None,
    ) -> list[ApplicationObject]:
        _, access = self._application_access(experience_id, application_id)
        if object_type_id is not None:
            self._require_definition(
                application_id,
                object_type_id,
                access.object_type_ids,
                self.records.object_types,
                self.records.object_type_revisions,
                "object_type",
            )
        objects = self.objects.list(application_id, object_type_id)
        if access.object_type_ids is None:
            return objects
        allowed = set(access.object_type_ids)
        return [item for item in objects if item.object_type_id in allowed]

    def list_relations(
        self,
        experience_id: str,
        application_id: str,
        *,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
    ) -> list[ApplicationRelation]:
        _, access = self._application_access(experience_id, application_id)
        if relation_type_id is not None:
            self._require_definition(
                application_id,
                relation_type_id,
                access.relation_type_ids,
                self.records.relation_types,
                self.records.relation_type_revisions,
                "relation_type",
            )
        for label, object_id in (
            ("from_object_id", from_object_id),
            ("to_object_id", to_object_id),
        ):
            if object_id is None:
                continue
            obj = self.uow.objects.get(application_id, object_id)
            if obj is None or obj.application_id != application_id:
                self._fail(
                    "invalid_filter",
                    f"{label} is not an object in the declared application",
                    400,
                )
        relations = self.relations.list(
            application_id,
            relation_type_id,
            from_object_id,
            to_object_id,
        )
        if access.relation_type_ids is None:
            return relations
        allowed = set(access.relation_type_ids)
        return [item for item in relations if item.relation_type_id in allowed]

    def invoke_action(
        self,
        experience_id: str,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ) -> Invocation:
        revision, access = self._application_access(experience_id, application_id)
        self._require_definition(
            application_id,
            action_id,
            access.action_ids,
            self.records.actions,
            self.records.action_revisions,
            "action",
        )
        invocation = self.invocations.run_active_action(
            application_id, action_id, input_value
        )
        delivery_error: FileDeliveryError | None = None
        try:
            output = self.file_delivery.prepare_output(
                invocation.output,
                application_id=application_id,
                experience_id=experience_id,
                experience_revision_id=revision.id,
            )
        except FileDeliveryError as exc:
            output = self.file_delivery.strip_delivery_fields(invocation.output)
            delivery_error = exc
        # Annotation is a separate, auditable graph write. The action keeps its
        # existing transaction and operation semantics.
        with self.uow:
            annotated = invocation.model_copy(
                update={
                    "output": output,
                    "metadata": {
                        **invocation.metadata,
                        "bridge": {
                            "experience_id": experience_id,
                            "experience_revision_id": revision.id,
                            "application_id": application_id,
                            "action_id": action_id,
                        },
                    }
                }
            )
            self.records.invocations.save(annotated)
        if delivery_error is not None:
            raise ExperienceAccessError(
                delivery_error.code,
                str(delivery_error),
                delivery_error.status_code,
            ) from delivery_error
        return annotated

    def deliver_file(
        self,
        experience_id: str,
        application_id: str,
        token: str,
    ) -> DeliveredFile:
        revision, _ = self._application_access(experience_id, application_id)
        try:
            return self.file_delivery.resolve(
                token,
                application_id=application_id,
                experience_id=experience_id,
                experience_revision_id=revision.id,
            )
        except FileDeliveryError as exc:
            raise ExperienceAccessError(
                exc.code, str(exc), exc.status_code
            ) from exc

    def secret_status(
        self,
        experience_id: str,
        application_id: str,
        secret_reference_id: str,
    ) -> dict[str, object]:
        self._require_secret_access(experience_id, application_id, secret_reference_id)
        return self.secrets.secret_status(application_id, secret_reference_id)

    def list_secrets(
        self, experience_id: str, application_id: str
    ) -> list[dict[str, object]]:
        _, access = self._application_access(experience_id, application_id)
        result: list[dict[str, object]] = []
        for reference_id in access.secret_reference_ids or []:
            reference = self.records.secret_references.get(reference_id)
            if reference is None or reference.application_id != application_id:
                self._fail(
                    "invalid_access_declaration",
                    "declared secret reference is unavailable",
                    500,
                )
            status = self.secrets.secret_status(application_id, reference_id)
            result.append(
                {
                    **status,
                    "uri": reference.uri,
                    "name": reference.name,
                    "description": reference.description,
                }
            )
        return sorted(result, key=lambda item: str(item["uri"]))

    def set_secret_value(
        self,
        experience_id: str,
        application_id: str,
        secret_reference_id: str,
        value: str,
    ) -> dict[str, object]:
        self._require_secret_access(experience_id, application_id, secret_reference_id)
        return self.secrets.set_secret_reference(
            application_id, secret_reference_id, value
        )

    def delete_secret_value(
        self,
        experience_id: str,
        application_id: str,
        secret_reference_id: str,
    ) -> dict[str, object]:
        self._require_secret_access(experience_id, application_id, secret_reference_id)
        return self.secrets.delete_secret_reference_value(
            application_id, secret_reference_id
        )

    def active_revision(self, experience_id: str) -> ExperienceRevision:
        experience = self.records.experiences.get(experience_id)
        if experience is None:
            self._fail("not_found", "experience was not found", 404)
        if experience.status != "active" or experience.active_revision_id is None:
            self._fail("inactive_experience", "experience is not active", 409)
        revision = self.records.experience_revisions.get(experience.active_revision_id)
        if (
            revision is None
            or revision.experience_id != experience.id
            or revision.status != "active"
        ):
            self._fail(
                "inactive_experience",
                "experience has no valid active revision",
                409,
            )
        return revision

    def _application_access(
        self, experience_id: str, application_id: str
    ) -> tuple[ExperienceRevision, ApplicationAccessDeclaration]:
        revision = self.active_revision(experience_id)
        access = next(
            (
                item
                for item in revision.application_access
                if item.application_id == application_id
            ),
            None,
        )
        if access is None:
            self._fail(
                "undeclared_application",
                "application is not declared by the active experience",
                403,
            )
        application = self.records.applications.get(application_id)
        if application is None:
            self._fail(
                "undeclared_application",
                "declared application is unavailable",
                404,
            )
        if (
            application.status not in {"active", "triggers_paused"}
            or application.active_revision_id is None
        ):
            self._fail(
                "inactive_application",
                "declared application is not active",
                409,
            )
        app_revision = self.records.revisions.get(application.active_revision_id)
        if (
            app_revision is None
            or app_revision.application_id != application.id
            or app_revision.status != "active"
        ):
            self._fail(
                "inactive_application",
                "declared application has no valid active revision",
                409,
            )
        return revision, access

    def _require_definition(
        self,
        application_id: str,
        identifier: str,
        allowlist: list[str] | None,
        stable_records,
        revision_records,
        kind: str,
    ) -> None:
        if not isinstance(identifier, str) or not identifier.strip():
            self._fail("invalid_filter", f"{kind}_id must not be empty", 400)
        stable = stable_records.get(identifier)
        if stable is None:
            self._fail("not_found", f"{kind} was not found", 404)
        if stable.application_id != application_id:
            self._fail(
                "undeclared_definition",
                f"{kind} is not declared by the requested application",
                403,
            )
        application = self.records.applications[application_id]
        bound = {
            "action": self.bindings.action_revisions,
            "object_type": self.bindings.object_type_revisions,
            "relation_type": self.bindings.relation_type_revisions,
        }.get(kind)
        active_id = application.active_revision_id
        if (
            bound is None
            or active_id is None
            or stable.active_revision_id is None
            or revision_records.get(stable.active_revision_id) is None
            or stable.active_revision_id
            not in {item.id for item in bound(active_id)}
        ):
            self._fail("inactive_definition", f"{kind} is not active", 409)
        if allowlist is not None and identifier not in allowlist:
            self._fail(
                "access_denied",
                f"{kind} is denied by the experience access declaration",
                403,
            )

    def _require_secret_access(
        self,
        experience_id: str,
        application_id: str,
        secret_reference_id: str,
    ) -> None:
        if not isinstance(secret_reference_id, str) or not secret_reference_id.strip():
            self._fail(
                "invalid_secret_reference",
                "secret_reference_id must not be empty",
                400,
            )
        _, access = self._application_access(experience_id, application_id)
        if (
            not access.secret_reference_ids
            or secret_reference_id not in access.secret_reference_ids
        ):
            self._fail(
                "access_denied",
                "secret reference is denied by the experience access declaration",
                403,
            )
        reference = self.records.secret_references.get(secret_reference_id)
        if reference is None:
            self._fail("not_found", "secret reference was not found", 404)
        if reference.application_id != application_id:
            self._fail(
                "undeclared_definition",
                "secret reference is not owned by the requested application",
                403,
            )

    @staticmethod
    def _fail(code: str, message: str, status_code: int) -> None:
        raise ExperienceAccessError(code, message, status_code)
