# N4X development

Companion to the public [README.md](../README.md). Local authoring, tests, and Office reconstruction.

N4X is a graph-native local application runtime for AI-created applications.

The authoritative V1 architecture is in [SPEC.md](../SPEC.md). Why a
boundary exists is in [docs/adr/](adr/).

## V1 status

The V1 engine architecture is implemented. Product serving is Host plus one System worker (`n4x serve` / `n4x mcp-stdio`):

- Neo4j is the authoritative production store; in-memory persistence is a test adapter.
- The System worker commits through one `GraphUnitOfWork`.
- Subprocess, Cypher IPC, secret-backend, `uv`, and `pnpm` waits reject active units of work.
- Application and Experience activation is a single pass. A killed activate retries from the start. A failed activate never switches `ACTIVE_REVISION`. Mutating migrations checkpoint and restore.
- Checkpoints, migrations, jobs, retries, leases, provenance, and graph-integrity repair are durable.
- Graph-owned Python actions run through a versioned subprocess/action-context protocol.
- Privileged app Cypher is explicit, mode-restricted, checkpoint-aware, and audited.
- React/TypeScript Experience Surfaces use host-appropriate browser or MCP Apps bridges and immutable build artifacts.
- Surface builds are explicit or activation-driven. Hosts return a missing-artifact error instead of building on first request.
- Generic MCP authoring reconstructs applications; there are no app-specific Host or System seeders.
- System enable rematerializes current source from the enabled revision's tree and stop-then-starts the worker. Neo4j stays in place. Package export then wipe is a development reset, not a System update.

Notes, Mail, and Calendar are independent backend Applications presented by
graph-owned Office Experience Surfaces:

- Notes: notebook/note/tag navigation, editing, relationships, and archive.
- Mail: account setup, inbox/detail, refresh, and compose/send.
- Calendar: calendar/list/detail views and event create/update/delete.

N4X V1 applications remain on `n4x.graph.metamodel.v6`. A fresh graph is stamped
that version; an older or non-empty unversioned graph is rejected and requires
an explicit guarded reset. There is no automatic predecessor-graph migration.

## Architecture

Requests enter the Host reverse proxy, reach the System worker MCP/HTTP surfaces, and commit through one shared `GraphUnitOfWork` into Neo4j. `SystemRuntime` is the worker composition root. Repositories and Neo4j remain authoritative for reads and writes.

App behavior stays graph-owned:

- backend source, Python dependencies, schemas, actions, and triggers belong to Application revisions; frontend source, JavaScript dependencies, and browser/MCP App Surfaces belong to Experience revisions;
- trusted actions mutate graph data through the Cypher gateway (`run_cypher` or `transaction()`);
- secret values remain in macOS Keychain or the encrypted local backend; Neo4j stores only references and credential metadata.

Callbacks use `GET|POST /callback/{route_id}` and the generic MCP callback tools. Routes are state-checked, single-use where configured, and versioned independently.

## Versioned contracts

- `n4x.action.context.v2`
- `n4x.action.subprocess.v3` — one child-interpreter RPC loop; spawn env is stable (data root, Cypher gateway, application/space ids); each invoke is a stdin JSON line and a result file; children may be reused for the same ApplicationRevision and DataSpace
- `n4x.file.delivery.v1` — allowlisted Actions may authorize confined, short-lived Experience URLs for app-owned files without a Host/System Blob/File model
- `n4x.experience.bridge.v1` — browser Surfaces use Experience-scoped HTTP capabilities; MCP App Surfaces use standard postMessage `tools/call` with `window.openai.callTool` only as a compatibility fallback
- `n4x.mcp.authoring.v9` — interned revision graph; source writes take `revision_id`; `inspect_system` lists Applications and Experiences; draft run uses `(application_revision_id, action_id)`; discard tools for unused drafts
- `n4x.callback.v1`
- `n4x.package.v2` — deterministic active-working-set archives with canonical Surface declarations and optional Application data; package-v1 archives are rejected
- `n4x.graph.metamodel.v6`
- platform authoring seed `n4x.platform-authoring-seed.v1`, UI release `n4x-ui-v8`
- checkpoint snapshot format `1`

The test path is `sh scripts/ci.sh`: pytest, build this tree's image, then
`scripts/release_smoke.sh` (fresh boot, import+enable, and upgrade from the
previous `v*` GHCR image). It does not push or tag.

Official releases run that same script on Ubuntu, then publish `linux/amd64`
images (`ghcr.io/p451m/n4x-core:<tag>` and `:epoch-1`) plus
`release-index.json` via the `official-release` workflow. The workflow uses
`GITHUB_TOKEN` with `packages: write`. Host HTTP binds even when the enabled
System worker fails to start so a later `enable` can replace it. Enable
points the graph edge only after that worker is healthy. Host
`import_official_system` writes a SystemRevision from a zip and does not
enable it. `enable_system_revision` rematerializes current SourceFiles and
restarts the worker. `/health` reports Host adapter and enabled System
`content_root` as separate fields. Neo4j is not recreated.
MCP initialize `serverInfo.version` is the enabled System provenance hash. Initialize also
sends catalog list-changed notifications so clients do not keep the previous
image's tool list at the same `/mcp` URL.

The Surface bridge contract is available at `GET /bridge/contract` and through MCP `inspect_experience_bridge`. Active Experiences may manage only explicitly allowlisted existing secret references through the Experience-scoped `/secrets/{secret_reference_id}` route; values never enter action Invocations. `AuthSession` lifecycle is intentionally outside V1.

Application volume bytes are outside Neo4j, Package v2, and graph checkpoints.
Neo4j remains semantic authority for app-owned relative references and metadata.
For a personal cloud deployment, run one active N4X instance with one Neo4j
database, a persistent `N4X_RUNTIME_ROOT`, a durable secret backend, and
authenticated/private ingress. This release does not claim active-active
replicas or per-user data isolation.

Local runtimes persist the file-delivery signing key beneath
`<runtime-root>/config`. Cloud deployments may provide a deployment-scoped
`N4X_FILE_DELIVERY_SIGNING_KEY` of at least 32 characters; the runtime removes
that value from action subprocess environments.

Package archives are local files under `<runtime-root>/packages`. An
authenticated client writes an archive there with `PUT /packages/{name}`
(same instance identity as `/mcp`). Use `n4x package stage` from a laptop;
do not inline archive bytes through MCP. Use `preview_package`,
`export_package`, and `inspect_package` before `import_package`. Imports
preserve public ids, reject unrelated collisions, activate the working set,
and leave imported Application Triggers paused. Configure reported
secret/callback bindings, then call `resume_application_triggers` explicitly.

### Export and install Office

Office is app-owned release content authored through generic MCP; the Kernel
never bundles or auto-installs it. The graph is the authority, so a release is
exported directly from a runtime where Office is active: call `export_package`
with `root_kind="experience"`, `root_id="office"`, and an `archive_name` ending
in `.n4xp`.

To install it on another instance, confirm the local path and run
`n4x package stage --origin <instance> --file office.n4xp` (or copy the
file into `<runtime-root>/packages` on the box). Then call the generic MCP
`inspect_package` and `import_package` tools, configure any reported
secret/callback bindings, then call `resume_application_triggers` for each
imported Application when it is ready to run scheduled work. If inspect
reports a contract mismatch, import only with `allow_incompatible` after
the operator asks; the working set lands disabled until a new revision is
written for the current System.

## Development

The release test path:

```bash
sh scripts/ci.sh
```

Pytest only:

```bash
uv run --extra dev pytest -q
```

Focused gates:

```bash
uv run --extra dev pytest -q -m contract
uv run --extra dev pytest -q -m acceptance
N4X_NEO4J_REQUIRED=1 uv run --extra dev pytest -q -m neo4j
```

Markers have distinct meanings:

- `contract`: GraphStore and unit-of-work behavior.
- `neo4j`: authenticated local Neo4j parity; `N4X_NEO4J_REQUIRED=1` fails fast when configuration is absent.
- `acceptance`: generic MCP reconstruction and app behavior.
- `pnpm`: real Experience Surface builds.

Run local smoke checks:

```bash
uv run n4x health
uv run n4x smoke-memory
uv run n4x smoke-python-env
uv run n4x bootstrap-neo4j --dry-run
```

Start the local N4X runtime. The Host reverse-proxies to one System worker, which serves browser Surfaces and MCP HTTP. `n4x serve` and `n4x mcp-stdio` cannot run at the same time. Development mode (the default) also starts MCP Inspector:

```bash
uv run n4x serve --neo4j
```

Production mode still starts MCP HTTP, but not Inspector:

```bash
uv run n4x serve --neo4j --mode production
```

The runtime advertises:

```text
http://127.0.0.1:7744/health
http://127.0.0.1:7744/mcp
http://127.0.0.1:6274          # Inspector in development mode
```

AI clients should use the HTTP MCP endpoint on the running process. After `N4X_NEO4J_*` is configured, do not start a second MCP process by hand. `n4x mcp-stdio` remains only for clients that must spawn a stdio subprocess:

```bash
uv run n4x mcp-stdio --neo4j
```

Draft Applications and Experiences can be tested without activation by creating
one expiring `DevelopmentDeployment`. It pins candidate hashes, binds an empty
or bounded production-cloned DataSpace per Application, and exposes
deployment-qualified browser, MCP App, Action, object, relation, invocation-log,
and file-delivery routes in the same process. Production active revisions and
production DataSpaces are unchanged. The Action worker is a single-user
bounded supervisor, not a multi-tenant queue.

The active Office browser Surface is one SPA with route-level views:

```text
http://127.0.0.1:7744/experiences
http://127.0.0.1:7744/experience/office/
http://127.0.0.1:7744/experience/office/notes
http://127.0.0.1:7744/experience/office/mail
http://127.0.0.1:7744/experience/office/calendar
```

The browser SPA and compact chat-oriented MCP App Surfaces share one active
`office` Experience revision, source tree, dependency scope, theme profile, and
activation lifecycle. Notes, Mail, and Calendar remain independent backend
Applications. Browser code uses explicitly allowlisted Experience HTTP
capabilities. MCP App resources use their app-only `callTool` bridge tools and
are revision-qualified under `ui://n4x/experience/office/revision/.../surface/...`.
The browser catalog excludes MCP-only Surfaces; MCP resource discovery excludes
the browser Surface. The canonical release contains exactly one browser Surface
(`browser`) and four `mcp_app` Surfaces (`summary`, `note`, `message`, `events`).

For every browser HTML response, the host replaces or injects a canonical
`<base href="/experience/{experience_id}{mount_path}/">`. Relative scripts,
styles, images, fetches, and links therefore resolve from the selected Surface
root even on deep SPA routes. Root-relative and absolute URLs keep ordinary
browser behavior; N4X does not rewrite them.

If startup reports a graph metamodel mismatch, point N4X at a dedicated
development database and reset it explicitly:

```text
reset_dev_graph  (MCP; confirmation_database = the dedicated development database)
```

This deletes every node, recreates the schema/root, and stamps v6. Back up
anything needed before running it; older prototype graphs are not migrated.
This is a development reset. Ordinary System updates do not wipe the database.

Local Docker Neo4j can also run on alternate ports when `7474`/`7687` are already in use:

```bash
docker volume create n4x_neo4j_data
docker run -d --name n4x-neo4j \
  -p 17474:7474 -p 17687:7687 \
  -e NEO4J_AUTH=neo4j/<local-password> \
  -v n4x_neo4j_data:/data \
  neo4j:5-community

export N4X_NEO4J_URI=bolt://localhost:17687
export N4X_NEO4J_USER=neo4j
export N4X_NEO4J_PASSWORD=<local-password>
export N4X_NEO4J_DATABASE=neo4j
uv run n4x bootstrap-neo4j
```

Run a Neo4j System smoke write:

```bash
uv run n4x smoke-neo4j
```

Create secret metadata and store a value outside Neo4j:

```bash
uv run n4x secret-create --application-id mail --uri secret://mail/password --name "Mail password" --neo4j
uv run n4x secret-set --application-id mail --uri secret://mail/password --name "Mail password" --neo4j
```

`secret-set` prompts for the value and stores it in macOS Keychain when available. The graph stores only `SecretReference` metadata.
