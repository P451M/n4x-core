from __future__ import annotations

import pytest

from n4x.graph.repositories import _decode_json_values
from n4x.kernel.errors import ImmutableRevisionError, SourceConflictError
from n4x.kernel.models import Invocation, SourceFile
from n4x.source_store.service import SourceUpdate
from n4x.testing import create_test_runtime


def test_source_store_writes_and_conflicts() -> None:
    system = create_test_runtime()
    app = system.create_application("notes", "Notes")
    revision = system.create_application_revision(app.id)

    file = system.source.write_source_file(
        revision.source_tree_id,
        "actions/create_note.py",
        "def run(ctx, input):\n    return input\n",
        role="action",
        language="python",
    )

    with pytest.raises(SourceConflictError):
        system.source.write_source_file(
            revision.source_tree_id,
            "actions/create_note.py",
            "def run(ctx, input):\n    return {'bad': True}\n",
            role="action",
            language="python",
            expected_hash="sha256:not-current",
        )

    updated = system.source.write_source_file(
        revision.source_tree_id,
        "actions/create_note.py",
        "def run(ctx, input):\n    return {'title': input['title']}\n",
        role="action",
        language="python",
        expected_hash=file.content_hash,
    )
    assert updated.version == 2
    assert len(system.source.inspect_source_changes(revision.source_tree_id)) == 2


def test_apply_source_patch_applies_strict_unified_diff() -> None:
    system = create_test_runtime()
    app = system.create_application("patched", "Patched")
    revision = system.create_application_revision(app.id)
    source = system.source.write_source_file(
        revision.source_tree_id,
        "actions/run.py",
        "def run():\n    first()\n    second()\n    third()\n",
        role="action",
        language="python",
    )

    updated = system.source.apply_source_patch(
        revision.source_tree_id,
        "actions/run.py",
        (
            "--- a/actions/run.py\n"
            "+++ b/actions/run.py\n"
            "@@ -1,3 +1,4 @@\n"
            " def run():\n"
            "-    first()\n"
            "+    before()\n"
            "+    FIRST()\n"
            "     second()\n"
            "@@ -4,1 +5,1 @@\n"
            "-    third()\n"
            "+    THIRD()\n"
        ),
        expected_hash=source.content_hash,
    )

    assert updated.content == (
        "def run():\n    before()\n    FIRST()\n    second()\n    THIRD()\n"
    )


def test_apply_source_patch_preserves_no_final_newline() -> None:
    system = create_test_runtime()
    app = system.create_application("no-newline", "No newline")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "value.txt",
        "old",
        role="helper",
        language="text",
    )

    updated = system.source.apply_source_patch(
        revision.source_tree_id,
        "value.txt",
        (
            "@@ -1 +1 @@\n"
            "-old\n"
            "\\ No newline at end of file\n"
            "+new\n"
            "\\ No newline at end of file\n"
        ),
    )

    assert updated.content == "new"


def test_apply_source_patch_matches_unique_context_without_line_numbers() -> None:
    system = create_test_runtime()
    app = system.create_application("context", "Context")
    revision = system.create_application_revision(app.id)
    source = system.source.write_source_file(
        revision.source_tree_id,
        "mail.tsx",
        "alpha\nclassName=\"keep\"\nbeta\nclassName=\"old\"\ngamma\n",
        role="helper",
        language="typescript",
    )

    updated = system.source.apply_source_patch(
        revision.source_tree_id,
        "mail.tsx",
        "@@\n-className=\"old\"\n+className=\"new\"\n",
        expected_hash=source.content_hash,
    )

    assert updated.content == (
        "alpha\nclassName=\"keep\"\nbeta\nclassName=\"new\"\ngamma\n"
    )


def test_apply_source_patch_uses_line_numbers_as_hint_when_context_moved() -> None:
    system = create_test_runtime()
    app = system.create_application("hint", "Hint")
    revision = system.create_application_revision(app.id)
    source = system.source.write_source_file(
        revision.source_tree_id,
        "mail.tsx",
        "one\ntwo\nunique-target\nthree\n",
        role="helper",
        language="typescript",
    )

    updated = system.source.apply_source_patch(
        revision.source_tree_id,
        "mail.tsx",
        "@@ -1,1 +1,1 @@\n-unique-target\n+replaced\n",
        expected_hash=source.content_hash,
    )

    assert updated.content == "one\ntwo\nreplaced\nthree\n"


def test_apply_source_patch_conflicts_when_context_is_ambiguous() -> None:
    system = create_test_runtime()
    app = system.create_application("ambiguous", "Ambiguous")
    revision = system.create_application_revision(app.id)
    original = system.source.write_source_file(
        revision.source_tree_id,
        "mail.tsx",
        "className=\"dup\"\nmiddle\nclassName=\"dup\"\n",
        role="helper",
        language="typescript",
    )

    with pytest.raises(SourceConflictError) as captured:
        system.source.apply_source_patch(
            revision.source_tree_id,
            "mail.tsx",
            "@@\n-className=\"dup\"\n+className=\"one\"\n",
            expected_hash=original.content_hash,
        )

    assert captured.value.path == "mail.tsx"
    assert captured.value.current_hash == original.content_hash
    assert captured.value.failing_hunk is not None
    current = system.source.read_source_file(revision.source_tree_id, "mail.tsx")
    assert current.content == original.content

    with pytest.raises(SourceConflictError):
        system.source.apply_source_patch(
            revision.source_tree_id,
            "mail.tsx",
            "@@ -1,1 +1,1 @@\n-className=\"dup\"\n+className=\"one\"\n",
            expected_hash=original.content_hash,
        )
    current = system.source.read_source_file(revision.source_tree_id, "mail.tsx")
    assert current.content == original.content


def test_search_source_tree_and_ranged_read() -> None:
    system = create_test_runtime()
    app = system.create_application("search", "Search")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "mail.tsx",
        "overflow-hidden\nkeep\noverflow-auto\n",
        role="helper",
        language="typescript",
    )
    system.source.write_source_file(
        revision.source_tree_id,
        "sidebar.tsx",
        "overflow-hidden\n",
        role="helper",
        language="typescript",
    )
    system.source.write_source_file(
        revision.source_tree_id,
        "notes.py",
        "overflow-hidden\n",
        role="helper",
        language="python",
    )

    matches = system.source.search_source_tree(
        revision.source_tree_id, r"overflow-hidden", glob="*.tsx"
    )
    assert {(item["path"], item["line"]) for item in matches} == {
        ("mail.tsx", 1),
        ("sidebar.tsx", 1),
    }

    ranged = system.source.read_source_file_range(
        revision.source_tree_id, "mail.tsx", offset=2, limit=1
    )
    assert ranged["content"] == "keep\n"
    assert ranged["total_lines"] == 3
    assert ranged["offset"] == 2


@pytest.mark.parametrize(
    "patch",
    [
        "@@ -1,1 +1,1 @@\n-missing\n+replacement\n",
        "--- a/value.txt\n@@ -1 +1 @@\n-original\n+replacement\n",
        "not a unified diff\n",
    ],
)
def test_apply_source_patch_rejects_conflicts_without_writing(patch: str) -> None:
    system = create_test_runtime()
    app = system.create_application("conflict", "Conflict")
    revision = system.create_application_revision(app.id)
    original = system.source.write_source_file(
        revision.source_tree_id,
        "value.txt",
        "original\n",
        role="helper",
        language="text",
    )

    with pytest.raises(SourceConflictError):
        system.source.apply_source_patch(
            revision.source_tree_id,
            "value.txt",
            patch,
            expected_hash=original.content_hash,
        )

    current = system.source.read_source_file(revision.source_tree_id, "value.txt")
    assert current.content == original.content
    assert current.version == original.version


def test_batch_update_is_atomic() -> None:
    system = create_test_runtime()
    app = system.create_application("tasks", "Tasks")
    revision = system.create_application_revision(app.id)
    trees = system.uow.records.source_trees
    initial_tree_hash = trees[revision.source_tree_id].tree_hash

    with pytest.raises(SourceConflictError):
        system.source.batch_update_source_files(
            revision.source_tree_id,
            [
                SourceUpdate(
                    "write",
                    "actions/a.py",
                    content="def run(ctx, input): return 1",
                    role="action",
                    language="python",
                ),
                SourceUpdate("delete", "missing.py"),
            ],
            expected_tree_hash=initial_tree_hash,
        )

    assert system.source.list_source_tree(revision.source_tree_id) == []
    assert (
        trees[revision.source_tree_id].tree_hash
        == initial_tree_hash
    )


def test_immutable_snapshot_rejects_source_writes() -> None:
    system = create_test_runtime()
    app = system.create_application("tasks", "Tasks")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.source_tree_id,
        "actions/hello.py",
        "def run(ctx, input): return {'ok': True}",
        role="action",
        language="python",
    )
    snapshot = system.source.snapshot_tree(revision.source_tree_id, revision.id)

    with pytest.raises(ImmutableRevisionError):
        system.source.write_source_file(
            snapshot.id,
            "actions/hello.py",
            "def run(ctx, input): return {'ok': False}",
            role="action",
            language="python",
        )


def test_clone_tree_to_draft_copies_graph_source_without_reusing_snapshot() -> None:
    system = create_test_runtime()
    app = system.create_application("tasks", "Tasks")
    revision = system.create_application_revision(app.id)
    original = system.source.write_source_file(
        revision.source_tree_id,
        "actions/hello.py",
        "def run(ctx, input): return {'ok': True}",
        role="action",
        language="python",
    )
    snapshot = system.source.snapshot_tree(revision.source_tree_id, revision.id)

    draft = system.source.clone_tree_to_draft(snapshot.id, app.id, "tasks@2")
    copied = system.source.read_source_file(draft.id, "actions/hello.py")
    updated = system.source.write_source_file(
        draft.id,
        "actions/hello.py",
        "def run(ctx, input): return {'ok': False}",
        role="action",
        language="python",
    )

    assert draft.status == "draft"
    assert copied.content == original.content
    assert copied.content_hash == original.content_hash
    assert updated.content_hash != original.content_hash
    assert (
        system.source.read_source_file(snapshot.id, "actions/hello.py").content
        == original.content
    )


def test_source_file_json_object_content_round_trips() -> None:
    system = create_test_runtime()
    experience = system.create_experience("json-ui", "JSON UI")
    revision = system.create_experience_revision(experience.id, ui_profile="none")
    payload = '{"name":"Office","display":"standalone"}'
    written = system.source.write_source_file(
        revision.source_tree_id,
        "public/manifest.webmanifest",
        payload,
        role="surface",
        language="json",
    )
    read = system.source.read_source_file(
        revision.source_tree_id, "public/manifest.webmanifest"
    )
    listed = {
        item.path: item
        for item in system.source.list_source_tree(revision.source_tree_id)
    }
    assert written.content == payload
    assert read.content == payload
    assert listed["public/manifest.webmanifest"].content == payload
    assert isinstance(read.content, str)


def test_graph_decode_keeps_string_content_and_parses_any_output() -> None:
    source = _decode_json_values(
        {"content": '{"name":"Office"}'},
        SourceFile,
    )
    invocation = _decode_json_values(
        {
            "output": '{"ok":true}',
            "input": '{"title":"n"}',
            "stdout": '{"not":"parsed"}',
        },
        Invocation,
    )
    assert source["content"] == '{"name":"Office"}'
    assert invocation["output"] == {"ok": True}
    assert invocation["input"] == {"title": "n"}
    assert invocation["stdout"] == '{"not":"parsed"}'
