from __future__ import annotations

import difflib
import fnmatch
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from n4x.graph.store import GraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork, transactional
from n4x.kernel.errors import ImmutableRevisionError, SourceConflictError
from n4x.kernel.hash import sha256_json, sha256_text
from n4x.kernel.models import (
    RevisionOwnerKind,
    SourceChange,
    SourceFile,
    SourceRole,
    SourceTree,
    now_utc,
)

SEARCH_SOURCE_TREE_LIMIT = 50


@dataclass(frozen=True)
class SourceUpdate:
    operation: Literal["write", "delete", "rename"]
    path: str
    content: str | None = None
    new_path: str | None = None
    role: SourceRole = "helper"
    language: str = "text"
    expected_hash: str | None = None


_UNIFIED_HUNK = re.compile(
    r"^@@(?: -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@)?(?: .*)?(?:\r?\n)?$"
)
_NEAREST_WINDOW = 4


def _hunk_text(header: str, entries: list[tuple[str, str, bool]]) -> str:
    body = "".join(
        f"{kind}{text}"
        if text.endswith(("\n", "\r")) or not text
        else f"{kind}{text}\n"
        for kind, text, _ in entries
    )
    return f"{header}{body}"


def _nearest_lines(source: list[str], hint: int | None, needle: str | None) -> list[str]:
    if hint is not None and 0 <= hint < len(source):
        start = max(0, hint - _NEAREST_WINDOW)
        end = min(len(source), hint + _NEAREST_WINDOW + 1)
        return [line.rstrip("\r\n") for line in source[start:end]]
    if needle:
        for index, line in enumerate(source):
            if line == needle:
                start = max(0, index - _NEAREST_WINDOW)
                end = min(len(source), index + _NEAREST_WINDOW + 1)
                return [item.rstrip("\r\n") for item in source[start:end]]
    return [line.rstrip("\r\n") for line in source[: _NEAREST_WINDOW * 2]]


def _find_unique_hunk_start(
    source: list[str],
    source_index: int,
    old_lines: list[str],
    hinted_start: int | None,
) -> int | None:
    if not old_lines:
        if hinted_start is not None and hinted_start >= source_index:
            return hinted_start
        return None
    matches: list[int] = []
    last = len(source) - len(old_lines)
    for candidate in range(source_index, last + 1):
        if source[candidate : candidate + len(old_lines)] == old_lines:
            matches.append(candidate)
    if len(matches) == 1:
        return matches[0]
    return None


def _apply_unified_diff(content: str, patch: str) -> str:
    """Apply a unified diff using unique context; line numbers are a hint."""
    source = content.splitlines(keepends=True)
    lines = patch.splitlines(keepends=True)
    if not lines:
        raise SourceConflictError("source patch is empty")

    index = 0
    if lines[index].startswith("--- "):
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise SourceConflictError("source patch is missing the +++ header")
        index += 1
    elif lines[index].startswith("+++ "):
        raise SourceConflictError("source patch is missing the --- header")

    output: list[str] = []
    source_index = 0
    hunk_count = 0
    while index < len(lines):
        header = lines[index]
        match = _UNIFIED_HUNK.match(header)
        if match is None:
            raise SourceConflictError(
                f"invalid unified diff hunk header: {header.rstrip()}"
            )
        index += 1
        hunk_count += 1
        hinted_start = None
        if match.group("old_start") is not None:
            old_start = int(match.group("old_start"))
            hinted_start = 0 if old_start == 0 else old_start - 1

        entries: list[tuple[str, str, bool]] = []
        while index < len(lines) and not lines[index].startswith("@@"):
            line = lines[index]
            if line.startswith(("--- ", "+++ ")):
                raise SourceConflictError("source patch contains multiple file sections")
            if line.startswith("\\ No newline at end of file"):
                if not entries:
                    raise SourceConflictError("orphan no-newline marker in source patch")
                kind, text, _ = entries[-1]
                entries[-1] = (kind, text, True)
                index += 1
                continue
            if not line or line[0] not in {" ", "+", "-"}:
                raise SourceConflictError(
                    f"invalid unified diff line: {line.rstrip()}"
                )
            entries.append((line[0], line[1:], False))
            index += 1

        old_lines: list[str] = []
        for kind, raw_text, no_newline in entries:
            text = raw_text.rstrip("\r\n") if no_newline else raw_text
            if kind in {" ", "-"}:
                old_lines.append(text)

        hunk_start = _find_unique_hunk_start(
            source, source_index, old_lines, hinted_start
        )
        if hunk_start is None:
            raise SourceConflictError(
                "source patch context does not uniquely match",
                failing_hunk=_hunk_text(header, entries),
                nearest_lines=_nearest_lines(
                    source,
                    hinted_start,
                    old_lines[0] if old_lines else None,
                ),
            )
        output.extend(source[source_index:hunk_start])
        source_index = hunk_start

        for kind, raw_text, no_newline in entries:
            text = raw_text.rstrip("\r\n") if no_newline else raw_text
            if kind in {" ", "-"}:
                source_index += 1
            if kind in {" ", "+"}:
                output.append(text)

    if hunk_count == 0:
        raise SourceConflictError("source patch contains no hunks")
    output.extend(source[source_index:])
    return "".join(output)


class SourceStore:
    def __init__(
        self,
        graph_store: GraphStore,
        uow: GraphUnitOfWork | None = None,
    ) -> None:
        self.store = graph_store
        self.uow = uow or GraphUnitOfWork(graph_store)
        self.graph = self.uow.records

    @transactional
    def create_tree(
        self,
        root_namespace: str,
        revision_id: str,
        status: str = "draft",
        *,
        owner_kind: RevisionOwnerKind = "ApplicationRevision",
    ) -> SourceTree:
        tree = SourceTree(
            id=f"{revision_id}.source",
            owner_kind=owner_kind,
            owner_id=revision_id,
            draft_or_revision_id=revision_id,
            status=status,  # type: ignore[arg-type]
            root_namespace=root_namespace,
            tree_hash=sha256_json({}),
        )
        self.graph.source_trees[tree.id] = tree
        return tree

    @transactional
    def snapshot_tree(self, source_tree_id: str, revision_id: str) -> SourceTree:
        source = self.graph.source_trees[source_tree_id]
        snapshot = SourceTree(
            id=f"{revision_id}.source.snapshot",
            owner_kind=source.owner_kind,
            owner_id=revision_id,
            draft_or_revision_id=revision_id,
            status="immutable_snapshot",
            root_namespace=source.root_namespace,
            tree_hash=source.tree_hash,
            derived_from_tree_id=source_tree_id,
        )
        self.graph.source_trees[snapshot.id] = snapshot
        self.store.create_edge(
            node_ref("SourceTree", id=snapshot.id),
            "SNAPSHOT_OF",
            node_ref("SourceTree", id=source_tree_id),
        )
        for file in self.list_source_tree(source_tree_id):
            copied = file.model_copy(update={"source_tree_id": snapshot.id})
            self.graph.source_files[(snapshot.id, copied.path)] = copied
            self._link_source_file(snapshot.id, copied.path)
        return snapshot

    @transactional
    def clone_tree_to_draft(
        self,
        source_tree_id: str,
        root_namespace: str,
        revision_id: str,
        *,
        owner_kind: RevisionOwnerKind | None = None,
        exclude_paths: set[str] | None = None,
    ) -> SourceTree:
        source = self.graph.source_trees[source_tree_id]
        draft = SourceTree(
            id=f"{revision_id}.source",
            owner_kind=owner_kind or source.owner_kind,
            owner_id=revision_id,
            draft_or_revision_id=revision_id,
            status="draft",
            root_namespace=root_namespace,
            tree_hash=source.tree_hash,
            derived_from_tree_id=source_tree_id,
        )
        self.graph.source_trees[draft.id] = draft
        self.store.create_edge(
            node_ref("SourceTree", id=draft.id),
            "CLONED_FROM",
            node_ref("SourceTree", id=source_tree_id),
        )
        for file in self.list_source_tree(source_tree_id):
            if exclude_paths and file.path in exclude_paths:
                continue
            copied = file.model_copy(update={"source_tree_id": draft.id})
            self.graph.source_files[(draft.id, copied.path)] = copied
            self._link_source_file(draft.id, copied.path)
        if exclude_paths:
            self._refresh_tree_hash(draft.id)
        return draft

    def list_source_tree(self, source_tree_id: str) -> list[SourceFile]:
        return sorted(
            [
                file
                for (tree_id, _), file in self.graph.source_files.items()
                if tree_id == source_tree_id
            ],
            key=lambda file: file.path,
        )

    def read_source_file(self, source_tree_id: str, path: str) -> SourceFile:
        return self.graph.source_files[(source_tree_id, path)]

    def read_source_file_range(
        self,
        source_tree_id: str,
        path: str,
        *,
        offset: int | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        file = self.read_source_file(source_tree_id, path)
        lines = file.content.splitlines(keepends=True)
        total_lines = len(lines)
        start = 0 if offset is None else offset - 1
        if offset is not None and offset < 1:
            raise ValueError("read_source_file offset must be >= 1")
        if limit is not None and limit < 1:
            raise ValueError("read_source_file limit must be >= 1")
        end = total_lines if limit is None else start + limit
        payload = file.model_dump(mode="json")
        payload["content"] = "".join(lines[start:end])
        payload["total_lines"] = total_lines
        payload["offset"] = 1 if offset is None else offset
        payload["limit"] = limit
        return payload

    def search_source_tree(
        self,
        source_tree_id: str,
        pattern: str,
        *,
        glob: str | None = None,
        limit: int = SEARCH_SOURCE_TREE_LIMIT,
    ) -> list[dict[str, object]]:
        if limit < 1:
            raise ValueError("search_source_tree limit must be >= 1")
        cap = min(limit, SEARCH_SOURCE_TREE_LIMIT)
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid search pattern: {exc}") from exc
        matches: list[dict[str, object]] = []
        for file in self.list_source_tree(source_tree_id):
            if glob and not fnmatch.fnmatch(file.path, glob):
                continue
            for line_number, line in enumerate(file.content.splitlines(), start=1):
                if compiled.search(line) is None:
                    continue
                matches.append(
                    {
                        "path": file.path,
                        "line": line_number,
                        "snippet": line[:240],
                    }
                )
                if len(matches) >= cap:
                    return matches
        return matches

    @transactional
    def write_source_file(
        self,
        source_tree_id: str,
        path: str,
        content: str,
        *,
        role: SourceRole,
        language: str,
        expected_hash: str | None = None,
        actor: str = "system",
        tool: str = "kernel",
    ) -> SourceFile:
        return self.batch_update_source_files(
            source_tree_id,
            [
                SourceUpdate(
                    "write",
                    path,
                    content=content,
                    role=role,
                    language=language,
                    expected_hash=expected_hash,
                )
            ],
            actor=actor,
            tool=tool,
        )[0]

    @transactional
    def apply_source_patch(
        self,
        source_tree_id: str,
        path: str,
        patch: str,
        *,
        expected_hash: str | None = None,
        actor: str = "system",
        tool: str = "kernel",
    ) -> SourceFile:
        current = self.read_source_file(source_tree_id, path)
        if expected_hash and current.content_hash != expected_hash:
            raise SourceConflictError(
                f"source conflict for {path}: expected {expected_hash}, got {current.content_hash}",
                path=path,
                expected_hash=expected_hash,
                current_hash=current.content_hash,
            )
        try:
            patched = _apply_unified_diff(current.content, patch)
        except SourceConflictError as exc:
            raise SourceConflictError(
                str(exc),
                path=path,
                expected_hash=expected_hash or current.content_hash,
                current_hash=current.content_hash,
                failing_hunk=exc.failing_hunk,
                nearest_lines=exc.nearest_lines,
            ) from exc
        return self.write_source_file(
            source_tree_id,
            path,
            patched,
            role=current.role,
            language=current.language,
            expected_hash=current.content_hash,
            actor=actor,
            tool=tool,
        )

    @transactional
    def rename_source_file(
        self,
        source_tree_id: str,
        path: str,
        new_path: str,
        *,
        expected_hash: str | None = None,
        actor: str = "system",
        tool: str = "kernel",
    ) -> SourceFile:
        return self.batch_update_source_files(
            source_tree_id,
            [
                SourceUpdate(
                    "rename", path, new_path=new_path, expected_hash=expected_hash
                )
            ],
            actor=actor,
            tool=tool,
        )[0]

    @transactional
    def delete_source_file(
        self,
        source_tree_id: str,
        path: str,
        *,
        expected_hash: str | None = None,
        actor: str = "system",
        tool: str = "kernel",
    ) -> None:
        self.batch_update_source_files(
            source_tree_id,
            [SourceUpdate("delete", path, expected_hash=expected_hash)],
            actor=actor,
            tool=tool,
        )

    @transactional
    def batch_update_source_files(
        self,
        source_tree_id: str,
        changes: Iterable[SourceUpdate],
        *,
        expected_tree_hash: str | None = None,
        actor: str = "system",
        tool: str = "kernel",
    ) -> list[SourceFile]:
        tree = self.graph.source_trees[source_tree_id]
        if tree.status != "draft":
            raise ImmutableRevisionError(f"SourceTree {source_tree_id} is immutable")
        if expected_tree_hash and tree.tree_hash != expected_tree_hash:
            raise SourceConflictError(
                f"source tree conflict for {source_tree_id}: expected {expected_tree_hash}, got {tree.tree_hash}"
            )

        materialized_changes = list(changes)
        # Validate first so the operation is atomic.
        for change in materialized_changes:
            current = self.graph.source_files.get((source_tree_id, change.path))
            if change.expected_hash and (
                current is None or current.content_hash != change.expected_hash
            ):
                got = None if current is None else current.content_hash
                raise SourceConflictError(
                    f"source conflict for {change.path}: expected {change.expected_hash}, got {got}"
                )
            if change.operation in {"delete", "rename"} and current is None:
                raise SourceConflictError(f"source path does not exist: {change.path}")
            if change.operation == "rename":
                if not change.new_path:
                    raise ValueError("rename requires new_path")
                if (source_tree_id, change.new_path) in self.graph.source_files:
                    raise SourceConflictError(
                        f"target path already exists: {change.new_path}"
                    )

        group_id = str(uuid.uuid4())
        written: list[SourceFile] = []
        for change in materialized_changes:
            key = (source_tree_id, change.path)
            current = self.graph.source_files.get(key)
            old_hash = None if current is None else current.content_hash
            op = (
                "modify"
                if change.operation == "write" and current
                else "add"
                if change.operation == "write"
                else change.operation
            )

            if change.operation == "write":
                assert change.content is not None
                content_hash = sha256_text(change.content)
                file = SourceFile(
                    source_tree_id=source_tree_id,
                    path=change.path,
                    role=change.role if current is None else current.role,
                    language=change.language if current is None else current.language,
                    content=change.content,
                    content_hash=content_hash,
                    size=len(change.content.encode("utf-8")),
                    version=1 if current is None else current.version + 1,
                    created_at=now_utc() if current is None else current.created_at,
                    updated_at=now_utc(),
                )
                self.graph.source_files[key] = file
                self._link_source_file(source_tree_id, file.path)
                written.append(file)
                new_hash = content_hash
            elif change.operation == "delete":
                del self.graph.source_files[key]
                self._unlink_source_file(source_tree_id, change.path)
                new_hash = None
            else:
                assert current is not None
                assert change.new_path is not None
                del self.graph.source_files[key]
                file = current.model_copy(
                    update={
                        "path": change.new_path,
                        "version": current.version + 1,
                        "updated_at": now_utc(),
                    }
                )
                self.graph.source_files[(source_tree_id, change.new_path)] = file
                self._unlink_source_file(source_tree_id, change.path)
                self._link_source_file(source_tree_id, file.path)
                written.append(file)
                new_hash = file.content_hash

            source_change = SourceChange(
                id=str(uuid.uuid4()),
                source_tree_id=source_tree_id,
                change_group_id=group_id,
                operation=op,  # type: ignore[arg-type]
                path=change.new_path
                if change.operation == "rename" and change.new_path
                else change.path,
                old_path=change.path if change.operation == "rename" else None,
                old_hash=old_hash,
                new_hash=new_hash,
                actor=actor,
                tool=tool,
            )
            self.graph.source_changes.append(source_change)
            self.store.create_edge(
                node_ref("SourceTree", id=source_tree_id),
                "HAS_CHANGE",
                node_ref("SourceChange", id=source_change.id),
            )

        self._refresh_tree_hash(source_tree_id)
        return written

    def render_source_diff(
        self, source_tree_id: str, path: str, new_content: str
    ) -> str:
        current = self.read_source_file(source_tree_id, path).content
        return "".join(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=path,
                tofile=path,
            )
        )

    def inspect_source_changes(self, source_tree_id: str) -> list[SourceChange]:
        return [
            change
            for change in self.graph.source_changes.values()
            if change.source_tree_id == source_tree_id
        ]

    def _refresh_tree_hash(self, source_tree_id: str) -> None:
        files = [
            {
                "path": file.path,
                "hash": file.content_hash,
                "role": file.role,
                "language": file.language,
            }
            for file in self.list_source_tree(source_tree_id)
        ]
        tree = self.graph.source_trees[source_tree_id]
        self.graph.source_trees[source_tree_id] = tree.model_copy(
            update={"tree_hash": sha256_json(files), "updated_at": now_utc()}
        )

    def _link_source_file(self, source_tree_id: str, path: str) -> None:
        self.store.create_edge(
            node_ref("SourceTree", id=source_tree_id),
            "HAS_FILE",
            node_ref("SourceFile", source_tree_id=source_tree_id, path=path),
        )

    def _unlink_source_file(self, source_tree_id: str, path: str) -> None:
        self.store.delete_edge(
            node_ref("SourceTree", id=source_tree_id),
            "HAS_FILE",
            node_ref("SourceFile", source_tree_id=source_tree_id, path=path),
        )
