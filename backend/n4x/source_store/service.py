from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from n4x.graph.bindings import RevisionBindings
from n4x.graph.intern_gc import delete_interned_orphans
from n4x.graph.store import GraphStore, node_ref
from n4x.graph.uow import GraphUnitOfWork, transactional
from n4x.kernel.errors import ImmutableRevisionError, SourceConflictError
from n4x.kernel.intern import empty_tree_hash, listing_hash, source_content_id
from n4x.kernel.models import (
    RevisionOwnerKind,
    SourceContent,
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
                f"invalid unified diff hunk header: {header.rstrip()}. "
                "apply_source_patch expects a unified diff "
                "(--- a/path, +++ b/path, @@ -1,2 +1,3 @@), "
                "not Cursor ApplyPatch (*** Begin Patch ***)."
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
        self.bindings = RevisionBindings(self.uow)

    def interned_empty_tree(self) -> SourceTree:
        tree_hash = empty_tree_hash()
        existing = self.graph.source_trees.get(tree_hash)
        if existing is not None:
            return existing
        tree = SourceTree(id=tree_hash, status="interned", tree_hash=tree_hash)
        self.graph.source_trees[tree.id] = tree
        return tree

    @transactional
    def create_working_tree(
        self,
        revision_id: str,
        *,
        owner_kind: RevisionOwnerKind,
    ) -> SourceTree:
        tree = SourceTree(
            id=f"{revision_id}.source",
            status="draft",
            tree_hash=empty_tree_hash(),
            owner_kind=owner_kind,
            owner_id=revision_id,
        )
        self.graph.source_trees[tree.id] = tree
        return tree

    def list_source_tree(self, source_tree_id: str) -> list[SourceFile]:
        files: list[SourceFile] = []
        for edge in self.store.list_edges(
            node_ref("SourceTree", id=source_tree_id), "HAS_FILE"
        ):
            content = self.graph.source_contents[edge.to_ref.identity["id"]]
            files.append(self._hydrate(source_tree_id, edge.props, content))
        return sorted(files, key=lambda file: file.path)

    def read_source_file(self, source_tree_id: str, path: str) -> SourceFile:
        for file in self.list_source_tree(source_tree_id):
            if file.path == path:
                return file
        raise KeyError((source_tree_id, path))

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
        context: int = 2,
    ) -> list[dict[str, object]]:
        if limit < 1:
            raise ValueError("search_source_tree limit must be >= 1")
        if context < 0:
            raise ValueError("search_source_tree context must be >= 0")
        cap = min(limit, SEARCH_SOURCE_TREE_LIMIT)
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid search pattern: {exc}") from exc
        matches: list[dict[str, object]] = []
        for file in self.list_source_tree(source_tree_id):
            if glob and not fnmatch.fnmatch(file.path, glob):
                continue
            lines = file.content.splitlines()
            for line_number, line in enumerate(lines, start=1):
                if compiled.search(line) is None:
                    continue
                start = max(0, line_number - 1 - context)
                end = min(len(lines), line_number + context)
                matches.append(
                    {
                        "path": file.path,
                        "line": line_number,
                        "snippet": line[:240],
                        "before": [item[:240] for item in lines[start : line_number - 1]],
                        "after": [item[:240] for item in lines[line_number:end]],
                    }
                )
                if len(matches) >= cap:
                    return matches
        return matches

    @transactional
    def ensure_writable(self, revision_id: str) -> SourceTree:
        current = self.bindings.tree(revision_id)
        if current.status == "draft":
            return current
        if current.status != "interned":
            raise ImmutableRevisionError(f"SourceTree {current.id} is not writable")
        working = SourceTree(
            id=f"{revision_id}.source",
            status="draft",
            tree_hash=current.tree_hash,
            owner_kind=self.bindings.label(revision_id),  # type: ignore[arg-type]
            owner_id=revision_id,
        )
        self.graph.source_trees[working.id] = working
        for file in self.list_source_tree(current.id):
            self._put_file(working.id, file)
        self.bindings.set_tree(revision_id, working.id)
        delete_interned_orphans(self.uow)
        return working

    @transactional
    def intern_tree(self, revision_id: str) -> SourceTree:
        current = self.bindings.tree(revision_id)
        files = [
            {
                "path": file.path,
                "hash": file.content_hash,
                "role": file.role,
                "language": file.language,
            }
            for file in self.list_source_tree(current.id)
        ]
        tree_hash = listing_hash(files)
        interned = self.graph.source_trees.get(tree_hash)
        if interned is None or interned.status != "interned":
            interned = SourceTree(
                id=tree_hash, status="interned", tree_hash=tree_hash
            )
            self.graph.source_trees[interned.id] = interned
            for file in self.list_source_tree(current.id):
                self._put_file(interned.id, file)
        if current.id != interned.id:
            self.bindings.set_tree(revision_id, interned.id)
            if current.status == "draft":
                self._delete_working_tree(current.id)
        delete_interned_orphans(self.uow)
        return interned

    @transactional
    def write_source_file(
        self,
        revision_id: str,
        path: str,
        content: str,
        *,
        role: SourceRole,
        language: str,
        expected_hash: str | None = None,
        actor: str = "system",
        tool: str = "kernel",
    ) -> SourceFile:
        return self.batch_update_revision(
            revision_id,
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
        revision_id: str,
        path: str,
        patch: str,
        *,
        expected_hash: str | None = None,
        actor: str = "system",
        tool: str = "kernel",
    ) -> SourceFile:
        tree = self.bindings.tree(revision_id)
        current = self.read_source_file(tree.id, path)
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
            revision_id,
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
        revision_id: str,
        path: str,
        new_path: str,
        *,
        expected_hash: str | None = None,
        actor: str = "system",
        tool: str = "kernel",
    ) -> SourceFile:
        return self.batch_update_revision(
            revision_id,
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
        revision_id: str,
        path: str,
        *,
        expected_hash: str | None = None,
        actor: str = "system",
        tool: str = "kernel",
    ) -> None:
        self.batch_update_revision(
            revision_id,
            [SourceUpdate("delete", path, expected_hash=expected_hash)],
            actor=actor,
            tool=tool,
        )

    @transactional
    def batch_update_revision(
        self,
        revision_id: str,
        changes: Iterable[SourceUpdate],
        *,
        expected_tree_hash: str | None = None,
        actor: str = "system",
        tool: str = "kernel",
    ) -> list[SourceFile]:
        current = self.bindings.tree(revision_id)
        if expected_tree_hash and current.tree_hash != expected_tree_hash:
            raise SourceConflictError(
                f"source tree conflict for {revision_id}: expected "
                f"{expected_tree_hash}, got {current.tree_hash}"
            )
        materialized = list(changes)
        self._validate_batch(current.id, materialized)
        tree = self.ensure_writable(revision_id)
        return self._batch_update_working_tree(
            tree.id, materialized, actor=actor, tool=tool
        )

    @transactional
    def _batch_update_working_tree(
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
                f"source tree conflict for {source_tree_id}: expected "
                f"{expected_tree_hash}, got {tree.tree_hash}"
            )
        materialized = list(changes)
        by_path = self._validate_batch(source_tree_id, materialized)

        written: list[SourceFile] = []
        for change in materialized:
            current = by_path.get(change.path)
            if change.operation == "write":
                assert change.content is not None
                file = self._write_blob(
                    source_tree_id,
                    change.path,
                    change.content,
                    role=change.role if current is None else current.role,
                    language=change.language if current is None else current.language,
                )
                by_path[change.path] = file
                written.append(file)
            elif change.operation == "delete":
                assert current is not None
                self._unlink_path(source_tree_id, change.path)
                del by_path[change.path]
            else:
                assert current is not None
                assert change.new_path is not None
                self._unlink_path(source_tree_id, change.path)
                del by_path[change.path]
                file = self._put_file(
                    source_tree_id,
                    current.model_copy(update={"path": change.new_path}),
                )
                by_path[change.new_path] = file
                written.append(file)
        self._refresh_tree_hash(source_tree_id)
        delete_interned_orphans(self.uow)
        return written

    def _validate_batch(
        self, source_tree_id: str, changes: list[SourceUpdate]
    ) -> dict[str, SourceFile]:
        by_path = {file.path: file for file in self.list_source_tree(source_tree_id)}
        for change in changes:
            current = by_path.get(change.path)
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
                if change.new_path in by_path:
                    raise SourceConflictError(
                        f"target path already exists: {change.new_path}"
                    )
        return by_path

    def _write_blob(
        self,
        source_tree_id: str,
        path: str,
        content: str,
        *,
        role: SourceRole,
        language: str,
    ) -> SourceFile:
        content_id = source_content_id(content)
        if self.graph.source_contents.get(content_id) is None:
            self.graph.source_contents[content_id] = SourceContent(
                id=content_id, content=content
            )
        file = SourceFile(
            source_tree_id=source_tree_id,
            path=path,
            role=role,
            language=language,
            content=content,
            content_hash=content_id,
            size=len(content.encode("utf-8")),
        )
        return self._put_file(source_tree_id, file)

    def _put_file(self, source_tree_id: str, file: SourceFile) -> SourceFile:
        self._unlink_path(source_tree_id, file.path)
        if self.graph.source_contents.get(file.content_hash) is None:
            self.graph.source_contents[file.content_hash] = SourceContent(
                id=file.content_hash, content=file.content
            )
        self.store.create_edge(
            node_ref("SourceTree", id=source_tree_id),
            "HAS_FILE",
            node_ref("SourceContent", id=file.content_hash),
            {
                "path": file.path,
                "role": file.role,
                "language": file.language,
                "size": file.size,
            },
        )
        return file.model_copy(update={"source_tree_id": source_tree_id})

    def _unlink_path(self, source_tree_id: str, path: str) -> None:
        self.store.delete_edge(
            node_ref("SourceTree", id=source_tree_id),
            "HAS_FILE",
            props={"path": path},
        )

    def delete_working_tree(self, tree_id: str) -> None:
        tree = self.graph.source_trees.get(tree_id)
        if tree is None:
            return
        if tree.status != "draft":
            raise ImmutableRevisionError(
                f"cannot delete interned SourceTree {tree_id}"
            )
        self._delete_working_tree(tree_id)

    def _delete_working_tree(self, tree_id: str) -> None:
        tree_ref = node_ref("SourceTree", id=tree_id)
        self.store.delete_edge(tree_ref, "HAS_FILE")
        self.graph.source_trees.delete(tree_id)

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
            update={"tree_hash": listing_hash(files), "updated_at": now_utc()}
        )

    @staticmethod
    def _hydrate(
        source_tree_id: str, props: dict[str, object], content: SourceContent
    ) -> SourceFile:
        return SourceFile(
            source_tree_id=source_tree_id,
            path=str(props["path"]),
            role=props.get("role", "helper"),  # type: ignore[arg-type]
            language=str(props.get("language", "text")),
            content=content.content,
            content_hash=content.id,
            size=int(props.get("size") or len(content.content.encode("utf-8"))),
        )
