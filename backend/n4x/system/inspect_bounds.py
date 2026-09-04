"""Bounds for authoring inspect tools. Not used by Experience product reads."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

INSPECT_DEFAULT_LIMIT = 50
INSPECT_HARD_CAP = 200

T = TypeVar("T")


def page_inspect(
    items: Sequence[T],
    *,
    offset: int = 0,
    limit: int = INSPECT_DEFAULT_LIMIT,
    include_values: bool = False,
    dump: Callable[[T], dict[str, Any]],
    summary: Callable[[T], dict[str, Any]],
    item_id: str | None = None,
    id_of: Callable[[T], str] | None = None,
) -> dict[str, Any]:
    if offset < 0:
        raise ValueError("inspect offset must be >= 0")
    if limit < 1:
        raise ValueError("inspect limit must be >= 1")
    selected = list(items)
    if item_id is not None:
        if id_of is None:
            raise ValueError("item_id requires id_of")
        selected = [item for item in selected if id_of(item) == item_id]
        include_values = True
    if limit > INSPECT_HARD_CAP:
        return {
            "status": "too_large",
            "error": f"limit exceeds {INSPECT_HARD_CAP}",
            "count": len(selected),
            "offset": offset,
            "limit": INSPECT_HARD_CAP,
            "items": [],
        }
    window = selected[offset : offset + limit]
    payload = [dump(item) if include_values else summary(item) for item in window]
    truncated = offset + len(window) < len(selected)
    return {
        "status": "truncated" if truncated else "ok",
        "count": len(selected),
        "offset": offset,
        "limit": limit,
        "items": payload,
    }


def model_summary(item: Any, *, drop: tuple[str, ...] = ("values",)) -> dict[str, Any]:
    data = item.model_dump(mode="json")
    for key in drop:
        data.pop(key, None)
    return data
