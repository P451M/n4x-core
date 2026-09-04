"""Trigger jobs owned by the System."""

from __future__ import annotations

from typing import Any, Protocol

from n4x.graph.uow import GraphUnitOfWork
from n4x.graph.service_base import ServiceBase, transactional


class SchedulerPort(Protocol):
    def start(self) -> None: ...

    def shutdown(self) -> None: ...

    def run_trigger(self, *args, **kwargs): ...

    def dispatch_event(self, *args, **kwargs): ...

    def inspect(self) -> dict[str, Any]: ...

    def process_due_work(self) -> list[Any]: ...

    def recover_expired_leases(self) -> list[Any]: ...


class JobService(ServiceBase):
    def __init__(
        self, uow: GraphUnitOfWork, scheduler: SchedulerPort
    ) -> None:
        super().__init__(uow)
        self.scheduler = scheduler

    def start(self) -> None:
        self.scheduler.start()

    def shutdown(self) -> None:
        self.scheduler.shutdown()

    def run_trigger(
        self,
        trigger_id: str,
        input_value: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ):
        return self.scheduler.run_trigger(
            trigger_id, input_value, idempotency_key=idempotency_key
        )

    def dispatch_event(
        self, application_id: str, event_type: str, payload: dict[str, Any]
    ):
        return self.scheduler.dispatch_event(
            application_id, event_type, payload
        )

    def process_due_work(self):
        return self.scheduler.process_due_work()

    @transactional
    def recover_expired_leases(self):
        return self.scheduler.recover_expired_leases()

    def inspect(self) -> dict[str, Any]:
        return self.scheduler.inspect()
