"""Action invocation and callback routes owned by the System."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

from n4x.graph.bindings import RevisionBindings
from n4x.graph.store import node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import ImmutableRevisionError, ValidationFailure
from n4x.kernel.models import (
    ActionRevision,
    CallbackRoute,
    ExecutionContext,
    Invocation,
    now_utc,
)
from n4x.graph.service_base import transactional


class ActionRuntimePort(Protocol):
    python_environments: Any

    def run(
        self,
        action_revision: ActionRevision,
        input_value: dict[str, Any],
        *,
        invocation_kind: str,
        execution_context: ExecutionContext | None = None,
    ) -> Invocation: ...

    def import_check(
        self, action_revision: ActionRevision, *, application_revision_id: str
    ) -> None: ...

    def materialize(
        self, action_revision: ActionRevision, *, application_revision_id: str
    ): ...

    def submit(
        self,
        action_revision: ActionRevision,
        input_value: dict[str, Any],
        invocation_kind: str = "draft",
        **kwargs: Any,
    ) -> Invocation: ...

    def cancel(self, invocation_id: str) -> Invocation: ...


class Invocations:
    def __init__(self, uow: GraphUnitOfWork, runtime: ActionRuntimePort) -> None:
        self.uow = uow
        self.store = uow.store
        self.records = uow.records
        self.runtime = runtime
        self.bindings = RevisionBindings(uow)

    def run_draft_action(
        self,
        application_revision_id: str,
        action_id: str,
        input_value: dict[str, Any],
        *,
        data_space_id: str | None = None,
    ) -> Invocation:
        with self.uow:
            app_revision = self.records.revisions[application_revision_id]
            if app_revision.status != "draft":
                raise ImmutableRevisionError(
                    "run_draft_action requires a draft revision"
                )
            action_revision = self.bindings.action_revision(
                app_revision.id, action_id
            )
            if action_revision.kind == "migration":
                raise ValidationFailure(
                    "migration revisions run only through activation"
                )
            execution_context = self._draft_execution_context(
                app_revision, data_space_id
            )
        return self.runtime.run(
            action_revision,
            input_value,
            invocation_kind="draft",
            execution_context=execution_context,
        )

    def run_active_action(
        self,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ) -> Invocation:
        with self.uow:
            application = self.records.applications[application_id]
            if application.status in {"disabled", "importing"}:
                raise ValidationFailure(
                    f"application is not available for actions: {application_id}"
                )
            action = self.records.actions[action_id]
            if (
                action.application_id != application_id
                or action.active_revision_id is None
                or application.active_revision_id is None
            ):
                raise KeyError(f"action is not active for application: {action_id}")
            revision = self.bindings.action_revision(
                application.active_revision_id, action_id
            )
            if revision.kind == "migration":
                raise ValidationFailure(
                    "migration revisions run only through activation"
                )
            execution_context = ExecutionContext(
                correlation_id=str(uuid.uuid4()),
                application_revision_id=application.active_revision_id,
                application_id=application_id,
            )
        return self.runtime.run(
            revision,
            input_value,
            invocation_kind="active",
            execution_context=execution_context,
        )

    def submit_draft_action(
        self,
        application_revision_id: str,
        action_id: str,
        input_value: dict[str, Any],
        *,
        data_space_id: str | None = None,
    ) -> Invocation:
        with self.uow:
            app_revision = self.records.revisions[application_revision_id]
            if app_revision.status != "draft":
                raise ImmutableRevisionError(
                    "submit_draft_action requires a draft revision"
                )
            action_revision = self.bindings.action_revision(
                app_revision.id, action_id
            )
            if action_revision.kind == "migration":
                raise ValidationFailure(
                    "migration revisions run only through activation"
                )
            execution_context = self._draft_execution_context(
                app_revision, data_space_id
            )
        return self.runtime.submit(
            action_revision,
            input_value,
            invocation_kind="draft",
            execution_context=execution_context,
        )

    def submit_active_action(
        self,
        application_id: str,
        action_id: str,
        input_value: dict[str, Any],
    ) -> Invocation:
        with self.uow:
            application = self.records.applications[application_id]
            if application.status in {"disabled", "importing"}:
                raise ValidationFailure(
                    f"application is not available for actions: {application_id}"
                )
            action = self.records.actions[action_id]
            if (
                action.application_id != application_id
                or action.active_revision_id is None
                or application.active_revision_id is None
            ):
                raise KeyError(f"action is not active for application: {action_id}")
            revision = self.bindings.action_revision(
                application.active_revision_id, action_id
            )
            if revision.kind == "migration":
                raise ValidationFailure(
                    "migration revisions run only through activation"
                )
            execution_context = ExecutionContext(
                correlation_id=str(uuid.uuid4()),
                application_revision_id=application.active_revision_id,
                application_id=application_id,
            )
        return self.runtime.submit(
            revision,
            input_value,
            invocation_kind="active",
            execution_context=execution_context,
        )

    def cancel_invocation(self, invocation_id: str) -> Invocation:
        return self.runtime.cancel(invocation_id)

    def dispatch_callback_route(
        self, route_id: str, payload: dict[str, Any]
    ) -> Invocation:
        with self.uow:
            route = self.records.callback_routes[route_id]
            now = now_utc()
            if route.status != "pending":
                raise ValidationFailure(
                    f"callback route is not pending: {route.status}"
                )
            if route.expires_at is not None and route.expires_at < now:
                self.records.callback_routes.save(
                    route.model_copy(update={"status": "expired"})
                )
                raise ValidationFailure("callback route expired")
            if route.state and payload.get("state") != route.state:
                raise ValidationFailure("callback state mismatch")
            action_revision = self.records.action_revisions[
                route.target_action_revision_id
            ]
            application = self.records.applications[route.application_id]
            if application.active_revision_id is None:
                raise ValidationFailure(
                    f"application has no active revision: {route.application_id}"
                )
            execution_context = ExecutionContext(
                correlation_id=str(uuid.uuid4()),
                application_revision_id=application.active_revision_id,
                application_id=route.application_id,
            )
        invocation = self.runtime.run(
            action_revision,
            payload,
            invocation_kind="callback",
            execution_context=execution_context,
        )
        with self.uow:
            current = self.records.callback_routes[route_id]
            self.records.callback_routes.save(
                current.model_copy(
                    update={
                        "status": "used",
                        "metadata": {
                            **current.metadata,
                            "invocation_id": invocation.id,
                        },
                    }
                )
            )
            self.store.create_edge(
                node_ref("CallbackRoute", id=route.id),
                "INVOKED",
                node_ref("Invocation", id=invocation.id),
            )
        return invocation

    def run_application_tests(
        self, application_revision_id: str
    ) -> list[Invocation]:
        with self.uow:
            app_revision = self.records.revisions[application_revision_id]
            if app_revision.status not in {"draft", "validating", "active"}:
                raise ImmutableRevisionError(
                    "tests require a draft, validating, or active revision"
                )
            tests = self.bindings.test_cases(app_revision.id)
            revisions = {
                test.id: self.bindings.action_revision(app_revision.id, test.action_id)
                for test in tests
            }
            execution_context = ExecutionContext(
                correlation_id=str(uuid.uuid4()),
                application_revision_id=app_revision.id,
                application_id=app_revision.application_id,
            )
        invocations = []
        for test in tests:
            invocation = self.runtime.run(
                revisions[test.id],
                test.input,
                invocation_kind="test",
                execution_context=execution_context,
            )
            invocations.append(invocation)
            if (
                invocation.status != "succeeded"
                or invocation.output != test.expected_output
            ):
                raise ValidationFailure(f"test failed: {test.id}")
        return invocations

    def run_migration(
        self, application_revision_id: str, action_revision_id: str
    ) -> Invocation:
        with self.uow:
            revision = self.records.action_revisions[action_revision_id]
            if revision.kind != "migration":
                raise ValidationFailure(
                    "migration execution requires ActionRevision.kind=migration"
                )
            app_revision = self.records.revisions[application_revision_id]
            input_value = dict(revision.migration_metadata.get("input", {}))
            execution_context = ExecutionContext(
                correlation_id=str(uuid.uuid4()),
                application_revision_id=app_revision.id,
                application_id=app_revision.application_id,
            )
        invocation = self.runtime.run(
            revision,
            input_value,
            invocation_kind="migration",
            execution_context=execution_context,
        )
        if invocation.status != "succeeded":
            raise ValidationFailure(
                f"migration execution failed: {revision.id}: {invocation.error}"
            )
        return invocation

    def validate_action_import(
        self, revision: ActionRevision, *, application_revision_id: str
    ) -> None:
        self.runtime.import_check(
            revision, application_revision_id=application_revision_id
        )

    def resolve_dependencies(self, application_revision_id: str) -> dict[str, Any]:
        action_revisions = self.bindings.action_revisions(application_revision_id)
        python = None
        if action_revisions:
            result = self.runtime.python_environments.prepare(
                application_revision_id
            )
            python = {
                "environment_id": result.environment.id,
                "lock_hash": result.environment.lock_hash,
            }
        return {"python": python}

    def build_revision_artifacts(
        self, application_revision_id: str
    ) -> dict[str, Any]:
        materialized_actions = []
        for revision in self.bindings.action_revisions(application_revision_id):
            path = self.runtime.materialize(
                revision, application_revision_id=application_revision_id
            )
            materialized_actions.append(
                {"revision_id": revision.id, "path": str(path)}
            )
        return {"actions": materialized_actions}

    @transactional
    def create_callback_route(
        self,
        application_id: str,
        target_action_revision_id: str,
        *,
        state: str,
        expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CallbackRoute:
        action_revision = self.records.action_revisions[target_action_revision_id]
        action = self.records.actions[action_revision.action_id]
        if action.application_id != application_id:
            raise ValueError(
                "callback target action belongs to a different application"
            )
        route = CallbackRoute(
            id=str(uuid.uuid4()),
            application_id=application_id,
            target_action_revision_id=target_action_revision_id,
            state=state,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        self.records.callback_routes.save(route)
        self.store.create_edge(
            node_ref("Application", id=application_id),
            "HAS_CALLBACK_ROUTE",
            node_ref("CallbackRoute", id=route.id),
        )
        self.store.create_edge(
            node_ref("CallbackRoute", id=route.id),
            "TARGETS",
            node_ref("ActionRevision", id=target_action_revision_id),
        )
        return route

    def inspect_callback_routes(
        self, application_id: str | None = None
    ) -> list[CallbackRoute]:
        return sorted(
            (
                route
                for route in self.records.callback_routes.values()
                if application_id is None or route.application_id == application_id
            ),
            key=lambda item: item.created_at,
        )

    def _draft_execution_context(
        self,
        app_revision,
        data_space_id: str | None,
    ) -> ExecutionContext:
        application_id = app_revision.application_id
        space_id = data_space_id or "production"
        data_space = self.records.data_spaces.get((application_id, space_id))
        if data_space is None:
            raise ValidationFailure(f"unknown DataSpace: {space_id}")
        if data_space.kind == "development":
            deployment = next(
                (
                    item
                    for item in self.records.development_deployments.values()
                    if item.data_space_ids.get(application_id) == space_id
                    and item.status == "active"
                ),
                None,
            )
            if deployment is None:
                raise ValidationFailure(
                    "development DataSpace requires an owning DevelopmentDeployment"
                )
            return ExecutionContext(
                mode="development",
                deployment_id=deployment.id,
                correlation_id=str(uuid.uuid4()),
                experience_revision_id=deployment.experience_revision_id,
                application_revision_id=app_revision.id,
                application_id=application_id,
                data_space_id=space_id,
            )
        return ExecutionContext(
            mode="production",
            correlation_id=str(uuid.uuid4()),
            application_revision_id=app_revision.id,
            application_id=application_id,
            data_space_id=space_id,
        )
