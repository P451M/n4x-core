# Author an N4X Experience

Use this workflow for Experience UI. Generic React work outside an N4X
Experience does not apply.

## Authority

Graph source is the source of truth. Writes take `revision_id`. Never copy theme values, component
lists, pinned references, bridge routes, or authoring-guide text into local
notes or skills.

Before writing or editing Surface source:

1. Inspect the target ExperienceRevision.
2. Call `inspect_experience_design_context` with its revision ID and
   `include_content=true`. Pass `last_seen_hash` from a previous inspect to
   skip unchanged guide and theme bodies.
3. Follow the returned `ui_profile` precedence, active guide/theme hashes,
   workflow, and visual-review checklist. `inspect_experience_revision`
   includes `dirty_paths` (added, removed, or changed since the parent
   revision), Surfaces without a current artifact, and a development
   `preview_url` when a matching deployment is still valid.
   Each `application_access` row includes that Application's
   `active_revision_id` for `create_development_deployment`.
4. Use focused `inspect_authoring_guide`, `inspect_surface_theme`,
   `inspect_component_palette`, or `inspect_experience_bridge` only when the
   composite response points to details needed for the task.

Do not treat compilation or `validate_experience_revision` as proof of visual
quality. The design context is advisory; structural validation remains a
separate operation.

## Workflow

- Confirm the running N4X MCP server and inspect the target Experience and
  revision via `inspect_system`. `create_experience_revision` takes
  `parent_revision_id`. If a draft already exists and you omit the parent, or
  you pass that draft or its parent, the draft is reused. A different parent
  while a draft exists is an error — `discard_experience_revision` first.
- Declare or verify Application access before Surface code consumes it.
- Load `inspect_experience_design_context`.
- List and read relevant existing graph source before deciding what to
  replace, extend, or reuse.
- Establish or reuse the selected component foundation, utilities, and shell
  before broad feature UI.
- Declare Experience-owned JavaScript dependencies before importing them.
- Write source through MCP and keep every Surface's `source_paths` declaration
  complete.
- Build changed Surfaces incrementally; fix build and runtime errors before
  expanding the change.
- Exercise graph-backed controls and verify loading, empty, success, and error
  states. A click must paint the next screen from local state before invoke
  returns; persist in the background and roll back on failure.
- After Surfaces are built, create or reuse a `DevelopmentDeployment` and open
  the returned `preview_url`. Do not activate to preview or to share a URL.
  MCP App / widget HTML is not the browser shell.
- Capture and inspect screenshots for every changed Surface in the relevant
  desktop, narrow, light, and dark states when browser capability exists.
- Report any visual state that could not be inspected.
- Re-read graph source changes, validate the Experience revision, then activate
  only after functional and visual review.

## Live draft preview

`create_development_deployment` and `inspect_development_deployment` return an
absolute instance `preview_url`:
`{origin}/development/{deployment_id}/experience/{experience_id}`.
`origin_kind` is `public` when `N4X_PUBLIC_ORIGIN` is not loopback; `host_bind_url`
is the process bind and is never the only URL on a managed instance.

On a public origin `preview_auth` is `deployment`: open `preview_url` without
OIDC. The path is a short-lived deployment capability; production
`/experience/{id}` stays instance OIDC. Local loopback has `preview_auth`
`none` and no sign-in. If the URL is expired or the client cannot open it,
report that visual review was impossible. Do not serve drafts on production
`/experience/{id}`.

## Editing discipline

- Reuse existing components and composition conventions before introducing new
  ones.
- Separate reusable component source from product-specific layout and behavior.
- Preserve the user-selected design system. Do not mix `n4x-default`, `custom`,
  and `none` conventions.
- Keep all visible controls, counts, statuses, and navigation backed by declared
  Application data or active actions.

## Paint first

A click opens the next screen from local state (query flag, optimistic row, or
cached object). Persist with invoke in the background. Roll back and toast if
it fails. Do not await an action in the click handler only to obtain an id or
to refresh a list you can patch. Do not put a global busy on mark, create, or
navigate. Open a compose/new route immediately (`new=1`, `compose=1`); do not
await create and then navigate.

- Graph source is the authority. Do not check out a local workspace.
  A new file or a rewrite: compose the body — `/tmp` is only scratch — then
  `write_source_file` with `revision_id` and `expected_hash` from the last
  read. A few surgical hunks: `apply_source_patch` with `revision_id` and
  unique context; line numbers are a hint. Search the tree or read a line
  range instead of reading every file.

## MCP App Surfaces

For `surface_type=mcp_app`, also call `inspect_client_guide` with
`topic=mcp_app`. Application domain behavior remains app-owned.
