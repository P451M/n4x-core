# Author an N4X Application

Use this workflow for backend graph apps. UI belongs on an Experience.

## Sequence

1. Call `inspect_client_guide` with `topic=application` if you have not already.
2. `inspect_system` lists Applications (`active_revision_id`, `draft_revision_id`,
   derived `source_tree_id`). Inspect those before creating a parallel namespace.
3. `create_application` then `create_application_revision`. If a draft already
   exists and you omit `parent_revision_id`, or you pass that draft or its
   parent, the draft is reused. A different parent while a draft exists is an
   error — `discard_application_revision` first. Else the next `@N+1` copies
   edges from the parent.
4. Declare `create_object_type` and `create_relation_type` for the domain model.
   Keep `RelationTypeRevision.physical_type` from inspect; Cypher cannot take a
   relationship type as a parameter.
5. `write_source_file` with `revision_id` for action Python. The first write
   copy-on-writes a working tree. Declare `create_runtime_dependency` before
   importing third-party packages.
6. `create_action` with `kind: "normal"`, entrypoint, and `source_paths`.
   Optional fields (`timeout_seconds`, `concurrency_policy`, schemas) may be
   omitted or JSON `null`; the server applies defaults. `kind: "python"` is
   invalid. Actions mutate data with Cypher, not an object buffer.
7. Optional: `create_trigger` / `create_test_case` with `action_id` (not an
   ActionRevision id), plus `create_callback_route`.
8. `run_draft_action` / `submit_draft_action` take `application_revision_id` and
   `action_id`. Optional `data_space_id` defaults to `production`. A development
   space requires an owning `DevelopmentDeployment`. Do not use
   `run_active_action` until the revision is activated.
9. `validate_application_revision`, fix errors, then
   `activate_application_revision`.
10. After activation, `run_active_action` mutates production Application data.

## Cypher

```python
ctx.graph.run_cypher(query, params)   # one session, one commit
with ctx.graph.transaction():
    ctx.graph.run_cypher(...)
    ctx.graph.run_cypher(...)         # same session
```

Always set `application_id` and `data_space_id` from `ctx`. Store fields on
`ApplicationObject.values` as JSON text (`json.dumps` / `json.loads`). Neo4j
cannot store a map as a property. Embed the physical relation type from inspect.
Experience list only sees `:ApplicationObject` and `APP_REL_*` edges. Do not
wait on provider I/O inside `transaction()`. `kind=migration` runs once during
activation after a checkpoint; there is no dry-run stage.

An action an Experience will await on a click should return after the graph
commit. Provider I/O (IMAP, SMTP, CalDAV) belongs on `submit`, a job, or a
follow-up action — not on the invoke that gates the next screen.

## Draft versus active

- `run_draft_action` / `submit_draft_action` — `(application_revision_id, action_id)`
  on a draft revision. The runtime resolves the ActionRevision from that
  revision's tree.
- `run_active_action` / `submit_active_action` — the Application's active
  revision and production DataSpace.

## Source

Graph source is the authority (`SourceContent` blobs; `SourceFile` is a read
DTO). Do not check out a local workspace. Writes, reads, list, and search take `revision_id`.
Payloads still include a derived `source_tree_id`. Mutations and listings
return metadata; `read_source_file` when you need the bytes (`offset` / `limit`
on large files). `search_source_tree` accepts `context` (default 2).

A new file or a rewrite (more than a few hunks): compose the body — a local
`/tmp` file is only scratch — then `write_source_file` with `revision_id` and
`expected_hash` from the last read. A few surgical hunks: `apply_source_patch`
with `revision_id` and unique context; line numbers are a hint, not an address.

## After backend

If the user needs UI, create or revise an Experience and follow
`inspect_client_guide` with `topic=experience`. Secrets, jobs, packages, and
development DataSpaces are under `topic=operate`.
