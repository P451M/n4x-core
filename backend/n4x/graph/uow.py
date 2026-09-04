from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from types import TracebackType
from typing import Any

from n4x.graph.repositories import (
    ApplicationRepository,
    ArtifactRepository,
    DefinitionRepository,
    ExperienceRepository,
    SystemRepository,
    JobRepository,
    ObjectRepository,
    RelationRepository,
    RepositoryRecords,
    SourceRepository,
)
from n4x.graph.store import GraphStore, node_ref
from n4x.kernel.errors import GraphUnitOfWorkError


@dataclass
class _UnitOfWorkState:
    transaction: Any
    depth: int = 1
    rollback_only: bool = False


class GraphUnitOfWork:
    """Owns one store transaction and repositories bound to that transaction."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self._state: ContextVar[_UnitOfWorkState | None] = ContextVar(
            f"n4x_graph_uow_{id(self)}", default=None
        )
        self.applications = ApplicationRepository(self)
        self.systems = SystemRepository(self)
        self.experiences = ExperienceRepository(self)
        self.definitions = DefinitionRepository(self)
        self.source = SourceRepository(self)
        self.objects = ObjectRepository(self)
        self.relations = RelationRepository(self)
        self.jobs = JobRepository(self)
        self.artifacts = ArtifactRepository(self)
        self.records = RepositoryRecords(self)

    @property
    def is_active(self) -> bool:
        return self._state.get() is not None

    @property
    def depth(self) -> int:
        state = self._state.get()
        return 0 if state is None else state.depth

    def require_active(self) -> None:
        if not self.is_active:
            raise GraphUnitOfWorkError(
                "repository operation requires an active GraphUnitOfWork"
            )

    def require_inactive(self, operation: str) -> None:
        """Reject external waits without altering caller-owned transaction scope."""
        if self.is_active:
            raise GraphUnitOfWorkError(
                f"{operation} requires an inactive GraphUnitOfWork"
            )

    def __enter__(self) -> GraphUnitOfWork:
        state = self._state.get()
        if state is not None:
            state.depth += 1
            return self
        transaction = self.store.transaction()
        transaction.__enter__()
        self._state.set(_UnitOfWorkState(transaction=transaction))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        state = self._state.get()
        if state is None:
            raise GraphUnitOfWorkError("GraphUnitOfWork is not active")
        if exc_type is not None:
            state.rollback_only = True
        state.depth -= 1
        if state.depth > 0:
            return False

        self._state.set(None)
        if exc_type is not None:
            state.transaction.__exit__(exc_type, exc_value, traceback)
            return False
        if state.rollback_only:
            rollback_error = GraphUnitOfWorkError(
                "nested GraphUnitOfWork failure marked the transaction for rollback"
            )
            state.transaction.__exit__(
                GraphUnitOfWorkError, rollback_error, rollback_error.__traceback__
            )
            raise rollback_error
        state.transaction.__exit__(None, None, None)
        return False

    def begin(self) -> _BegunUnitOfWork:
        self.__enter__()
        return _BegunUnitOfWork(self)

    def commit(self) -> None:
        self.__exit__(None, None, None)

    def rollback(self) -> None:
        state = self._state.get()
        if state is None:
            raise GraphUnitOfWorkError("GraphUnitOfWork is not active")
        state.rollback_only = True
        if state.depth > 1:
            state.depth -= 1
            return
        self._state.set(None)
        rollback_error = GraphUnitOfWorkError("GraphUnitOfWork rolled back")
        state.transaction.__exit__(
            GraphUnitOfWorkError, rollback_error, rollback_error.__traceback__
        )


class _BegunUnitOfWork:
    """Context token for a transaction already opened by begin()."""

    def __init__(self, uow: GraphUnitOfWork) -> None:
        self.uow = uow

    def __enter__(self) -> GraphUnitOfWork:
        return self.uow

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return self.uow.__exit__(exc_type, exc_value, traceback)


def transactional(method):
    """Run a graph mutation after acquiring the global write lock."""

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        outermost = not self.uow.is_active
        with self.uow:
            if outermost:
                self.uow.store.acquire_write_lock(node_ref("N4XRoot", id="n4x"))
            return method(self, *args, **kwargs)

    return wrapper
