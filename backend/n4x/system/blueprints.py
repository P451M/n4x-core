"""Reusable app blueprints owned by the System."""

from __future__ import annotations

from typing import Any, Protocol

from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.hash import sha256_json
from n4x.kernel.models import (
    ActionRevision,
    AppBlueprint,
    Application,
    ApplicationRevision,
    BlueprintRevision,
    Experience,
    ExperienceBlueprintDefinition,
    ExperienceRevision,
    ObjectTypeRevision,
    RelationTypeRevision,
    RuntimeDependency,
    TestCase,
    TriggerRevision,
    UiProfile,
)
from n4x.graph.service_base import transactional


class BlueprintApplicationPort(Protocol):
    def create(
        self, application_id: str, name: str, description: str = ""
    ) -> Application: ...

    def create_revision(
        self,
        application_id: str,
        created_by: str = "system",
        ui_profile: UiProfile | None = None,
    ) -> ApplicationRevision: ...

    def create_runtime_dependency(
        self,
        application_revision_id: str,
        ecosystem: str,
        package: str,
        spec: str,
    ) -> RuntimeDependency: ...


class BlueprintSchemaPort(Protocol):
    def create_object_type(
        self,
        application_revision_id: str,
        object_type_id: str,
        *,
        name: str,
        properties: dict[str, Any] | None = None,
        required: list[str] | None = None,
    ) -> ObjectTypeRevision: ...

    def create_relation_type(
        self,
        application_revision_id: str,
        relation_type_id: str,
        *,
        name: str,
        from_object_type_id: str,
        to_object_type_id: str,
        properties: dict[str, Any] | None = None,
    ) -> RelationTypeRevision: ...


class BlueprintDefinitionPort(Protocol):
    def create_action(self, *args, **kwargs) -> ActionRevision: ...

    def create_trigger(self, *args, **kwargs) -> TriggerRevision: ...



class BlueprintExperiencePort(Protocol):
    def create(
        self, experience_id: str, name: str, description: str = ""
    ) -> Experience: ...

    def create_revision(
        self,
        experience_id: str,
        *,
        created_by: str = "system",
        ui_profile: UiProfile | None = None,
        application_access: list[dict[str, Any]] | None = None,
    ) -> ExperienceRevision: ...

    def create_runtime_dependency(
        self,
        experience_revision_id: str,
        ecosystem: str,
        package: str,
        spec: str,
    ) -> RuntimeDependency: ...



class BlueprintExperienceSurfacePort(Protocol):
    def create(self, *args, **kwargs): ...


class BlueprintInvocationPort(Protocol):
    def create_test_case(
        self,
        application_revision_id: str,
        action_revision_id: str,
        input_value: dict[str, Any],
        expected_output: Any,
    ) -> TestCase: ...


class BlueprintSourcePort(Protocol):
    def write_source_file(self, *args, **kwargs): ...


class BlueprintService:
    """Stores reusable app blueprints and coordinates their instantiation."""

    def __init__(
        self,
        uow: GraphUnitOfWork,
        source: BlueprintSourcePort,
        applications: BlueprintApplicationPort,
        schema: BlueprintSchemaPort,
        definitions: BlueprintDefinitionPort,
        experiences: BlueprintExperiencePort,
        experience_surfaces: BlueprintExperienceSurfacePort,
        invocations: BlueprintInvocationPort,
    ) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.source = source
        self.applications = applications
        self.schema = schema
        self.definitions = definitions
        self.experiences = experiences
        self.experience_surfaces = experience_surfaces
        self.invocations = invocations

    @transactional
    def create(
        self, blueprint_id: str, name: str, description: str = ""
    ) -> AppBlueprint:
        blueprint = self.records.app_blueprints.get(blueprint_id)
        if blueprint is not None:
            return blueprint
        blueprint = AppBlueprint(id=blueprint_id, name=name, description=description)
        self.records.app_blueprints.save(blueprint)
        self.store.create_edge(
            node_ref("N4XRoot", id="n4x"),
            "HAS_BLUEPRINT",
            node_ref("AppBlueprint", id=blueprint.id),
        )
        return blueprint

    @transactional
    def create_revision(
        self,
        blueprint_id: str,
        *,
        instructions: str,
        content: dict[str, Any],
        activate: bool = False,
        created_by: str = "system",
    ) -> BlueprintRevision:
        if self.records.app_blueprints.get(blueprint_id) is None:
            raise KeyError(blueprint_id)
        content = self._validated_content(content)
        revision = BlueprintRevision(
            id=(
                f"{blueprint_id}@"
                f"{len([item for item in self.records.blueprint_revisions.values() if item.blueprint_id == blueprint_id]) + 1}"
            ),
            blueprint_id=blueprint_id,
            status="active" if activate else "draft",
            instructions=instructions,
            content=content,
            created_by=created_by,
            content_hash=sha256_json(
                {"instructions": instructions, "content": content}
            ),
        )
        self.records.blueprint_revisions.save(revision)
        self.store.create_edge(
            node_ref("AppBlueprint", id=blueprint_id),
            "HAS_REVISION",
            node_ref("BlueprintRevision", id=revision.id),
        )
        if activate:
            blueprint = self.records.app_blueprints[blueprint_id]
            if blueprint.active_revision_id is not None:
                current = self.records.blueprint_revisions.get(
                    blueprint.active_revision_id
                )
                if current is not None:
                    self.records.blueprint_revisions.save(
                        current.model_copy(update={"status": "superseded"})
                    )
            self.records.app_blueprints.save(
                blueprint.model_copy(update={"active_revision_id": revision.id})
            )
            self.store.replace_single_edge(
                node_ref("AppBlueprint", id=blueprint_id),
                "ACTIVE_REVISION",
                node_ref("BlueprintRevision", id=revision.id),
            )
        return revision

    def _validated_content(self, content: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(content)
        experiences = [
            ExperienceBlueprintDefinition.model_validate(item)
            for item in content.get("experiences", [])
        ]
        experience_ids = [item.id for item in experiences]
        if len(experience_ids) != len(set(experience_ids)):
            raise ValueError("blueprint Experience ids must be unique")
        if experiences or "experiences" in content:
            normalized["experiences"] = [
                item.model_dump(mode="json") for item in experiences
            ]
        return normalized

    def list(self) -> list[AppBlueprint]:
        return sorted(self.records.app_blueprints.values(), key=lambda item: item.id)

    def inspect(self, blueprint_id: str) -> dict[str, Any]:
        blueprint = self.records.app_blueprints[blueprint_id]
        revisions = sorted(
            [
                revision
                for revision in self.records.blueprint_revisions.values()
                if revision.blueprint_id == blueprint_id
            ],
            key=lambda revision: revision.id,
        )
        return {
            "blueprint": blueprint.model_dump(mode="json"),
            "revisions": [revision.model_dump(mode="json") for revision in revisions],
        }

    @transactional
    def instantiate(
        self,
        blueprint_revision_id: str,
        *,
        application_id: str,
        name: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        blueprint_revision = self.records.blueprint_revisions[blueprint_revision_id]
        content = blueprint_revision.content
        app_values = content.get("application", {})
        if self.records.applications.get(application_id) is None:
            self.applications.create(
                application_id,
                name or app_values.get("name") or application_id,
                description or app_values.get("description", ""),
            )
        revision = self.applications.create_revision(
            application_id,
            created_by=f"blueprint:{blueprint_revision_id}",
            ui_profile=app_values.get("ui_profile"),
        )
        dependencies: dict[str, str] = {}
        for item in content.get("dependencies", []):
            created = self.applications.create_runtime_dependency(
                revision.id,
                item["ecosystem"],
                item["package"],
                item.get("spec", ""),
            )
            key = item.get("id") or (
                f"{item['ecosystem']}:{item['package']}:{item.get('spec', '')}"
            )
            dependencies[key] = created.id
        for source_file in content.get("source_files", []):
            self.source.write_source_file(
                revision.source_tree_id,
                source_file["path"],
                source_file["content"],
                role=source_file["role"],
                language=source_file["language"],
                actor=f"blueprint:{blueprint_revision_id}",
            )
        for object_type in content.get("object_types", []):
            self.schema.create_object_type(
                revision.id,
                object_type["id"],
                name=object_type["name"],
                properties=object_type.get("properties", {}),
                required=object_type.get("required", []),
            )
        for relation_type in content.get("relation_types", []):
            self.schema.create_relation_type(
                revision.id,
                relation_type["id"],
                name=relation_type["name"],
                from_object_type_id=relation_type["from"],
                to_object_type_id=relation_type["to"],
                properties=relation_type.get("properties", {}),
            )
        action_ids: dict[str, str] = {}
        for action in content.get("actions", []):
            created = self.definitions.create_action(
                revision.id,
                action["id"],
                kind=action.get("kind", "normal"),
                entrypoint=action["entrypoint"],
                source_paths=action["source_paths"],
                input_schema=action.get("input_schema", {}),
                output_schema=action.get("output_schema", {}),
                dependency_ids=[
                    dependencies[key] for key in action.get("dependency_keys", [])
                ],
                secret_ref_ids=action.get("secret_ref_ids", []),
                timeout_seconds=action.get("timeout_seconds", 30),
            )
            action_ids[action["id"]] = created.id
        trigger_ids: dict[str, str] = {}
        for trigger in content.get("triggers", []):
            created = self.definitions.create_trigger(
                revision.id,
                trigger["id"],
                trigger_type=trigger["trigger_type"],
                action_revision_id=action_ids[trigger["action_id"]],
                config=trigger.get("config", {}),
                input_template=trigger.get("input_template", {}),
                overlap_policy=trigger.get("overlap_policy"),
                misfire_policy=trigger.get("misfire_policy", "run_once"),
                max_attempts=trigger.get("max_attempts", 3),
                retry_policy=trigger.get("retry_policy", {}),
                enabled=trigger.get("enabled", True),
            )
            trigger_ids[trigger["id"]] = created.id
        test_ids = []
        for test in content.get("tests", []):
            created = self.invocations.create_test_case(
                revision.id,
                action_ids[test["action_id"]],
                test.get("input", {}),
                test.get("expected_output"),
            )
            test_ids.append(created.id)
        experience_results: dict[str, dict[str, Any]] = {}
        for definition_value in content.get("experiences", []):
            definition = ExperienceBlueprintDefinition.model_validate(definition_value)
            if self.records.experiences.get(definition.id) is None:
                self.experiences.create(
                    definition.id,
                    definition.name or definition.id,
                    definition.description,
                )
            access = [
                {
                    **item.model_dump(mode="json"),
                    "application_id": _resolve_application_reference(
                        item.application_id, application_id
                    ),
                }
                for item in definition.application_access
            ]
            experience_revision = self.experiences.create_revision(
                definition.id,
                created_by=f"blueprint:{blueprint_revision_id}",
                ui_profile=definition.ui_profile,
                application_access=access,
            )
            experience_dependencies: dict[str, str] = {}
            for dependency in definition.dependencies:
                created = self.experiences.create_runtime_dependency(
                    experience_revision.id,
                    "javascript",
                    dependency.package,
                    dependency.spec,
                )
                key = dependency.id or (
                    f"javascript:{dependency.package}:{dependency.spec}"
                )
                experience_dependencies[key] = created.id
            for source_file in definition.source_files:
                self.source.write_source_file(
                    experience_revision.source_tree_id,
                    source_file.path,
                    source_file.content,
                    role=source_file.role,
                    language=source_file.language,
                    actor=f"blueprint:{blueprint_revision_id}",
                )
            experience_surfaces: list[str] = []
            for surface in definition.surfaces:
                created = self.experience_surfaces.create(
                    experience_revision.id,
                    surface.id,
                    surface_type=surface.surface_type,
                    surface_type_version=surface.surface_type_version,
                    entrypoint=surface.entrypoint,
                    source_paths=surface.source_paths,
                    title=surface.title,
                    description=surface.description,
                    config=surface.config,
                    created_by=f"blueprint:{blueprint_revision_id}",
                )
                experience_surfaces.append(created.surface_id)
            experience_results[definition.id] = {
                "experience_revision_id": experience_revision.id,
                "source_tree_id": experience_revision.source_tree_id,
                "surface_ids": experience_surfaces,
            }
        return {
            "application_id": application_id,
            "application_revision_id": revision.id,
            "source_tree_id": revision.source_tree_id,
            "action_revision_ids": action_ids,
            "trigger_revision_ids": trigger_ids,
            "test_ids": test_ids,
            "experiences": experience_results,
        }


def _resolve_application_reference(value: str, application_id: str) -> str:
    if value in {"$application", "{application_id}", "@application"}:
        return application_id
    return value
