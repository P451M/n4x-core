from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal

from n4x.graph.integrity import GraphIntegrityService
from n4x.graph.store import GraphStore, Neo4jGraphStore
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ValidationFailure
from n4x.kernel.models import (
    ActionRevision,
    Application,
    ApplicationAccessDeclaration,
    ApplicationRelation,
    ApplicationRevision,
    AppBlueprint,
    BlueprintRevision,
    CallbackRoute,
    DataSpace,
    DataSpaceCloneSpec,
    DevelopmentDeployment,
    Experience,
    ExperienceRevision,
    ExperienceSurface,
    GraphCheckpoint,
    Invocation,
    ObjectTypeRevision,
    RelationTypeRevision,
    RuntimeDependency,
    TestCase,
    TriggerRevision,
    UiProfile,
    ValidationReport,
)
from n4x.runtime.action_supervisor import ActionSupervisor
from n4x.runtime.actions import ActionRuntime, RuntimePaths
from n4x.runtime.cypher_gateway import CypherGateway
from n4x.runtime.scheduler import TriggerRuntime
from n4x.runtime.surfaces import ExperienceSurfaceRuntime
from n4x.secrets.backends import SecretBackend
from n4x.secrets.service import SecretService
from n4x.source_store.service import SourceStore
from n4x.system.activation import ActivationService
from n4x.system.applications import Applications
from n4x.system.authoring import PlatformAuthoringService
from n4x.system.blueprints import BlueprintService
from n4x.system.checkpoints import CheckpointService
from n4x.system.data_spaces import DataSpaceService
from n4x.system.definitions import Definitions
from n4x.system.development_deployments import DevelopmentDeploymentService
from n4x.system.experience_access import ExperienceAccessService
from n4x.system.experience_activation import ExperienceActivationService
from n4x.system.experiences import Experiences
from n4x.system.file_delivery import FileDeliveryService
from n4x.system.invocations import Invocations
from n4x.system.jobs import JobService
from n4x.system.objects import Objects
from n4x.system.packages import PackageService
from n4x.system.queries import QueryService
from n4x.system.relations import Relations
from n4x.system.schema import Schema
from n4x.system.surface_notifications import SurfaceCatalogNotifier


class SystemRuntime:
    """System worker composition root."""

    def __init__(
        self,
        store: GraphStore,
        secret_backend: SecretBackend | None = None,
        runtime_paths: RuntimePaths | None = None,
        *,
        reset_dev_graph: bool = False,
    ) -> None:
        self.store = store
        self.uow = GraphUnitOfWork(store)
        self.graph = self.uow.records
        self.integrity = GraphIntegrityService(store, self.uow)
        self.platform_authoring = PlatformAuthoringService(self.uow)
        self.platform_authoring_service = self.platform_authoring
        if reset_dev_graph:
            self.integrity.reset_dev_graph()
            self.platform_authoring_release = (
                self.platform_authoring.bootstrap_packaged_release()
            )
        else:
            self.integrity.bootstrap_schema()
            self.platform_authoring_release = (
                self.platform_authoring.bootstrap_packaged_release()
            )
        self.source = SourceStore(store, self.uow)
        self.source_store = self.source
        self.secrets = SecretService(store, secret_backend, self.uow)
        self.applications = Applications(self.uow, self.source)
        self.experiences = Experiences(self.uow, self.source)
        self.schema = Schema(self.uow)
        self.definitions = Definitions(self.uow, self.source)
        self.objects = Objects(self.uow)
        self.relations = Relations(self.uow, self.schema)
        self.cypher_gateway = CypherGateway(store)
        self.action_runtime = ActionRuntime(
            self.source,
            self.secrets,
            store,
            runtime_paths,
            self.uow,
            self.cypher_gateway,
        )
        self.action_supervisor = ActionSupervisor(self.action_runtime, self.uow)
        self.invocations = Invocations(self.uow, self.action_supervisor)
        self.scheduler = TriggerRuntime(self.action_supervisor, store, self.uow)
        self.jobs = JobService(self.uow, self.scheduler)
        self.file_delivery = FileDeliveryService(self.action_runtime.paths)
        self.data_spaces = DataSpaceService(self.uow)
        self.development_deployment_service = DevelopmentDeploymentService(
            self.uow,
            self.data_spaces,
            self.action_supervisor,
            self.file_delivery,
            process_pool=self.action_runtime.pool,
        )
        self.development_deployments = self.development_deployment_service
        self.experience_access_service = ExperienceAccessService(
            self.uow,
            self.objects,
            self.relations,
            self.invocations,
            self.secrets,
            self.file_delivery,
        )
        self.experience_access = self.experience_access_service
        self.checkpoints = CheckpointService(self.uow)
        self.activation = ActivationService(
            self.uow,
            self.source,
            self.invocations,
            self.definitions,
            self.scheduler,
            self.checkpoints,
            self.cypher_gateway,
            process_pool=self.action_runtime.pool,
        )
        self.surface_catalog_notifier = SurfaceCatalogNotifier()
        self.surfaces = ExperienceSurfaceRuntime(
            self.source,
            self.action_runtime.paths.root,
            store,
            self.uow,
            self.platform_authoring,
        )
        self.experience_activation = ExperienceActivationService(
            self.uow,
            self.source,
            self.surfaces,
            self.surface_catalog_notifier,
        )
        self.blueprints = BlueprintService(
            self.uow,
            self.source,
            self.applications,
            self.schema,
            self.definitions,
            self.experiences,
            self.experiences.surfaces,
        )
        self.packages = PackageService(
            self.uow,
            runtime_root=self.action_runtime.paths.root,
            source=self.source,
            secrets=self.secrets,
            applications=self.applications,
            schema=self.schema,
            definitions=self.definitions,
            experiences=self.experiences,
            experience_surfaces=self.experiences.surfaces,
            activation=self.activation,
            experience_activation=self.experience_activation,
            scheduler=self.scheduler,
        )
        self.queries = QueryService(self.uow)
        self.application_service = self.applications
        self.schema_service = self.schema
        self.definition_service = self.definitions
        self.experience_service = self.experiences
        self.experience_surface_service = self.experiences.surfaces
        self.blueprint_service = self.blueprints
        self.invocation_service = self.invocations
        self.activation_service = self.activation
        self.experience_activation_service = self.experience_activation
        self.package_service = self.packages
        self.job_service = self.jobs
        self.object_service = self.objects
        self.relation_service = self.relations
        self.checkpoint_service = self.checkpoints
        self.data_space_service = self.data_spaces
        self.file_delivery_service = self.file_delivery
        self.query_service = self.queries
        self.runtime = self.action_runtime

    def close(self) -> None:
        self.jobs.shutdown()
        self.action_supervisor.shutdown(wait=True)
        self.action_runtime.shutdown()
        if isinstance(self.store, Neo4jGraphStore):
            self.store.graph.close()

    def list_experiences(self):
        return self.experiences.list()

    def list_experience_surfaces(self, experience_revision_id: str):
        return self.experiences.surfaces.list(experience_revision_id)

    def list_application_objects(
        self, application_id: str, object_type_id: str | None = None
    ):
        return self.objects.list(application_id, object_type_id)

    def list_application_relations(
        self,
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
    ):
        return self.relations.list(
            application_id,
            relation_type_id=relation_type_id,
            from_object_id=from_object_id,
            to_object_id=to_object_id,
        )

    def resolve_development_experience_revision(
        self, deployment_id: str, experience_id: str
    ):
        return self.development_deployments.experience_revision(
            deployment_id, experience_id
        )

    def run_development_action(
        self,
        deployment_id: str,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ):
        return self.development_deployments.run_action(
            deployment_id, application_id, action_id, input_value
        )

    def submit_development_action(
        self,
        deployment_id: str,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ):
        return self.development_deployments.submit_action(
            deployment_id, application_id, action_id, input_value
        )

    def cancel_invocation(self, invocation_id: str):
        return self.invocations.cancel_invocation(invocation_id)

    def run_active_action(
        self, application_id: str, action_id: str, input_value: dict[str, Any]
    ):
        return self.invocations.run_active_action(
            application_id, action_id, input_value
        )

    def submit_active_action(
        self, application_id: str, action_id: str, input_value: dict[str, Any]
    ):
        return self.invocations.submit_active_action(
            application_id, action_id, input_value
        )

    def list_experience_application_objects(
        self,
        experience_id: str,
        application_id: str,
        object_type_id: str | None = None,
    ):
        return self.experience_access.list_objects(
            experience_id, application_id, object_type_id
        )

    def list_experience_application_relations(
        self,
        experience_id: str,
        application_id: str,
        relation_type_id: str | None = None,
        from_object_id: str | None = None,
        to_object_id: str | None = None,
    ):
        return self.experience_access.list_relations(
            experience_id,
            application_id,
            relation_type_id=relation_type_id,
            from_object_id=from_object_id,
            to_object_id=to_object_id,
        )

    def invoke_experience_application_action(
        self,
        experience_id: str,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ):
        return self.experience_access.invoke_action(
            experience_id, application_id, action_id, input_value
        )

    def deliver_experience_application_file(
        self, experience_id: str, application_id: str, token: str
    ):
        return self.experience_access.deliver_file(
            experience_id, application_id, token
        )

    def experience_secret_status(
        self, experience_id: str, application_id: str, secret_reference_id: str
    ):
        return self.experience_access.secret_status(
            experience_id, application_id, secret_reference_id
        )

    def list_experience_secrets(self, experience_id: str, application_id: str):
        return self.experience_access.list_secrets(experience_id, application_id)

    def list_development_secrets(self, deployment_id: str, application_id: str):
        reference_ids = (
            self.development_deployments.bound_secret_reference_ids(
                deployment_id, application_id
            )
        )
        return self._secret_status_rows(application_id, reference_ids)

    def development_secret_status(
        self,
        deployment_id: str,
        application_id: str,
        secret_reference_id: str,
    ):
        bound = self.development_deployments.bound_secret_reference_ids(
            deployment_id, application_id
        )
        if secret_reference_id not in bound:
            raise ValidationFailure(
                f"secret reference is not bound to deployment: {secret_reference_id}"
            )
        rows = self._secret_status_rows(application_id, [secret_reference_id])
        if not rows:
            raise KeyError(secret_reference_id)
        return rows[0]

    def _secret_status_rows(
        self, application_id: str, reference_ids: list[str]
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for reference_id in reference_ids:
            reference = self.uow.records.secret_references.get(reference_id)
            if reference is None or reference.application_id != application_id:
                raise ValidationFailure(
                    f"declared secret reference is unavailable: {reference_id}"
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

    def set_experience_secret_value(
        self,
        experience_id: str,
        application_id: str,
        secret_reference_id: str,
        value: str,
    ):
        return self.experience_access.set_secret_value(
            experience_id, application_id, secret_reference_id, value
        )

    def delete_experience_secret_value(
        self,
        experience_id: str,
        application_id: str,
        secret_reference_id: str,
    ):
        return self.experience_access.delete_secret_value(
            experience_id, application_id, secret_reference_id
        )

    def dispatch_callback_route(self, route_id: str, payload: dict[str, Any]):
        return self.invocations.dispatch_callback_route(route_id, payload)

    def inspect_component_palette(self):
        return self.platform_authoring.inspect_component_palette()

    def inspect_authoring_guide(self, guide_id: str | None = None):
        return self.platform_authoring.inspect_authoring_guide(guide_id)

    def inspect_surface_theme(self):
        return self.platform_authoring.inspect_surface_theme()

    def inspect_experience_bridge(self):
        return self.platform_authoring.inspect_experience_bridge()

    def start_scheduler(self) -> None:
        self.jobs.start()

    def shutdown_scheduler(self) -> None:
        self.jobs.shutdown()

    def reset_dev_graph(self) -> dict[str, Any]:
        self.integrity.reset_dev_graph()
        self.platform_authoring_release = (
            self.platform_authoring.bootstrap_packaged_release()
        )
        report = self.integrity.validate_graph_shape()
        return {
            "ok": report.ok,
            "errors": report.errors,
            "warnings": report.warnings,
        }

    def build_experience_surface(
        self, experience_revision_id: str, surface_id: str
    ):
        surface = self.experiences.surfaces.inspect(
            experience_revision_id, surface_id
        )
        return self.surfaces.build(experience_revision_id, surface)

    def resolve_experience_surface_artifact(
        self,
        experience_revision_id: str,
        surface_id: str,
        input_hash: str | None = None,
    ):
        from pathlib import Path

        surface = self.experiences.surfaces.inspect(
            experience_revision_id, surface_id
        )
        resolved_hash = input_hash or self.surfaces.build_input_hash(
            experience_revision_id, surface
        )
        artifacts = [
            artifact
            for artifact in self.uow.records.build_artifacts.values()
            if artifact.owner_kind == "ExperienceRevision"
            and artifact.owner_id == experience_revision_id
            and artifact.surface_id == surface_id
            and artifact.input_hash == resolved_hash
        ]
        existing = [
            artifact for artifact in artifacts if Path(artifact.path).exists()
        ]
        selected = existing or artifacts
        return selected[-1] if selected else None

    def validate_graph_shape(self) -> dict[str, Any]:
        report = self.integrity.validate_graph_shape()
        return {
            "ok": report.ok,
            "errors": report.errors,
            "warnings": report.warnings,
        }

    def repair_graph_edges(self) -> dict[str, Any]:
        report = self.integrity.repair_graph_edges()
        return {
            "ok": report.ok,
            "errors": report.errors,
            "warnings": report.warnings,
        }

    def inspect_application(self, application_id: str) -> Application | None:
        return self.query_service.application(application_id)

    def inspect_experience_revision(
        self, experience_revision_id: str
    ) -> dict[str, Any]:
        payload = self.experiences.inspect_revision(experience_revision_id)
        payload["surfaces_without_current_artifact"] = [
            surface.surface_id
            for surface in self.experiences.surfaces.list(experience_revision_id)
            if self.resolve_experience_surface_artifact(
                experience_revision_id, surface.surface_id
            )
            is None
        ]
        return payload

    def inspect_invocations(self) -> list[Invocation]:
        return self.query_service.invocations()

    def inspect_secret_references(self):
        return self.query_service.secret_references()

    def inspect_credential_records(self):
        return self.query_service.credential_records()

    def create_application(
        self,
        application_id: str,
        name: str,
        description: str = "",
    ) -> Application:
        return self.application_service.create(application_id, name, description)

    def create_development_data_space(
        self,
        application_id: str,
        *,
        data_space_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> DataSpace:
        return self.data_space_service.create_development(
            application_id,
            data_space_id=data_space_id,
            expires_at=expires_at,
        )

    def create_development_deployment(
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
        return self.development_deployment_service.create(
            experience_revision_id,
            application_revision_ids,
            ttl_seconds=ttl_seconds,
            initialization=initialization,
            clone_specs=clone_specs,
        )

    def inspect_development_deployment(
        self, deployment_id: str
    ) -> DevelopmentDeployment:
        return self.development_deployment_service.get(deployment_id)

    def expire_development_deployment(
        self, deployment_id: str
    ) -> DevelopmentDeployment:
        deployment = self.development_deployment_service.expire(
            deployment_id
        )
        for application_id, data_space_id in (
            deployment.data_space_ids.items()
        ):
            scope_key = self.runtime.paths.application_data_scope_key(
                application_id, data_space_id
            )
            self.runtime.paths.purge_application_data_scope(scope_key)
        return deployment

    def create_application_revision(
        self,
        application_id: str,
        created_by: str = "system",
        ui_profile: UiProfile | None = None,
        parent_revision_id: str | None = None,
    ) -> ApplicationRevision:
        return self.application_service.create_revision(
            application_id,
            created_by,
            ui_profile,
            parent_revision_id=parent_revision_id,
        )

    def discard_application_revision(self, application_revision_id: str) -> None:
        self.application_service.discard_revision(application_revision_id)

    def create_runtime_dependency(
        self,
        application_revision_id: str,
        ecosystem: str,
        package: str,
        spec: str,
    ) -> RuntimeDependency:
        return self.application_service.create_runtime_dependency(
            application_revision_id, ecosystem, package, spec
        )

    def create_experience(
        self,
        experience_id: str,
        name: str,
        description: str = "",
    ) -> Experience:
        return self.experience_service.create(experience_id, name, description)

    def retire_experience(
        self,
        experience_id: str,
        *,
        expected_active_revision_id: str | None = None,
    ) -> Experience:
        return self.experience_service.retire(
            experience_id,
            expected_active_revision_id=expected_active_revision_id,
        )

    def create_experience_revision(
        self,
        experience_id: str,
        *,
        created_by: str = "system",
        ui_profile: UiProfile | None = None,
        parent_revision_id: str | None = None,
        application_access: list[ApplicationAccessDeclaration | dict[str, Any]]
        | None = None,
    ) -> ExperienceRevision:
        return self.experience_service.create_revision(
            experience_id,
            created_by=created_by,
            ui_profile=ui_profile,
            parent_revision_id=parent_revision_id,
            application_access=application_access,
        )

    def discard_experience_revision(self, experience_revision_id: str) -> None:
        self.experience_service.discard_revision(experience_revision_id)

    def create_experience_runtime_dependency(
        self,
        experience_revision_id: str,
        ecosystem: str,
        package: str,
        spec: str,
    ) -> RuntimeDependency:
        return self.experience_service.create_runtime_dependency(
            experience_revision_id, ecosystem, package, spec
        )

    def set_experience_application_access(
        self,
        experience_revision_id: str,
        application_access: list[ApplicationAccessDeclaration | dict[str, Any]],
    ) -> ExperienceRevision:
        return self.experience_service.set_application_access(
            experience_revision_id, application_access
        )

    def create_experience_surface(
        self,
        experience_revision_id: str,
        surface_id: str,
        *,
        surface_type: str,
        surface_type_version: int = 1,
        entrypoint: str,
        source_paths: list[str],
        title: str = "",
        description: str | None = None,
        config: dict[str, Any] | None = None,
        created_by: str = "system",
    ) -> ExperienceSurface:
        return self.experience_surface_service.create(
            experience_revision_id,
            surface_id,
            surface_type=surface_type,
            surface_type_version=surface_type_version,
            entrypoint=entrypoint,
            source_paths=source_paths,
            title=title,
            description=description,
            config=config,
            created_by=created_by,
        )

    def inspect_experience_surface(
        self, experience_revision_id: str, surface_id: str
    ) -> ExperienceSurface:
        return self.experience_surface_service.inspect(
            experience_revision_id, surface_id
        )

    def update_experience_surface(
        self,
        experience_revision_id: str,
        surface_id: str,
        **changes: Any,
    ) -> ExperienceSurface:
        return self.experience_surface_service.update(
            experience_revision_id, surface_id, **changes
        )

    def delete_experience_surface(
        self, experience_revision_id: str, surface_id: str
    ) -> None:
        self.experience_surface_service.delete(experience_revision_id, surface_id)

    def create_blueprint(
        self,
        blueprint_id: str,
        name: str,
        description: str = "",
    ) -> AppBlueprint:
        return self.blueprint_service.create(blueprint_id, name, description)

    def create_blueprint_revision(
        self,
        blueprint_id: str,
        *,
        instructions: str,
        content: dict[str, Any],
        activate: bool = False,
        created_by: str = "system",
    ) -> BlueprintRevision:
        return self.blueprint_service.create_revision(
            blueprint_id,
            instructions=instructions,
            content=content,
            activate=activate,
            created_by=created_by,
        )

    def instantiate_blueprint(
        self,
        blueprint_revision_id: str,
        *,
        application_id: str,
        name: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        return self.blueprint_service.instantiate(
            blueprint_revision_id,
            application_id=application_id,
            name=name,
            description=description,
        )

    def create_object_type(
        self,
        application_revision_id: str,
        object_type_id: str,
        *,
        name: str,
        properties: dict[str, Any] | None = None,
        required: list[str] | None = None,
    ) -> ObjectTypeRevision:
        return self.schema_service.create_object_type(
            application_revision_id,
            object_type_id,
            name=name,
            properties=properties,
            required=required,
        )

    def create_relation_type(
        self,
        application_revision_id: str,
        relation_type_id: str,
        *,
        name: str,
        from_object_type_id: str,
        to_object_type_id: str,
        properties: dict[str, Any] | None = None,
    ) -> RelationTypeRevision:
        return self.schema_service.create_relation_type(
            application_revision_id,
            relation_type_id,
            name=name,
            from_object_type_id=from_object_type_id,
            to_object_type_id=to_object_type_id,
            properties=properties,
        )

    def create_action(
        self,
        application_revision_id: str,
        action_id: str,
        *,
        kind: str,
        entrypoint: str,
        source_paths: list[str],
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        dependency_ids: list[str] | None = None,
        secret_ref_ids: list[str] | None = None,
        migration_metadata: dict[str, Any] | None = None,
        timeout_seconds: int = 30,
        concurrency_policy: str = "default",
    ) -> ActionRevision:
        return self.definition_service.create_action(
            application_revision_id,
            action_id,
            kind=kind,
            entrypoint=entrypoint,
            source_paths=source_paths,
            input_schema=input_schema,
            output_schema=output_schema,
            dependency_ids=dependency_ids,
            secret_ref_ids=secret_ref_ids,
            migration_metadata=migration_metadata,
            timeout_seconds=timeout_seconds,
            concurrency_policy=concurrency_policy,
        )

    def create_trigger(
        self,
        application_revision_id: str,
        trigger_id: str,
        *,
        trigger_type: str,
        action_id: str,
        config: dict[str, Any] | None = None,
        input_template: dict[str, Any] | None = None,
        overlap_policy: str | None = None,
        misfire_policy: str = "run_once",
        max_attempts: int = 3,
        retry_policy: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> TriggerRevision:
        return self.definition_service.create_trigger(
            application_revision_id,
            trigger_id,
            trigger_type=trigger_type,
            action_id=action_id,
            config=config,
            input_template=input_template,
            overlap_policy=overlap_policy,
            misfire_policy=misfire_policy,
            max_attempts=max_attempts,
            retry_policy=retry_policy,
            enabled=enabled,
        )

    def inspect_experience_design_context(
        self,
        experience_revision_id: str,
        *,
        include_content: bool = True,
        last_seen_hash: str | None = None,
    ) -> dict[str, Any]:
        return self.platform_authoring_service.inspect_experience_design_context(
            experience_revision_id,
            include_content=include_content,
            last_seen_hash=last_seen_hash,
        )

    def inspect_job_attempts(self, job_id: str | None = None):
        return self.query_service.job_attempts(job_id)

    def process_due_job_work(self):
        return self.job_service.process_due_work()

    def recover_expired_job_leases(self):
        return self.job_service.recover_expired_leases()

    def run_draft_action(
        self,
        application_revision_id: str,
        action_id: str,
        input_value: dict[str, Any],
        *,
        data_space_id: str | None = None,
    ) -> Invocation:
        return self.invocation_service.run_draft_action(
            application_revision_id,
            action_id,
            input_value,
            data_space_id=data_space_id,
        )

    def submit_draft_action(
        self,
        application_revision_id: str,
        action_id: str,
        input_value: dict[str, Any],
        *,
        data_space_id: str | None = None,
    ) -> Invocation:
        return self.invocation_service.submit_draft_action(
            application_revision_id,
            action_id,
            input_value,
            data_space_id=data_space_id,
        )

    def await_invocation(
        self, invocation_id: str, timeout: float | None = None
    ) -> Invocation:
        return self.invocation_service.await_invocation(
            invocation_id, timeout
        )

    def create_test_case(
        self,
        application_revision_id: str,
        action_id: str,
        input_value: dict[str, Any],
        expected_output: Any,
    ) -> TestCase:
        return self.definition_service.create_test_case(
            application_revision_id,
            action_id,
            input_value,
            expected_output,
        )

    def run_application_tests(self, application_revision_id: str) -> list[Invocation]:
        return self.invocation_service.run_application_tests(application_revision_id)

    def validate_application_revision(
        self, application_revision_id: str
    ) -> ValidationReport:
        return self.activation_service.validate(application_revision_id)

    def activate_application_revision(
        self, application_revision_id: str
    ) -> ApplicationRevision:
        return self.activation_service.activate(application_revision_id)

    def validate_experience_revision(self, experience_revision_id: str):
        return self.experience_activation_service.validate(experience_revision_id)

    def activate_experience_revision(
        self, experience_revision_id: str
    ) -> ExperienceRevision:
        return self.experience_activation_service.activate(experience_revision_id)

    def preview_package(
        self,
        root_kind: str,
        root_id: str,
        *,
        include_data: bool = False,
    ) -> dict[str, Any]:
        return self.package_service.preview(
            root_kind, root_id, include_data=include_data
        )

    def export_package(
        self,
        root_kind: str,
        root_id: str,
        archive_name: str,
        *,
        include_data: bool = False,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return self.package_service.export(
            root_kind,
            root_id,
            archive_name,
            include_data=include_data,
            overwrite=overwrite,
        )

    def delete_working_set(
        self,
        root_kind: str,
        root_id: str,
        archive_name: str,
        *,
        confirmation_root_id: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return self.package_service.delete_working_set(
            root_kind,
            root_id,
            archive_name,
            confirmation_root_id=confirmation_root_id,
            overwrite=overwrite,
        )

    def stage_package(
        self,
        archive_name: str,
        archive_bytes: bytes,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return self.package_service.stage(
            archive_name,
            archive_bytes,
            overwrite=overwrite,
        )

    def stage_package_from_path(
        self,
        archive_name: str,
        source,
        *,
        overwrite: bool = False,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        return self.package_service.stage_from_path(
            archive_name,
            source,
            overwrite=overwrite,
            expected_sha256=expected_sha256,
        )

    def list_packages(self) -> list[dict[str, Any]]:
        return self.package_service.list()

    def inspect_package(self, archive_name: str) -> dict[str, Any]:
        return self.package_service.inspect(archive_name)

    def import_package(
        self,
        archive_name: str,
        *,
        dry_run: bool = False,
        restore_data: bool | None = None,
        allow_incompatible: bool = False,
    ) -> dict[str, Any]:
        return self.package_service.import_package(
            archive_name,
            dry_run=dry_run,
            restore_data=restore_data,
            allow_incompatible=allow_incompatible,
        )

    def inspect_package_import(self, attempt_id: str) -> dict[str, Any]:
        return self.package_service.inspect_import(attempt_id)

    def resume_application_triggers(self, application_id: str) -> Application:
        application = self.query_service.application(application_id)
        if application is None:
            raise KeyError(application_id)
        if application.status != "triggers_paused":
            raise ValidationFailure(
                "Application triggers can resume only from triggers_paused"
            )
        resumed = self.application_service.set_status(
            application_id,
            "active",
            expected_status="triggers_paused",
        )
        self.scheduler.remount()
        return resumed

    def rollback_application(
        self, application_id: str, target_revision_id: str
    ) -> ApplicationRevision:
        return self.activation_service.rollback(application_id, target_revision_id)

    def create_checkpoint(
        self,
        application_id: str,
        *,
        level: str = "revision",
        reason: str = "manual checkpoint",
    ) -> GraphCheckpoint:
        return self.checkpoint_service.create(
            application_id, level=level, reason=reason
        )

    def restore_checkpoint(self, checkpoint_id: str) -> GraphCheckpoint:
        checkpoint = self.checkpoint_service.restore(checkpoint_id)
        self.scheduler.remount()
        return checkpoint

    def inspect_checkpoints(
        self, application_id: str | None = None
    ) -> list[GraphCheckpoint]:
        return self.checkpoint_service.list(application_id)

    def run_trigger(
        self,
        trigger_id: str,
        input_value: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ):
        return self.job_service.run_trigger(
            trigger_id,
            input_value,
            idempotency_key=idempotency_key,
        )

    def dispatch_event(
        self,
        application_id: str,
        event_type: str,
        payload: dict[str, Any],
    ):
        return self.job_service.dispatch_event(application_id, event_type, payload)

    def inspect_scheduler(self) -> dict[str, Any]:
        return self.job_service.inspect()

    def create_callback_route(
        self,
        application_id: str,
        target_action_revision_id: str,
        *,
        state: str,
        expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CallbackRoute:
        return self.invocation_service.create_callback_route(
            application_id,
            target_action_revision_id,
            state=state,
            expires_at=expires_at,
            metadata=metadata,
        )

    def inspect_callback_routes(
        self, application_id: str | None = None
    ) -> list[CallbackRoute]:
        return self.query_service.callback_routes(application_id)

    def create_application_relation(
        self,
        application_id: str,
        relation_type_id: str,
        from_object_id: str,
        to_object_id: str,
        *,
        values: dict[str, Any] | None = None,
        relation_id: str | None = None,
    ) -> ApplicationRelation:
        return self.relation_service.create(
            application_id,
            relation_type_id,
            from_object_id,
            to_object_id,
            values=values,
            relation_id=relation_id,
        )

    def delete_application_relation(
        self, application_id: str, relation_id: str
    ) -> None:
        return self.relation_service.delete(application_id, relation_id)

    @classmethod
    def neo4j(cls, config: Any, *, reset_dev_graph: bool = False) -> SystemRuntime:
        from n4x.graph.neo4j import Neo4jGraph

        graph = Neo4jGraph(config)
        try:
            graph.verify_connectivity()
            return cls(Neo4jGraphStore(graph), reset_dev_graph=reset_dev_graph)
        except BaseException:
            graph.close()
            raise

    @classmethod
    def from_env(cls) -> SystemRuntime:
        if os.getenv("N4X_NEO4J_URI"):
            from n4x.graph.neo4j import Neo4jConfig

            return cls.neo4j(Neo4jConfig.from_env())
        from n4x.testing.graph_store import InMemoryGraphStore

        return cls(InMemoryGraphStore())
