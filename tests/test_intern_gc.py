from __future__ import annotations

from n4x.kernel.intern import source_content_id
from n4x.testing import create_test_runtime, tree_id


def test_discard_collects_unique_interned_source() -> None:
    system = create_test_runtime()
    app = system.create_application("scratch", "Scratch")
    revision = system.create_application_revision(app.id)
    content = "def run(ctx, input): return {'unique': True}\n"
    system.source.write_source_file(
        revision.id,
        "actions/unique.py",
        content,
        role="action",
        language="python",
    )
    working = tree_id(system, revision)
    blob_id = source_content_id(content)
    assert system.graph.source_contents.get(blob_id) is not None

    system.discard_application_revision(revision.id)

    assert system.graph.revisions.get(revision.id) is None
    assert system.graph.source_trees.get(working) is None
    assert system.graph.source_contents.get(blob_id) is None


def test_shared_intern_survives_discard() -> None:
    system = create_test_runtime()
    content = "def run(ctx, input): return {'shared': True}\n"
    first_app = system.create_application("keep", "Keep")
    first = system.create_application_revision(first_app.id)
    system.source.write_source_file(
        first.id,
        "actions/shared.py",
        content,
        role="action",
        language="python",
    )
    interned = system.source.intern_tree(first.id)

    other_app = system.create_application("drop", "Drop")
    draft = system.create_application_revision(other_app.id)
    system.source.write_source_file(
        draft.id,
        "actions/shared.py",
        content,
        role="action",
        language="python",
    )
    system.discard_application_revision(draft.id)

    blob_id = source_content_id(content)
    assert tree_id(system, first) == interned.id
    assert system.graph.source_trees.get(interned.id) is not None
    assert system.graph.source_contents[blob_id].content == content


def test_replaced_action_revision_is_collected() -> None:
    system = create_test_runtime()
    app = system.create_application("tasks", "Tasks")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.id,
        "actions/hello.py",
        "def run(ctx, input): return {'ok': True}\n",
        role="action",
        language="python",
    )
    first = system.create_action(
        revision.id,
        "tasks.hello",
        kind="normal",
        entrypoint="actions/hello.py:run",
        source_paths=["actions/hello.py"],
    )
    second = system.create_action(
        revision.id,
        "tasks.hello",
        kind="normal",
        entrypoint="actions/hello.py:other",
        source_paths=["actions/hello.py"],
    )
    assert first.id != second.id
    assert system.graph.action_revisions.get(first.id) is None
    assert system.graph.action_revisions[second.id].entrypoint.endswith(":other")


def test_callback_route_pins_replaced_action_revision() -> None:
    system = create_test_runtime()
    app = system.create_application("mail", "Mail")
    revision = system.create_application_revision(app.id)
    system.source.write_source_file(
        revision.id,
        "actions/echo.py",
        "def run(ctx, input): return input\n",
        role="action",
        language="python",
    )
    first = system.create_action(
        revision.id,
        "mail.echo",
        kind="normal",
        entrypoint="actions/echo.py:run",
        source_paths=["actions/echo.py"],
    )
    system.create_callback_route("mail", first.id, state="oauth")
    second = system.create_action(
        revision.id,
        "mail.echo",
        kind="normal",
        entrypoint="actions/echo.py:other",
        source_paths=["actions/echo.py"],
    )
    assert first.id != second.id
    assert system.graph.action_revisions.get(first.id) is not None
    assert system.graph.action_revisions.get(second.id) is not None
