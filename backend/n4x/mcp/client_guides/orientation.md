# N4X MCP orientation

N4X is MCP-first. This catalog authors graph-owned Applications and Experiences.
Do not copy this playbook, theme tokens, or authoring-guide text into a local
skill. Call `inspect_client_guide` whenever you need the workflow.

## Split

- **Application** — backend. Schema, Python source, actions, tests, triggers,
  secrets, and runtime data. Call `inspect_client_guide` with `topic=application`.
- **Experience** — frontend. TypeScript/React source, Surfaces, JavaScript
  dependencies, and Application access. Call `inspect_client_guide` with
  `topic=experience`. For Surface source, then call
  `inspect_experience_design_context` and treat that output as UI authority.
  The UI paints first; invoke persists.

Do not put UI in an Application or domain behavior in an Experience.

## Inspect first

List and inspect existing Applications, Experiences, revisions, and SourceFiles
before creating replacements. Prefer reuse over a parallel namespace.

## Validation vs design

`validate_application_revision` and `validate_experience_revision` check
structure. They do not prove visual quality or product correctness. Design
context and the graph-owned authoring guide are advisory.

## Destructive tools

Call `reset_dev_graph`, `import_official_system`, `enable_system_revision`, `repair_graph_edges`,
`retire_experience`, `delete_working_set`, deletes, rollback, and checkpoint restore only when the
user explicitly asks. Confirmation databases and expected revision ids must
match exactly.

## Kernel boundary

Keep domain nouns, validation, queries, files, retries, and provider protocols
in Application or Experience source. Do not propose Host/System features unless
the work requires transaction commit, secret-backend access, process lifecycle,
Experience authorization, or HTTP/MCP hosting.

## Other topics

- `mcp_app` — chat-native Surfaces versus browser Surfaces
- `operate` — development deployments, jobs, callbacks, secrets, packages
