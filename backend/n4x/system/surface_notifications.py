from __future__ import annotations

from collections.abc import Callable

SurfaceCatalogListener = Callable[[str, str], None]


class SurfaceCatalogNotifier:
    """Process-local hook for MCP hosts to publish list-changed notifications."""

    def __init__(self) -> None:
        self._listeners: list[SurfaceCatalogListener] = []

    def subscribe(self, listener: SurfaceCatalogListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def activated(self, experience_id: str, revision_id: str) -> None:
        for listener in tuple(self._listeners):
            listener(experience_id, revision_id)
