"""Application object inspection owned by the System."""

from __future__ import annotations

from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.models import ApplicationObject
from n4x.graph.service_base import transactional


class Objects:
    def __init__(self, uow: GraphUnitOfWork) -> None:
        self.uow = uow

    @transactional
    def list(
        self,
        application_id: str,
        object_type_id: str | None = None,
        *,
        data_space_id: str = "production",
    ) -> list[ApplicationObject]:
        return sorted(
            self.uow.objects.list(
                application_id,
                object_type_id,
                data_space_id=data_space_id,
            ),
            key=lambda item: item.created_at,
        )
