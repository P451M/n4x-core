from __future__ import annotations

from pathlib import Path, PurePath


class PathContainmentError(ValueError):
    """Raised when a relative path escapes its assigned runtime root."""


def resolve_path_within(
    root: Path, relative_path: str, *, allow_root: bool = False
) -> Path:
    """Resolve an untrusted relative path beneath a trusted root."""
    if not isinstance(relative_path, str):
        raise PathContainmentError("path must be a non-empty string")
    if not relative_path:
        if allow_root:
            return root.resolve()
        raise PathContainmentError("path must be a non-empty string")
    if "\x00" in relative_path or "\\" in relative_path:
        raise PathContainmentError("path contains unsupported characters")

    relative = PurePath(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise PathContainmentError("path must be a normalized relative path")

    resolved_root = root.resolve()
    resolved = (resolved_root / relative_path).resolve()
    if resolved_root != resolved and resolved_root not in resolved.parents:
        raise PathContainmentError("path escapes its assigned root")
    return resolved
