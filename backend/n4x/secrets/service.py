from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import wraps

from n4x.graph.store import GraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork
from n4x.kernel.errors import SecretNotFoundError
from n4x.kernel.models import CredentialRecord, SecretReference, now_utc
from n4x.secrets.backends import SecretBackend, default_secret_backend


def _transactional(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self.uow:
            return method(self, *args, **kwargs)

    return wrapper


@dataclass(frozen=True)
class SecretValue:
    uri: str
    value: str


class SecretService:
    def __init__(
        self,
        graph_store: GraphStore,
        backend: SecretBackend | None = None,
        uow: GraphUnitOfWork | None = None,
    ) -> None:
        self.backend = backend or default_secret_backend()
        self.store = graph_store
        self.uow = uow or GraphUnitOfWork(graph_store)
        self.graph = self.uow.records

    @_transactional
    def create_reference(
        self,
        application_id: str,
        uri: str,
        *,
        name: str = "",
        description: str = "",
    ) -> SecretReference:
        existing = self.find_reference_by_uri(uri)
        if existing:
            if existing.application_id != application_id:
                raise ValueError("secret URI is already owned by another Application")
            return existing
        reference = SecretReference(
            id=str(uuid.uuid4()),
            application_id=application_id,
            uri=uri,
            backend=self.backend.name,  # type: ignore[arg-type]
            name=name,
            description=description,
        )
        self.graph.secret_references[reference.id] = reference
        self.store.create_edge(
            node_ref("Application", id=application_id),
            "HAS_SECRET_REFERENCE",
            node_ref("SecretReference", id=reference.id),
        )
        return reference

    def set_secret(self, uri: str, value: str) -> SecretReference:
        self.uow.require_inactive("set secret backend value")
        with self.uow:
            reference = self.find_reference_by_uri(uri)
            if reference is None:
                raise KeyError(f"SecretReference does not exist for {uri}")
        self.backend.set(uri, value)
        with self.uow:
            current = self.graph.secret_references[reference.id]
            updated = current.model_copy(update={"updated_at": now_utc()})
            self.graph.secret_references[reference.id] = updated
            return updated

    def get_secret(self, uri: str) -> str:
        self.uow.require_inactive("get secret backend value")
        with self.uow:
            if self.find_reference_by_uri(uri) is None:
                raise KeyError(f"SecretReference does not exist for {uri}")
        return self.backend.get(uri)

    def delete_secret_value(self, uri: str) -> None:
        self.uow.require_inactive("delete secret backend value")
        with self.uow:
            reference = self.find_reference_by_uri(uri)
            if reference is None:
                raise KeyError(f"SecretReference does not exist for {uri}")
        self.backend.delete(uri)
        with self.uow:
            current = self.graph.secret_references[reference.id]
            self.graph.secret_references[reference.id] = current.model_copy(
                update={"updated_at": now_utc()}
            )

    def secret_status(
        self, application_id: str, reference_id: str
    ) -> dict[str, object]:
        self.uow.require_inactive("inspect secret backend status")
        reference = self._owned_reference(application_id, reference_id)
        try:
            self.backend.get(reference.uri)
            configured = True
        except SecretNotFoundError:
            configured = False
        return self._status_payload(reference, configured)

    def set_secret_reference(
        self, application_id: str, reference_id: str, value: str
    ) -> dict[str, object]:
        self.uow.require_inactive("set secret backend value")
        reference = self._owned_reference(application_id, reference_id)
        self.backend.set(reference.uri, value)
        with self.uow:
            current = self.graph.secret_references[reference.id]
            updated = current.model_copy(update={"updated_at": now_utc()})
            self.graph.secret_references[reference.id] = updated
        return self._status_payload(updated, True)

    def delete_secret_reference_value(
        self, application_id: str, reference_id: str
    ) -> dict[str, object]:
        self.uow.require_inactive("delete secret backend value")
        reference = self._owned_reference(application_id, reference_id)
        self.backend.delete(reference.uri)
        with self.uow:
            current = self.graph.secret_references[reference.id]
            updated = current.model_copy(update={"updated_at": now_utc()})
            self.graph.secret_references[reference.id] = updated
        return self._status_payload(updated, False)

    @_transactional
    def create_credential_record(
        self,
        application_id: str,
        provider: str,
        account_name: str,
        *,
        secret_reference_ids: list[str],
        metadata: dict | None = None,
    ) -> CredentialRecord:
        for reference_id in secret_reference_ids:
            if reference_id not in self.graph.secret_references:
                raise KeyError(reference_id)
        record = CredentialRecord(
            id=str(uuid.uuid4()),
            application_id=application_id,
            provider=provider,
            account_name=account_name,
            secret_reference_ids=secret_reference_ids,
            metadata=metadata or {},
        )
        self.graph.credential_records[record.id] = record
        self.store.create_edge(
            node_ref("Application", id=application_id),
            "HAS_CREDENTIAL",
            node_ref("CredentialRecord", id=record.id),
        )
        for reference_id in secret_reference_ids:
            self.store.create_edge(
                node_ref("CredentialRecord", id=record.id),
                "USES_SECRET",
                node_ref("SecretReference", id=reference_id),
            )
        return record

    def find_reference_by_uri(self, uri: str) -> SecretReference | None:
        return next(
            (
                reference
                for reference in self.graph.secret_references.values()
                if reference.uri == uri
            ),
            None,
        )

    def _owned_reference(
        self, application_id: str, reference_id: str
    ) -> SecretReference:
        with self.uow:
            reference = self.graph.secret_references.get(reference_id)
            if reference is None:
                raise KeyError(reference_id)
            if reference.application_id != application_id:
                raise PermissionError(
                    "SecretReference is not owned by the requested Application"
                )
            return reference

    @staticmethod
    def _status_payload(
        reference: SecretReference, configured: bool
    ) -> dict[str, object]:
        return {
            "secret_reference_id": reference.id,
            "configured": configured,
            "backend": reference.backend,
            "updated_at": reference.updated_at.isoformat(),
        }

    def allowed_secret_values(
        self,
        secret_refs: list[str],
        *,
        require_values: bool = True,
    ) -> list[SecretValue]:
        self.uow.require_inactive("read allowed secret backend values")
        with self.uow:
            references = []
            for reference_id in secret_refs:
                reference = self.graph.secret_references.get(reference_id)
                if reference is None:
                    raise KeyError(f"unknown secret reference: {reference_id}")
                references.append(reference)
        values: list[SecretValue] = []
        for reference in references:
            try:
                value = self.backend.get(reference.uri)
            except SecretNotFoundError:
                if require_values:
                    raise
                continue
            values.append(SecretValue(uri=reference.uri, value=value))
        return values
