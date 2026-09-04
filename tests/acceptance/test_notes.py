from __future__ import annotations

import json
import re

import pytest
from n4x.system.http import create_system_http_app
from starlette.testclient import TestClient

from n4x.graph.store import relation_physical_type
from tests.cypher_source import action_source

from .conftest import pnpm_available, unique_app_id
from .harness import McpAuthoringHarness

pytestmark = pytest.mark.acceptance

NOTES_ACTIONS = """
def _find_or_create(ctx, object_type_id, field, value):
    for item in list_objects(ctx, object_type_id):
        if item['values'].get(field) == value:
            return item
    return upsert_object(ctx, object_type_id, {field: value})

def _replace_links(ctx, physical_type, relation_type_id, note_id, target_ids):
    existing = ctx.graph.run_cypher(
        f'''
        MATCH (from:ApplicationObject {{
            application_id: $application_id,
            data_space_id: $data_space_id,
            id: $from_id
        }})-[r:{physical_type}]->()
        RETURN r.id AS id, r.to_object_id AS to_object_id
        ''',
        {
            'application_id': ctx.application_id,
            'data_space_id': ctx.data_space_id,
            'from_id': note_id,
            'relation_type_id': relation_type_id,
        },
    )
    existing_targets = {relation['to_object_id'] for relation in existing}
    wanted = set(target_ids)
    for relation in existing:
        if relation['to_object_id'] not in wanted:
            ctx.graph.run_cypher(
                f'''
                MATCH ()-[r:{physical_type} {{
                    id: $id, application_id: $application_id
                }}]->()
                DELETE r
                ''',
                {
                    'id': relation['id'],
                    'application_id': ctx.application_id,
                    'data_space_id': ctx.data_space_id,
                },
            )
    for target_id in wanted - existing_targets:
        merge_rel(ctx, physical_type, relation_type_id, note_id, target_id)

def create_note(ctx, input):
    notebook_id = input.get('notebook_id')
    if notebook_id:
        notebook = get_object(ctx, notebook_id)
    else:
        notebook = _find_or_create(
            ctx, 'notes.Notebook', 'title', input.get('notebook', 'Inbox')
        )
    note = upsert_object(ctx, 'notes.Note', {
        'title': input['title'],
        'body': input.get('body', ''),
        'archived': False,
    })
    merge_rel(
        ctx,
        '__NOTE_NOTEBOOK_PHYSICAL__',
        'notes.note_notebook',
        note['id'],
        notebook['id'],
    )
    for name in dict.fromkeys(input.get('tags') or []):
        tag = _find_or_create(ctx, 'notes.Tag', 'name', name)
        merge_rel(
            ctx, '__NOTE_TAG_PHYSICAL__', 'notes.note_tag', note['id'], tag['id']
        )
    return {'id': note['id'], 'notebook_id': notebook['id']}

def list_notes(ctx, input):
    notes = list_objects(ctx, 'notes.Note')
    if input.get('include_archived'):
        return {'notes': notes}
    return {'notes': [note for note in notes if not note['values'].get('archived')]}

def update_note(ctx, input):
    current = get_object(ctx, input['id'])
    values = {**current['values'], **input.get('values', {})}
    updated = upsert_object(
        ctx, current['object_type_id'], values, object_id=input['id']
    )
    if input.get('notebook_id'):
        _replace_links(
            ctx,
            '__NOTE_NOTEBOOK_PHYSICAL__',
            'notes.note_notebook',
            input['id'],
            [input['notebook_id']],
        )
    if 'tags' in input:
        tag_ids = [
            _find_or_create(ctx, 'notes.Tag', 'name', name)['id']
            for name in dict.fromkeys(input.get('tags') or [])
        ]
        _replace_links(
            ctx, '__NOTE_TAG_PHYSICAL__', 'notes.note_tag', input['id'], tag_ids
        )
    return updated

def archive_note(ctx, input):
    current = get_object(ctx, input['id'])
    return upsert_object(
        ctx,
        current['object_type_id'],
        {**current['values'], 'archived': True},
        object_id=input['id'],
    )
"""

NOTES_MIGRATION = """
def run(ctx, input):
    updated = 0
    for note in list_objects(ctx, 'notes.Note'):
        if 'archived' not in note['values']:
            upsert_object(
                ctx,
                note['object_type_id'],
                {**note['values'], 'archived': False},
                object_id=note['id'],
            )
            updated += 1
    return {'updated': updated}
"""

NOTES_SURFACE = """
import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './n4x-theme.css';

type GraphObject = {
  id: string;
  object_type_id: string;
  values: Record<string, unknown>;
};

type GraphRelation = {
  id: string;
  relation_type_id: string;
  from_object_id: string;
  to_object_id: string;
};

type BridgeContract = {
  version: string;
  http: {
    objects: string;
    relations: string;
    invoke: string;
  };
};

type Selection =
  | { kind: 'all' }
  | { kind: 'archived' }
  | { kind: 'notebook'; id: string }
  | { kind: 'tag'; id: string };

const applicationId = decodeURIComponent(location.pathname.split('/')[2] || '');
const expectedBridgeVersion = 'n4x.experience.bridge.v1';

function endpoint(template: string, values: Record<string, string>) {
  return Object.entries(values).reduce(
    (path, [key, value]) => path.replace(`{${key}}`, encodeURIComponent(value)),
    template,
  );
}

function label(item: GraphObject) {
  return String(item.values.title || item.values.name || 'Untitled');
}

function App() {
  const [contract, setContract] = useState<BridgeContract | null>(null);
  const [objects, setObjects] = useState<GraphObject[]>([]);
  const [relations, setRelations] = useState<GraphRelation[]>([]);
  const [selection, setSelection] = useState<Selection>({ kind: 'all' });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [notebookId, setNotebookId] = useState('');
  const [tags, setTags] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const notes = objects.filter((item) => item.object_type_id === 'notes.Note');
  const notebooks = objects.filter(
    (item) => item.object_type_id === 'notes.Notebook',
  );
  const tagObjects = objects.filter((item) => item.object_type_id === 'notes.Tag');
  const selectedNote = notes.find((note) => note.id === selectedId) || null;

  const linksFrom = (noteId: string, relationType: string) =>
    relations.filter(
      (relation) =>
        relation.from_object_id === noteId &&
        relation.relation_type_id === relationType,
    );

  const filteredNotes = useMemo(() => {
    return notes.filter((note) => {
      const archived = Boolean(note.values.archived);
      if (selection.kind === 'archived') return archived;
      if (archived) return false;
      if (selection.kind === 'notebook') {
        return linksFrom(note.id, 'notes.note_notebook').some(
          (relation) => relation.to_object_id === selection.id,
        );
      }
      if (selection.kind === 'tag') {
        return linksFrom(note.id, 'notes.note_tag').some(
          (relation) => relation.to_object_id === selection.id,
        );
      }
      return true;
    });
  }, [notes, relations, selection]);

  async function refresh(activeContract = contract) {
    if (!activeContract) return;
    const objectsUrl = endpoint(activeContract.http.objects, {
      experience_id: applicationId,
      application_id: applicationId,
    });
    const relationsUrl = endpoint(activeContract.http.relations, {
      experience_id: applicationId,
      application_id: applicationId,
    });
    const [objectResponse, relationResponse] = await Promise.all([
      fetch(objectsUrl),
      fetch(relationsUrl),
    ]);
    if (!objectResponse.ok || !relationResponse.ok) {
      throw new Error('Unable to read the Notes graph');
    }
    setObjects((await objectResponse.json()).objects);
    setRelations((await relationResponse.json()).relations);
  }

  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch('/bridge/contract');
        const discovered = (await response.json()) as BridgeContract;
        if (discovered.version !== expectedBridgeVersion) {
          throw new Error(`Unsupported bridge ${discovered.version}`);
        }
        setContract(discovered);
        await refresh(discovered);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : 'Unable to start Notes');
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedNote || creating) return;
    const notebook = linksFrom(selectedNote.id, 'notes.note_notebook')[0];
    const noteTags = linksFrom(selectedNote.id, 'notes.note_tag')
      .map((relation) => tagObjects.find((tag) => tag.id === relation.to_object_id))
      .filter(Boolean)
      .map((tag) => label(tag!));
    setTitle(String(selectedNote.values.title || ''));
    setBody(String(selectedNote.values.body || ''));
    setNotebookId(notebook?.to_object_id || '');
    setTags(noteTags.join(', '));
    setEditing(false);
  }, [selectedId, objects, relations]);

  async function invoke(actionId: string, input: Record<string, unknown>) {
    if (!contract) return;
    setBusy(true);
    setError('');
    try {
      const response = await fetch(
        endpoint(contract.http.invoke, {
          experience_id: applicationId,
          application_id: applicationId,
          action_id: actionId,
        }),
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ input }),
        },
      );
      const invocation = await response.json();
      if (!response.ok || invocation.status !== 'succeeded') {
        throw new Error(invocation.error || `${actionId} failed`);
      }
      await refresh();
      return invocation.output;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `${actionId} failed`);
    } finally {
      setBusy(false);
    }
  }

  function startCreate() {
    setCreating(true);
    setEditing(true);
    setSelectedId(null);
    setTitle('');
    setBody('');
    setNotebookId(notebooks[0]?.id || '');
    setTags('');
  }

  async function save() {
    const tagNames = tags
      .split(',')
      .map((name) => name.trim())
      .filter(Boolean);
    if (creating) {
      const output = await invoke('notes.create', {
        title,
        body,
        notebook_id: notebookId || undefined,
        notebook: 'Inbox',
        tags: tagNames,
      });
      if (output?.id) setSelectedId(output.id);
      setCreating(false);
    } else if (selectedNote) {
      await invoke('notes.update', {
        id: selectedNote.id,
        values: { title, body },
        notebook_id: notebookId || undefined,
        tags: tagNames,
      });
    }
    setEditing(false);
  }

  async function archive() {
    if (!selectedNote) return;
    await invoke('notes.archive', { id: selectedNote.id });
    setSelectedId(null);
  }

  function selectNavigation(next: Selection) {
    setSelection(next);
    setSelectedId(null);
    setCreating(false);
    setEditing(false);
  }

  function NavigationButton({
    active,
    count,
    children,
    onClick,
  }: {
    active: boolean;
    count: number;
    children: React.ReactNode;
    onClick: () => void;
  }) {
    return (
      <button
        className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm transition-colors ${
          active
            ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
            : 'text-sidebar-foreground hover:bg-sidebar-accent/60'
        }`}
        onClick={onClick}
      >
        <span className="truncate">{children}</span>
        <span className="rounded-full bg-background/80 px-2 py-0.5 text-xs text-muted-foreground">
          {count}
        </span>
      </button>
    );
  }

  const relatedNotebook = selectedNote
    ? linksFrom(selectedNote.id, 'notes.note_notebook')
        .map((relation) =>
          notebooks.find((item) => item.id === relation.to_object_id),
        )
        .filter(Boolean)
    : [];
  const relatedTags = selectedNote
    ? linksFrom(selectedNote.id, 'notes.note_tag')
        .map((relation) =>
          tagObjects.find((item) => item.id === relation.to_object_id),
        )
        .filter(Boolean)
    : [];

  return (
    <main className="min-h-screen bg-muted p-3 text-foreground">
      <div className="mx-auto grid min-h-[calc(100vh-1.5rem)] max-w-[1500px] grid-cols-[240px_360px_1fr] overflow-hidden rounded-xl border bg-background shadow-sm">
        <aside className="flex flex-col border-r bg-sidebar p-4">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                N4X workspace
              </p>
              <h1 className="mt-1 text-xl font-semibold">Notes</h1>
            </div>
            <span className="rounded-md border bg-background px-2 py-1 font-mono text-[10px] text-muted-foreground">
              v1
            </span>
          </div>
          <button
            className="mb-5 rounded-lg bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground shadow-xs hover:opacity-90 disabled:opacity-50"
            onClick={startCreate}
            disabled={busy}
          >
            + New note
          </button>
          <nav className="space-y-1">
            <NavigationButton
              active={selection.kind === 'all'}
              count={notes.filter((note) => !note.values.archived).length}
              onClick={() => selectNavigation({ kind: 'all' })}
            >
              All notes
            </NavigationButton>
            <NavigationButton
              active={selection.kind === 'archived'}
              count={notes.filter((note) => note.values.archived).length}
              onClick={() => selectNavigation({ kind: 'archived' })}
            >
              Archive
            </NavigationButton>
          </nav>
          <p className="mb-2 mt-6 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Notebooks
          </p>
          <nav className="space-y-1">
            {notebooks.map((notebook) => (
              <NavigationButton
                key={notebook.id}
                active={
                  selection.kind === 'notebook' && selection.id === notebook.id
                }
                count={
                  notes.filter((note) =>
                    linksFrom(note.id, 'notes.note_notebook').some(
                      (relation) => relation.to_object_id === notebook.id,
                    ),
                  ).length
                }
                onClick={() =>
                  selectNavigation({ kind: 'notebook', id: notebook.id })
                }
              >
                {label(notebook)}
              </NavigationButton>
            ))}
          </nav>
          {tagObjects.length > 0 && (
            <>
              <p className="mb-2 mt-6 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Tags
              </p>
              <nav className="space-y-1">
                {tagObjects.map((tag) => (
                  <NavigationButton
                    key={tag.id}
                    active={selection.kind === 'tag' && selection.id === tag.id}
                    count={
                      notes.filter((note) =>
                        linksFrom(note.id, 'notes.note_tag').some(
                          (relation) => relation.to_object_id === tag.id,
                        ),
                      ).length
                    }
                    onClick={() =>
                      selectNavigation({ kind: 'tag', id: tag.id })
                    }
                  >
                    # {label(tag)}
                  </NavigationButton>
                ))}
              </nav>
            </>
          )}
          <div className="mt-auto border-t pt-4 text-xs text-muted-foreground">
            {objects.length} objects · {relations.length} relations
          </div>
        </aside>

        <section className="border-r">
          <header className="border-b px-5 py-4">
            <h2 className="font-semibold">
              {selection.kind === 'all'
                ? 'All notes'
                : selection.kind === 'archived'
                  ? 'Archive'
                  : label(
                      objects.find((item) => item.id === selection.id) ||
                        ({ values: {} } as GraphObject),
                    )}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {filteredNotes.length} {filteredNotes.length === 1 ? 'note' : 'notes'}
            </p>
          </header>
          <div className="max-h-[calc(100vh-7.5rem)] overflow-y-auto p-3">
            {filteredNotes.map((note) => {
              const notebook = linksFrom(note.id, 'notes.note_notebook')
                .map((relation) =>
                  notebooks.find((item) => item.id === relation.to_object_id),
                )
                .find(Boolean);
              return (
                <button
                  key={note.id}
                  className={`mb-2 w-full rounded-lg border p-4 text-left transition ${
                    note.id === selectedId
                      ? 'border-primary bg-accent shadow-xs'
                      : 'bg-card hover:border-primary/40 hover:shadow-xs'
                  }`}
                  onClick={() => {
                    setCreating(false);
                    setSelectedId(note.id);
                  }}
                >
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="truncate font-medium">{label(note)}</h3>
                    {notebook && (
                      <span className="shrink-0 rounded bg-secondary px-2 py-1 text-[10px] text-secondary-foreground">
                        {label(notebook)}
                      </span>
                    )}
                  </div>
                  <p className="mt-2 line-clamp-2 text-sm leading-6 text-muted-foreground">
                    {String(note.values.body || 'No content')}
                  </p>
                </button>
              );
            })}
            {filteredNotes.length === 0 && (
              <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                No graph notes in this view.
              </div>
            )}
          </div>
        </section>

        <section className="min-w-0">
          {error && (
            <div className="m-5 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}
          {creating || selectedNote ? (
            <div className="flex h-full flex-col">
              <header className="flex items-center justify-between border-b px-6 py-4">
                <div>
                  <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                    {creating ? 'New graph object' : 'Note detail'}
                  </p>
                  {!creating && !editing && (
                    <h2 className="mt-1 text-lg font-semibold">{label(selectedNote!)}</h2>
                  )}
                </div>
                <div className="flex gap-2">
                  {!creating && !editing && !selectedNote?.values.archived && (
                    <>
                      <button
                        className="rounded-lg border px-3 py-2 text-sm hover:bg-muted"
                        onClick={() => setEditing(true)}
                      >
                        Edit
                      </button>
                      <button
                        className="rounded-lg border border-destructive/30 px-3 py-2 text-sm text-destructive hover:bg-destructive/10"
                        onClick={archive}
                        disabled={busy}
                      >
                        Archive
                      </button>
                    </>
                  )}
                  {(creating || editing) && (
                    <button
                      className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
                      onClick={save}
                      disabled={busy || !title.trim()}
                    >
                      {busy ? 'Saving…' : creating ? 'Create note' : 'Save changes'}
                    </button>
                  )}
                </div>
              </header>
              <div className="flex-1 overflow-y-auto p-6">
                {creating || editing ? (
                  <div className="mx-auto max-w-3xl space-y-5">
                    <label className="block">
                      <span className="mb-2 block text-xs font-medium uppercase tracking-wider text-muted-foreground">
                        Title
                      </span>
                      <input
                        className="w-full rounded-lg border bg-background px-4 py-3 text-lg font-semibold outline-none focus:ring-2 focus:ring-ring/30"
                        value={title}
                        onChange={(event) => setTitle(event.target.value)}
                        autoFocus
                      />
                    </label>
                    <div className="grid grid-cols-2 gap-4">
                      <label className="block">
                        <span className="mb-2 block text-xs font-medium uppercase tracking-wider text-muted-foreground">
                          Notebook
                        </span>
                        <select
                          className="w-full rounded-lg border bg-background px-3 py-2.5"
                          value={notebookId}
                          onChange={(event) => setNotebookId(event.target.value)}
                        >
                          {notebooks.length === 0 && (
                            <option value="">Inbox (created on save)</option>
                          )}
                          {notebooks.map((notebook) => (
                            <option key={notebook.id} value={notebook.id}>
                              {label(notebook)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="block">
                        <span className="mb-2 block text-xs font-medium uppercase tracking-wider text-muted-foreground">
                          Tags
                        </span>
                        <input
                          className="w-full rounded-lg border bg-background px-3 py-2.5"
                          value={tags}
                          onChange={(event) => setTags(event.target.value)}
                          placeholder="Comma-separated graph tags"
                        />
                      </label>
                    </div>
                    <label className="block">
                      <span className="mb-2 block text-xs font-medium uppercase tracking-wider text-muted-foreground">
                        Body
                      </span>
                      <textarea
                        className="min-h-80 w-full resize-y rounded-lg border bg-background px-4 py-3 leading-7 outline-none focus:ring-2 focus:ring-ring/30"
                        value={body}
                        onChange={(event) => setBody(event.target.value)}
                      />
                    </label>
                  </div>
                ) : (
                  <article className="mx-auto max-w-3xl">
                    <div className="mb-7 flex flex-wrap gap-2">
                      {relatedNotebook.map((notebook) => (
                        <span
                          key={notebook!.id}
                          className="rounded-md bg-secondary px-2.5 py-1 text-xs font-medium text-secondary-foreground"
                        >
                          Notebook · {label(notebook!)}
                        </span>
                      ))}
                      {relatedTags.map((tag) => (
                        <span
                          key={tag!.id}
                          className="rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground"
                        >
                          # {label(tag!)}
                        </span>
                      ))}
                      {selectedNote?.values.archived && (
                        <span className="rounded-md bg-destructive/10 px-2.5 py-1 text-xs font-medium text-destructive">
                          Archived
                        </span>
                      )}
                    </div>
                    <p className="whitespace-pre-wrap text-base leading-8">
                      {String(selectedNote?.values.body || 'No content')}
                    </p>
                    <section className="mt-10 rounded-lg border bg-muted/40 p-4">
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        Graph relationships
                      </h3>
                      <div className="mt-3 space-y-2 font-mono text-xs">
                        {[...relatedNotebook, ...relatedTags].map((object) => (
                          <div
                            key={object!.id}
                            className="flex items-center gap-2 rounded-md bg-background p-2"
                          >
                            <span className="text-primary">notes.Note</span>
                            <span className="text-muted-foreground">→</span>
                            <span>{object!.object_type_id}</span>
                            <span className="ml-auto text-muted-foreground">
                              {label(object!)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </section>
                  </article>
                )}
              </div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center p-8 text-center">
              <div>
                <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-accent text-xl text-accent-foreground">
                  N
                </div>
                <h2 className="font-semibold">Select a graph note</h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  Choose a note from the list or create a new one.
                </p>
              </div>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
"""


def _definition_id(app_id: str, name: str) -> str:
    return f"{app_id}.{name}"


def _render_notes_python(source: str, app_id: str) -> str:
    rendered = _namespace_source(action_source(source), app_id)
    return (
        rendered.replace(
            "__NOTE_NOTEBOOK_PHYSICAL__",
            relation_physical_type(_definition_id(app_id, "note_notebook")),
        ).replace(
            "__NOTE_TAG_PHYSICAL__",
            relation_physical_type(_definition_id(app_id, "note_tag")),
        )
    )


def _namespace_source(source: str, app_id: str) -> str:
    """Render canonical Notes source for an application-owned namespace."""
    definition_names = (
        "Notebook",
        "Note",
        "Tag",
        "note_notebook",
        "note_tag",
        "create",
        "list",
        "update",
        "archive",
        "migrate_archived",
        "main",
    )
    names = "|".join(re.escape(name) for name in definition_names)
    return re.sub(rf"\bnotes\.(?=(?:{names})\b)", f"{app_id}.", source)


def _author_notes_surface(harness: McpAuthoringHarness, app_id: str) -> dict | None:
    if not pnpm_available():
        return None
    source = _namespace_source(NOTES_SURFACE, app_id).replace(
        "const applicationId = decodeURIComponent(location.pathname.split('/')[2] || '');",
        f"const applicationId = {json.dumps(app_id)};",
    )
    return harness.author_experience(
        experience_id=f"{app_id}-experience",
        name="Notes",
        application_access=[
            {
                "application_id": app_id,
                "object_type_ids": [
                    _definition_id(app_id, name)
                    for name in ("Notebook", "Note", "Tag")
                ],
                "relation_type_ids": [
                    _definition_id(app_id, name)
                    for name in ("note_notebook", "note_tag")
                ],
                "action_ids": [
                    _definition_id(app_id, name)
                    for name in ("create", "list", "update", "archive")
                ],
            }
        ],
        source_files={"src/main.tsx": source},
        surfaces=[
            {
                "surface_id": "main",
                "surface_type": "browser",
                "entrypoint": "src/main.tsx",
                "source_paths": ["src/main.tsx"],
                "config": {"mount_path": "/"},
            }
        ],
    )


def _author_notes(
    harness: McpAuthoringHarness, app_id: str, *, include_surface: bool = False
) -> dict:
    """Author and activate the complete Notes application through generic MCP."""
    app = harness.run("create_application", application_id=app_id, name="Notes")
    revision = harness.run("create_application_revision", application_id=app["id"])
    source_tree_id = revision["source_tree_id"]
    revision_id = revision["id"]
    notebook = harness.run(
        "create_object_type",
        application_revision_id=revision_id,
        object_type_id=_definition_id(app_id, "Notebook"),
        name="Notebook",
        properties={"title": {"type": "string"}},
        required=["title"],
    )
    note = harness.run(
        "create_object_type",
        application_revision_id=revision_id,
        object_type_id=_definition_id(app_id, "Note"),
        name="Note",
        properties={
            "title": {"type": "string"},
            "body": {"type": "string"},
            "archived": {"type": "boolean"},
        },
        required=["title"],
    )
    tag = harness.run(
        "create_object_type",
        application_revision_id=revision_id,
        object_type_id=_definition_id(app_id, "Tag"),
        name="Tag",
        properties={"name": {"type": "string"}},
        required=["name"],
    )
    harness.run(
        "create_relation_type",
        application_revision_id=revision_id,
        relation_type_id=_definition_id(app_id, "note_notebook"),
        name="note_notebook",
        from_object_type_id=note["object_type_id"],
        to_object_type_id=notebook["object_type_id"],
    )
    harness.run(
        "create_relation_type",
        application_revision_id=revision_id,
        relation_type_id=_definition_id(app_id, "note_tag"),
        name="note_tag",
        from_object_type_id=note["object_type_id"],
        to_object_type_id=tag["object_type_id"],
    )
    harness.run(
        "write_source_file",
        source_tree_id=source_tree_id,
        path="actions/notes.py",
        role="action",
        language="python",
        content=_render_notes_python(NOTES_ACTIONS, app_id),
    )
    create = harness.run(
        "create_action",
        application_revision_id=revision_id,
        action_id=_definition_id(app_id, "create"),
        kind="normal",
        entrypoint="actions/notes.py:create_note",
        source_paths=["actions/notes.py"],
        input_schema={"type": "object", "required": ["title"]},
    )
    for action_id, function in (
        (_definition_id(app_id, "list"), "list_notes"),
        (_definition_id(app_id, "update"), "update_note"),
        (_definition_id(app_id, "archive"), "archive_note"),
    ):
        harness.run(
            "create_action",
            application_revision_id=revision_id,
            action_id=action_id,
            kind="normal",
            entrypoint=f"actions/notes.py:{function}",
            source_paths=["actions/notes.py"],
        )
    activation = harness.run(
        "activate_application_revision", application_revision_id=revision_id
    )
    surface = _author_notes_surface(harness, app_id) if include_surface else None
    return {
        "application": app,
        "revision": revision,
        "create_action": create,
        "surface": surface,
        "activation": activation,
    }


def test_notes_is_reconstructable_through_mcp(harness: McpAuthoringHarness) -> None:
    app_id = unique_app_id("notes")
    namespace = f"{app_id}."
    authored = _author_notes(harness, app_id)
    created = harness.run(
        "run_active_action",
        application_id=app_id,
        action_id=_definition_id(app_id, "create"),
        input_value={"title": "First", "body": "Hello", "tags": ["inbox"]},
    )
    activated = authored["activation"]
    updated = harness.run(
        "run_active_action",
        application_id=app_id,
        action_id=_definition_id(app_id, "update"),
        input_value={"id": created["output"]["id"], "values": {"body": "Updated"}},
    )
    archived = harness.run(
        "run_active_action",
        application_id=app_id,
        action_id=_definition_id(app_id, "archive"),
        input_value={"id": created["output"]["id"]},
    )
    listed = harness.run(
        "run_active_action",
        application_id=app_id,
        action_id=_definition_id(app_id, "list"),
        input_value={},
    )
    objects = harness.inspect_objects(application_id=app_id)
    relations = harness.inspect_relations(application_id=app_id)

    next_revision = harness.run("create_application_revision", application_id=app_id)
    harness.run(
        "write_source_file",
        source_tree_id=next_revision["source_tree_id"],
        path="migrations/archived.py",
        role="migration",
        language="python",
        content=_render_notes_python(NOTES_MIGRATION, app_id),
    )
    harness.run(
        "create_action",
        application_revision_id=next_revision["id"],
        action_id=_definition_id(app_id, "migrate_archived"),
        kind="migration",
        entrypoint="migrations/archived.py:run",
        source_paths=["migrations/archived.py"],
        migration_metadata={
            "mutates_application_data": True,
        },
    )
    harness.run(
        "activate_application_revision",
        application_revision_id=next_revision["id"],
    )

    http = TestClient(create_system_http_app(harness.runtime))
    http_objects = http.get(
        f"/api/apps/{app_id}/objects?object_type_id={_definition_id(app_id, 'Note')}"
    )
    http_relations = http.get(f"/api/apps/{app_id}/relations")

    assert activated["status"] == "active"
    assert updated["status"] == "succeeded"
    assert archived["status"] == "succeeded"
    assert listed["output"]["notes"] == []
    note_records = [
        item
        for item in objects
        if item["object_type_id"] == _definition_id(app_id, "Note")
    ]
    assert note_records[0]["values"]["archived"] is True
    assert {item["relation_type_id"] for item in relations} == {
        _definition_id(app_id, "note_notebook"),
        _definition_id(app_id, "note_tag"),
    }
    assert all(item["object_type_id"].startswith(namespace) for item in objects)
    assert all(item["relation_type_id"].startswith(namespace) for item in relations)
    assert authored["create_action"]["action_id"].startswith(namespace)
    assert authored["surface"] is None
    assert http_objects.status_code == 200
    assert http_objects.json()["objects"][0]["id"] == note_records[0]["id"]
    assert http_relations.status_code == 200
    assert len(http_relations.json()["relations"]) == len(relations)
