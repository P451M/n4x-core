# Author an N4X Application

Use this workflow for backend graph apps. UI belongs on an Experience.

## Sequence

1. Call `inspect_client_guide` with `topic=application` if you have not already.
2. `inspect_system` if you need the running revision; `list`/`inspect` existing
   Applications before creating a parallel namespace.
3. `create_application` then `create_application_revision`. A new revision
   continues the latest `status=draft` for that app, else the active revision.
   Pass `parent_revision_id` only to fork from another revision.
4. Declare `create_object_type` and `create_relation_type` for the domain model.
   Keep `RelationTypeRevision.physical_type` from inspect; Cypher cannot take a
   relationship type as a parameter.
5. `write_source_file` action Python onto the revision SourceTree. Declare
   `create_runtime_dependency` before importing third-party packages.
6. `create_action` with `kind: "normal"`, entrypoint, and `source_paths`.
   Optional fields (`timeout_seconds`, `concurrency_policy`, schemas) may be
   omitted or JSON `null`; the server applies defaults. `kind: "python"` is
   invalid. Actions mutate data with Cypher, not an object buffer.
7. Optional: `create_trigger`, `create_test_case`, `create_callback_route`.
8. `run_draft_action` or `run_application_tests` against the draft. Optional
   `data_space_id` defaults to `production`. A development space requires an
   owning `DevelopmentDeployment`. Do not use `run_active_action` until the
   revision is activated.
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
`ApplicationObject.values`. Embed the physical relation type from inspect.
Experience list only sees `:ApplicationObject` and `APP_REL_*` edges. Do not
wait on provider I/O inside `transaction()`. `kind=migration` runs once during
activation after a checkpoint; there is no dry-run stage.

An action an Experience will await on a click should return after the graph
commit. Provider I/O (IMAP, SMTP, CalDAV) belongs on `submit`, a job, or a
follow-up action — not on the invoke that gates the next screen.

## Draft versus active

- `run_draft_action` / `submit_draft_action` — graph source on a draft revision.
- `run_active_action` / `submit_active_action` — the Application's active
  revision and production DataSpace.

## Source

Graph SourceFiles are the authority. Do not check out a local workspace.
Mutations and listings return metadata; `read_source_file` when you need the
bytes (`offset` / `limit` on large files). Search the tree instead of reading
every file.

A new file or a rewrite (more than a few hunks): compose the body — a local
`/tmp` file is only scratch — then `write_source_file` with `expected_hash`
from the last read. A few surgical hunks: `apply_source_patch` with unique
context; line numbers are a hint, not an address.

## After backend

If the user needs UI, create or revise an Experience and follow
`inspect_client_guide` with `topic=experience`. Secrets, jobs, packages, and
development DataSpaces are under `topic=operate`.
