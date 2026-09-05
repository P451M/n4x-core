# N4X V1 Specification

This is the implementer specification. Strangers start at
[README.md](README.md) and the product docs at
[https://n4x.emergingthoughts.org/docs](https://n4x.emergingthoughts.org/docs).

## 1. Purpose

N4X is a trusted local graph-native application runtime. It lets an AI create and update local backend Applications and independently revisioned frontend Experiences. Applications own graph capabilities and data. Experiences are coherent UI applications and release boundaries: they own presentation source, frontend dependencies, browser and MCP App surfaces, and access declarations.

N4X is MCP-first. The primary authoring interface is FastMCP / the MCP Python SDK. The local runtime command `n4x serve` starts the constitutional **Host** (HTTP reverse proxy, install file lock, worker supervisor) and one **System** worker; the worker serves Experience HTTP and MCP HTTP. Without MCP on startup N4X is not usable. Development mode also starts MCP Inspector. `n4x serve` and `n4x mcp-stdio` are mutually exclusive (install file lock). `n4x mcp-stdio` starts the host lock and a stdio-only System worker. Local HTTP serves browser surfaces, browser bridge APIs, callback routes, MCP transport, local development, and Inspector. Browser and MCP App surfaces may have separate entrypoints and implementations; N4X does not derive one host's UI from the other. System MCP initialize instructions plus `inspect_client_guide` are the client-usage contract; UI remains graph-owned `inspect_experience_design_context`.

V1 optimizes for power, fast AI iteration, transparency, checkpointing, rollback, and local control. It is not a hostile-code sandbox. AI-authored graph-app code is trusted local automation code.

The runtime has three product layers:

- **Host** — process supervisor, reverse proxy, install file lock, official zip import, materialize of enabled System SourceFiles, localhost enable HTTP. It uses the installed adapter (Bolt, SourceStore, kernel models). It never imports System Python. `n4x host enable` and `enable_system_revision` point the graph enabled edge and restart the worker.
- **System** — one worker process whose Python is the enabled `SystemRevision` SourceFiles. It owns Application and Experience authoring, activation, packages, jobs, MCP, and Experience HTTP. `SystemRuntime` is the worker composition root. The official zip is an import seed, not what runs.
- **Application / Experience** — graph-owned product behavior. They never import Host, System, or persistence internals.

**Kernel** in this specification is the architectural name for generic mechanism (transactions, secrets, process lifecycle, Experience authorization, hosting). It is not a Python composition class and is not the `n4x serve` process. Product CLI and MCP/HTTP serving use Host plus System.

An **Application** is the backend graph capability and data boundary. It owns schema, Python source and dependencies, actions, tests, migrations, provider integrations, triggers, and runtime data. An **Experience** is an independently revisioned UI application and atomic frontend release boundary. It owns TypeScript/React source and dependencies, `ui_profile`, activation, and immutable **ExperienceSurface** declarations. A Surface is a separately built entrypoint of kind `browser` or `mcp_app`; “MCP App” or “widget” is user-facing terminology for an `mcp_app` Surface, not a separately revisioned domain entity. A **Package** is the reserved distribution/export noun: a portable archive of Applications and Experiences, not a runtime capability or presentation boundary. Applications and Experiences depend only on stable N4X contracts and never import System services or persistence implementations.

Enabling a SystemRevision rematerializes that revision's current SourceFiles and stop-then-starts the worker. The Neo4j database, including Application and Experience data, stays in place. The official zip imports another revision; it does not enable. Heal broken System source with the same source tools as apps, then enable again. Exporting Packages and wiping Neo4j is a **development reset** of an install, not the production System-update path.

### 1.1 Lean Kernel, Powerful Trusted Applications

N4X defaults behavior and policy to graph-owned Applications, not to Host, System, or other generic mechanism. Trusted Applications may implement specialized schemas, validation, queries, indexing, files, idempotency, concurrency policy, provider protocols, scheduling policy, retries, and user-facing errors in their source. They may use stable N4X contracts and Cypher through the System-owned gateway.

A new Host, System, or other generic-runtime capability is justified only when all of the following are true:

1. An Application cannot implement the requirement correctly through app-owned source, actions, triggers, callbacks, secret references, shared app libraries, or Cypher.
2. Correctness requires authority held exclusively by Host or System: graph transaction commit, secret-backend access, process/runtime lifecycle, Experience authorization, HTTP/MCP hosting, or Host import/enable of System source.
3. The capability is generic without interpreting Application domain nouns, fields, workflows, provider protocols, or business rules.
4. The mechanism is the minimum and leaves policy optional and Application-owned wherever Applications may reasonably choose different behavior.
5. The proposal includes a concrete failing Application use case; anticipated convenience, uniformity, inefficient app code, or possible future reuse is not sufficient evidence.

Before adding Host or System code, prefer in this order:

1. Application source or an app-owned action;
2. an Experience implementation;
3. a reusable graph-app library, authoring pattern, or blueprint;
4. an existing generic contract, including Cypher through the gateway;
5. only then, the smallest new Host or System mechanism.

Host and System do not protect trusted Applications from every inefficient or undesirable design choice. Operational safeguards may bound shared runtime resources, but they must not become a restrictive domain framework. A mechanism that would reduce Application power, force one query/validation/consistency model, or move provider behavior into core is rejected unless required to preserve a non-negotiable graph, secret, process, authorization, or hosting invariant.

Before adding Host, System, or other generic-runtime functionality, the question remains: **Why can this not be implemented by a trusted Application?** If the answer is not compelling, keep it out of Host and System.

## 2. Non-Negotiable Architecture

- Lean Kernel and powerful trusted Applications is an architectural invariant. Host and System proposals must pass §1.1; the default owner of new behavior is an Application or Experience.
- `n4x serve` is Host plus one System worker. System enable is rematerialize plus stop-then-start. Host never imports System Python.
- The control graph owns the enabled SystemRevision (`(:System)-[:ACTIVE_REVISION]->(:SystemRevision)`). That edge is SoT. Host has an install file lock (one `n4x serve`); the lock is not a System policy store. There is no last-known-good pointer.
- Neo4j is the authoritative store for System source, application definitions, source, runtime objects, relationships, and provenance. If Neo4j is down, the instance is down.
- Large opaque byte payloads may live in an Application data volume when they are unsuitable for Neo4j. The graph remains semantic authority for their app-owned identity, relative reference, metadata, and relationships; the volume is authoritative only for the referenced bytes.
- Graph-app source is canonical as interned `SourceContent` blobs pointed at by `SourceTree` `HAS_FILE` edges. `SourceFile` is a read DTO. Filesystem source trees are materialized runtime artifacts only.
- Process-local maps may be disposable read caches or working sets, but they are never a durable mutation mechanism or repair authority.
- Every durable mutation is expressed through one `GraphUnitOfWork`. Related node writes, structural edges, active-edge replacements, and app relations commit in one backing-store transaction or not at all.
- A `GraphUnitOfWork` must not remain open across a wait that another thread or process needs in order to use the store. Action subprocesses, Cypher IPC, `uv`/`pnpm` builds, and scheduler-run actions are waits. Lookup and commit transactions close before those waits; results persist in a later unit of work.
- Relationships are graph facts. Normal runtime code must create relationships when the related nodes/facts are created.
- No normal runtime path may depend on later relationship sync to make the graph coherent.
- `N4X_KERNEL` is not used in the normal graph model.
- Generic `N4X_RELATION` is not used for new app/domain relationships.
- `ApplicationObject.object_type_id` is canonical. Short names are display/input conveniences only.
- App/domain relationships use generated physical Neo4j relationship types derived from `RelationType`.
- Generic runtime code remains mechanism-only. Provider-specific logic such as IMAP, SMTP, CalDAV, Gmail, Outlook, Notion, or GMX, plus domain validation, query semantics, idempotency, retries, and conflict policy, belongs to graph-owned application source.
- The isolated-development reset establishes `n4x.graph.metamodel.v6` as a fresh
  baseline. There is no predecessor graph/data migration or compatibility
  reader. Wipe and recreate instances. This wipe/import path is a development
  reset of an install, not how production System revisions are activated.
- Proof apps are not created by checked-in app-specific seed scripts. They are authored through the same generic MCP tools and authoring guidance that an AI client uses for new apps.
- Applications never own frontend source, JavaScript dependencies, `ui_profile`, Surface definitions, or Experience activation.
- An Experience may consume multiple Applications only through capabilities allowlisted by its active `ExperienceRevision`.
- An `ExperienceRevision` is the only frontend revision and activation boundary. Surfaces do not have independent revisions or active edges.
- Browser and MCP App Surfaces may use separate source and builds. They share Experience ownership, release, capability authorization, and backend contracts, not necessarily components or rendered HTML.
- Separate bridge action calls remain separate transactions and are non-atomic as a group.

## 3. Graph Layers

N4X uses six graph layers. They are logical layers in one Neo4j database.

### 3.1 Control graph

The control graph (in Neo4j) owns the platform System, applications, revisions, source trees, lifecycle, checkpoints, and root reachability. Host reads the enabled System edge from this graph. Host's install file lock is not this layer.

Required relationships:

```cypher
(:N4XRoot {id: "n4x"})-[:HAS_SYSTEM]->(:System {id: "n4x"})
(:System)-[:HAS_REVISION]->(:SystemRevision)
(:System)-[:ACTIVE_REVISION]->(:SystemRevision)
(:SystemRevision)-[:HAS_SOURCE_TREE]->(:SourceTree)
(:N4XRoot {id: "n4x"})-[:HAS_AUTHORING_GUIDE]->(:AuthoringGuide)
(:AuthoringGuide)-[:HAS_REVISION]->(:AuthoringGuideRevision)
(:AuthoringGuide)-[:ACTIVE_REVISION]->(:AuthoringGuideRevision)
(:N4XRoot {id: "n4x"})-[:HAS_UI_THEME]->(:UiTheme)
(:UiTheme)-[:HAS_REVISION]->(:UiThemeRevision)
(:UiTheme)-[:ACTIVE_REVISION]->(:UiThemeRevision)
(:N4XRoot {id: "n4x"})-[:HAS_BLUEPRINT]->(:AppBlueprint)
(:AppBlueprint)-[:HAS_REVISION]->(:BlueprintRevision)
(:AppBlueprint)-[:ACTIVE_REVISION]->(:BlueprintRevision)
(:N4XRoot {id: "n4x"})-[:HAS_APPLICATION]->(:Application)
(:Application)-[:HAS_REVISION]->(:ApplicationRevision)
(:Application)-[:ACTIVE_REVISION]->(:ApplicationRevision)
(:ApplicationRevision)-[:PARENT_REVISION]->(:ApplicationRevision)
(:ApplicationRevision)-[:HAS_SOURCE_TREE]->(:SourceTree)
(:SystemRevision)-[:HAS_SOURCE_TREE]->(:SourceTree)
(:SourceTree)-[:HAS_FILE {path, role, language, size}]->(:SourceContent)
(:Application)-[:HAS_CHECKPOINT]->(:GraphCheckpoint)
(:GraphCheckpoint)-[:CAPTURES_REVISION]->(:ApplicationRevision)
(:Application)-[:HAS_DATA_SPACE]->(:DataSpace)

(:N4XRoot {id: "n4x"})-[:HAS_DEVELOPMENT_DEPLOYMENT]->(:DevelopmentDeployment)
(:DevelopmentDeployment)-[:PINS_EXPERIENCE_REVISION]->(:ExperienceRevision)
(:DevelopmentDeployment)-[:PINS_APPLICATION_REVISION]->(:ApplicationRevision)
(:DevelopmentDeployment)-[:BINDS_DATA_SPACE]->(:DataSpace)

(:N4XRoot {id: "n4x"})-[:HAS_EXPERIENCE]->(:Experience)
(:Experience)-[:HAS_REVISION]->(:ExperienceRevision)
(:Experience)-[:ACTIVE_REVISION]->(:ExperienceRevision)
(:ExperienceRevision)-[:PARENT_REVISION]->(:ExperienceRevision)
(:ExperienceRevision)-[:HAS_SOURCE_TREE]->(:SourceTree)
(:ExperienceRevision)-[:DECLARES_APPLICATION]->(:Application)
```

`N4XRoot` is created during schema bootstrap. Every durable control/app node created through normal APIs must have an explicit ownership path from `N4XRoot` or its owning `Application`. Validation must follow declared ownership relationships rather than depend on an arbitrary maximum traversal depth.

`UiTheme` and `AuthoringGuide` are control-graph facilities owned only by `N4XRoot`; neither they nor their immutable release revisions carry `application_id`. System release bootstrap atomically publishes matching, explicitly versioned theme and guide revisions and moves their single `ACTIVE_REVISION` edges. Applications can select a consumption profile but cannot own, create, update, or activate platform theme or guide revisions.

### 3.2 App Schema Graph

Applications define object types, relation types, actions, triggers, tests, and migration `ActionRevision` records. Experience Surfaces are deliberately absent because they belong to Experience revisions.

Required relationships:

```cypher
(:Application)-[:DEFINES_OBJECT_TYPE]->(:ObjectType)
(:Application)-[:DEFINES_RELATION_TYPE]->(:RelationType)
(:Application)-[:DEFINES_ACTION]->(:Action)
(:Application)-[:DEFINES_TRIGGER]->(:Trigger)
(:Application)-[:HAS_SECRET_REFERENCE]->(:SecretReference)
(:Application)-[:HAS_CREDENTIAL]->(:CredentialRecord)
(:Application)-[:HAS_CALLBACK_ROUTE]->(:CallbackRoute)

(:ApplicationRevision)-[:DECLARES_DEPENDENCY]->(:RuntimeDependency)
(:ApplicationRevision)-[:HAS_ACTION_REVISION]->(:ActionRevision)
(:ApplicationRevision)-[:HAS_OBJECT_TYPE_REVISION]->(:ObjectTypeRevision)
(:ApplicationRevision)-[:HAS_RELATION_TYPE_REVISION]->(:RelationTypeRevision)
(:ApplicationRevision)-[:HAS_TRIGGER_REVISION]->(:TriggerRevision)
(:ApplicationRevision)-[:HAS_TEST]->(:TestCase)
(:ApplicationRevision)-[:HAS_VALIDATION_REPORT]->(:ValidationReport)

(:ObjectType)-[:HAS_REVISION]->(:ObjectTypeRevision)
(:ObjectType)-[:ACTIVE_REVISION]->(:ObjectTypeRevision)

(:RelationType)-[:HAS_REVISION]->(:RelationTypeRevision)
(:RelationType)-[:ACTIVE_REVISION]->(:RelationTypeRevision)
(:RelationTypeRevision)-[:FROM_TYPE]->(:ObjectType)
(:RelationTypeRevision)-[:TO_TYPE]->(:ObjectType)

(:Action)-[:HAS_REVISION]->(:ActionRevision)
(:Action)-[:ACTIVE_REVISION]->(:ActionRevision)
(:ActionRevision)-[:DEPENDS_ON]->(:RuntimeDependency)
(:ActionRevision)-[:USES_SECRET]->(:SecretReference)

(:Trigger)-[:HAS_REVISION]->(:TriggerRevision)
(:Trigger)-[:ACTIVE_REVISION]->(:TriggerRevision)
(:TriggerRevision)-[:INVOKES]->(:Action)

(:TestCase)-[:TESTS]->(:Action)
(:CredentialRecord)-[:USES_SECRET]->(:SecretReference)
(:CallbackRoute)-[:TARGETS]->(:ActionRevision)
```

Schema changes create revisions. Active definitions are never silently edited in place.

### 3.2.1 Experience and Surface Graph

An `Experience` has identity and lifecycle independent of every Application it consumes. Its immutable `ExperienceRevision` is the unit of frontend source, dependency, UI-profile, capability declaration, Surface declaration, validation, build, and activation.

Required relationships:

```cypher
(:Experience)-[:HAS_REVISION]->(:ExperienceRevision)
(:Experience)-[:ACTIVE_REVISION]->(:ExperienceRevision)
(:ExperienceRevision)-[:PARENT_REVISION]->(:ExperienceRevision)
(:ExperienceRevision)-[:HAS_SOURCE_TREE]->(:SourceTree)
(:ExperienceRevision)-[:DECLARES_DEPENDENCY]->(:RuntimeDependency)
(:ExperienceRevision)-[:DECLARES_APPLICATION]->(:Application)
(:ExperienceRevision)-[:ALLOWS_OBJECT_TYPE]->(:ObjectType)
(:ExperienceRevision)-[:ALLOWS_RELATION_TYPE]->(:RelationType)
(:ExperienceRevision)-[:ALLOWS_ACTION]->(:Action)
(:ExperienceRevision)-[:DECLARES_SURFACE]->(:ExperienceSurface)
```

`DECLARES_APPLICATION` identifies the Applications an Experience revision consumes. The three `ALLOWS_*` relationships are the least-privilege bridge allowlist and must target definitions owned by a declared Application. An allowlisted relation remains wholly owned by one Application and both endpoints remain objects of that same Application.

An `ExperienceSurface` is an interned declaration. Its storage `id` is the declaration hash. An ExperienceRevision points at it with `DECLARES_SURFACE`. It has no `HAS_REVISION` or `ACTIVE_REVISION` edge. Every Surface declares one `kind` (`browser` or `mcp_app`), one source entrypoint, its source-path closure, host metadata, and a CSP policy. Updating, adding, or deleting a Surface on a draft replaces the `DECLARES_SURFACE` pointer.

The canonical declaration is a discriminated record:

```yaml
experience_revision_id: string
surface_id: string
kind: browser | mcp_app
entrypoint: relative source path
source_paths: [relative source path]
title: string
description: string | null
csp:
  connect_domains: [origin]
  resource_domains: [origin]
browser:
  mount_path: /relative/path        # required only for kind=browser
  pwa: {} | omitted                 # opt-in installable hosting; valid only on mount_path /
  # pwa.manifest_path defaults to manifest.webmanifest (artifact-relative)
mcp_app:
  related_browser_path: /path | null # valid only for kind=mcp_app
  metadata: object
```

Host-specific blocks are mutually exclusive. Paths are normalized and traversal-safe. CSP origins are validated absolute origins; wildcards require an explicitly supported policy rather than passing through unchecked metadata.

A browser Surface is a separately built browser application entrypoint. It declares a `mount_path` relative to `/experience/{experience_id}`. An Experience revision may declare multiple browser Surfaces. At most one may use `/` as the default; explicit mount paths are unique, and the longest matching explicit mount path wins before the default. Ordinary pages and client-side routes remain internal to a browser Surface and are not graph entities.

Optional `browser.pwa` opts that `/` browser Surface into installable web-app hosting. Omit it and the Surface is not a PWA. There is no `kind: pwa`, no Application field, and no convert-Experience tool. A second independently installable UI is a second Experience. MCP App Surfaces cannot declare `pwa`. The Experience owns the web manifest file (name, icons, `display`, and unknown members). The Kernel does not inject `<link rel="manifest">` and does not register a service worker.

The browser host anchors every served HTML document to the selected Surface
root. It replaces the first existing `<base>` element or injects one immediately
inside `<head>` with the canonical href
`/experience/{experience_id}{mount_path}/` (the `/` mount becomes
`/experience/{experience_id}/`). Relative scripts, stylesheets, images, module
imports, fetches, and links therefore resolve from the Surface root even when
the browser loads a deep SPA route. Root-relative and absolute URLs retain
normal browser semantics; N4X does not rewrite them.

An MCP App Surface is a purpose-built embedded UI entrypoint advertised through MCP. It may declare an optional `related_browser_path` for `ui/open-link`, but N4X does not require it to mirror a browser Surface or derive its source from one. Separate Surfaces may voluntarily import common files from the Experience source tree.

Deleting an Experience never deletes an Application or its data, except `delete_working_set` on an Experience root, which exports that active Package closure with data and then deletes the Experience and every Application in the export. An Application cannot be deleted while an Experience revision references it unless those references are first migrated or removed. Experience activation does not activate or mutate any Application revision.

### 3.3 App Data Graph

App data is stored in an Application-owned `DataSpace` as `ApplicationObject`
nodes plus app-owned physical relationship types. Every Application has exactly
one production DataSpace and may have isolated development DataSpaces. A
DataSpace is a System transaction/hosting scope, not an Application schema or
provider abstraction.

```cypher
(:Application)-[:HAS_DATA_SPACE]->(:DataSpace)
(:DataSpace)-[:OWNS_OBJECT]->(:ApplicationObject)
(:ApplicationObject)-[:INSTANCE_OF]->(:ObjectType)
(:ApplicationObject)-[:CONFORMS_TO]->(:ObjectTypeRevision)
```

`ApplicationObject` fields:

```yaml
id: string
application_id: string
data_space_id: string
object_type_id: string
values: map
created_at: datetime
updated_at: datetime
```

`object_type_id` is semantically authoritative and should be globally stable
inside the app namespace, for example `mail.Message` or `notes.Note`.
`ApplicationObject` physical identity and uniqueness are
`(application_id, data_space_id, id)`; Application source continues to observe
the logical `id`.

### 3.4 App Relation Graph

`RelationType` defines semantic relationships between object types.

`RelationTypeRevision` fields:

```yaml
id: string
relation_type_id: string
application_revision_id: string
name: string
from_object_type_id: string
to_object_type_id: string
physical_type: string
properties: map
created_at: datetime
content_hash: string
```

System deterministically generates `physical_type` from `relation_type_id`, for example:

```text
mail.account_message -> APP_REL_MAIL_ACCOUNT_MESSAGE_EDBEC1F3
```

Runtime relationships are Neo4j relationships between `ApplicationObject` nodes:

```cypher
(:ApplicationObject {object_type_id: "mail.MailAccount"})
  -[:APP_REL_MAIL_ACCOUNT_MESSAGE_EDBEC1F3 {
      id,
      application_id,
      data_space_id,
      relation_type_id,
      relation_type_revision_id,
      values,
      created_by_invocation_id,
      updated_by_invocation_id,
      created_at,
      updated_at
    }]->
(:ApplicationObject {object_type_id: "mail.Message"})
```

The semantic authority is `relation_type_id`. The physical type exists for graph-native traversal, query ergonomics, and performance.

Normal APIs enforce:

- relation type belongs to the application
- source and target objects belong to the same Application and DataSpace
- source object type matches the active relation revision's `from_object_type_id`
- target object type matches the active relation revision's `to_object_type_id`

The physical relation identity is `(application_id, data_space_id, id)`. N4X
stays permissive about relation cardinality and property schemas unless an app
explicitly asks validation to fail on those issues.

### 3.5 Execution and Provenance Graph

Action and trigger execution is recorded as graph structure.

```cypher
(:ActionRevision)-[:HAS_INVOCATION]->(:Invocation)
(:Invocation)-[:RAN]->(:ActionRevision)
(:Invocation)-[:CREATED_OBJECT]->(:ApplicationObject)
(:Invocation)-[:UPDATED_OBJECT]->(:ApplicationObject)
(:Application)-[:HAS_JOB]->(:JobRecord)
(:TriggerRevision)-[:HAS_JOB]->(:JobRecord)
(:JobRecord)-[:HAS_ATTEMPT]->(:JobAttempt)
(:JobRecord)-[:CURRENT_ATTEMPT]->(:JobAttempt)
(:JobRecord)-[:INVOKED]->(:Invocation)
(:JobAttempt)-[:INVOKED]->(:Invocation)
(:CallbackRoute)-[:INVOKED]->(:Invocation)
(:Application)-[:HAS_CYPHER_AUDIT]->(:CypherAuditRecord)
(:Invocation)-[:HAS_CYPHER_AUDIT]->(:CypherAuditRecord)
```

Neo4j relationships cannot be targets of relationships, so relation provenance is stored on app relation edge properties. If an app needs richer relation lifecycle semantics, it should define an ordinary object type such as `Membership`, `Assignment`, or `ThreadMembership`.

The kernel does not write `CREATED_OBJECT` or `UPDATED_OBJECT`. Those edges remain so an Application may write them.

### 3.6 Artifact Graph

Runtime artifacts are rebuildable and linked to their definitions.

```cypher
(:ApplicationRevision)-[:HAS_BUILD_INVOCATION]->(:BuildInvocation)
(:ExperienceRevision)-[:HAS_BUILD_INVOCATION]->(:BuildInvocation)
(:ApplicationRevision)-[:USES_PYTHON_ENV]->(:PythonEnvironment)
(:ExperienceRevision)-[:USES_JAVASCRIPT_ENV]->(:JavaScriptEnvironment)
(:BuildInvocation)-[:PRODUCED]->(:BuildArtifact)
```

Materialize and Surface-build cache keys are the interned declaration hash plus blob hashes from the revision's current tree. Environments and build artifacts hang off the Application or Experience revision, not interned declarations.

Runtime artifacts can be deleted and rebuilt from graph source, dependency declarations, locks, package registries, and asset references.

## 4. Transactional Persistence and Internal Architecture

N4X uses the following dependency direction:

```text
MCP / HTTP / CLI adapters
  -> application services
  -> domain policies
  -> GraphUnitOfWork and repositories
  -> Neo4j / artifact / secret / process adapters
```

Transport adapters validate and serialize requests but contain no graph-app or provider behavior. System services implement authoring, lifecycle, invocation, scheduling, and inspection use cases. Domain policies define revision, ownership, validation, and lifecycle rules. Infrastructure adapters implement ports owned by the generic runtime.

`SystemRuntime` is the System worker composition root. Unrelated use cases must not be implemented only in one adapter; they belong in System services that HTTP, MCP, and CLI all call.

The System worker writes through a `GraphUnitOfWork` backed by `GraphStore` and focused repositories. Repositories include application, definition, source, object, relation, job, and artifact repositories.

Core persistence contracts:

- `GraphStore` implements primitive durable node, edge, app-relation, and Cypher operations for one backing store.
- `GraphUnitOfWork` owns begin, commit, rollback, nesting/reuse, and access to repositories bound to one transaction.
- nested service and repository calls reuse the active unit of work; they must not open a second backing-store transaction for the same use case.
- a repository call with no active unit of work may begin and commit one of its own.
- `GraphIntegrityService` validates and repairs declared ownership/revision invariants using durable records.
- bootstrap administration manages initial schema creation outside ordinary mutations; later persistent-format changes use migrations.

### 4.1 Transaction scope and wait boundaries

A unit of work is a transaction and resource-ownership boundary, not a logical use-case span. A Neo4j transaction is session/thread-bound and may hold database locks. The in-memory test store serializes transactions on the opening thread. The action Cypher gateway serves authenticated local IPC on a different thread from the parent action process.

A wait boundary is any blocking operation that another thread or process may need the store to complete, or that can leave a transaction open for an unbounded external duration. Examples are waiting for an action subprocess, waiting while that subprocess uses `CypherGateway`, `uv` environment preparation, `pnpm` / Vite builds, and scheduler-run actions. A gateway request handler may open its own short unit of work; the parent must not hold one while waiting for that request.

Therefore a unit of work must not stay open across a wait boundary. Starting an external wait while `GraphUnitOfWork.is_active` is a programming error. The orchestrating application service owns transaction scope and must close its transaction before calling a waiting runtime operation. Runtime managers (`ActionRuntime`, `PythonEnvironmentManager`, `ExperienceSurfaceRuntime`) must reject an inherited active unit of work; they must never close a caller-owned transaction.

Allowed wrapping:

- nested in-process service and repository calls reuse the active unit of work
- a repository call with no active unit of work may begin and commit one of its own
- a stage or use case that only writes graph records should use one unit of work for those related writes
- a stage or use case that waits uses the lookup/claim then persist shape below

Forbidden wrapping:

- `@transactional` or `with uow` around action subprocess waits, the lifetime of a Cypher IPC server, `uv`, or `pnpm`; individual gateway requests still use short handler-owned units of work
- holding a lease or invocation transaction open “so the whole use case is atomic”
- treating Cypher audits or app Cypher as part of an open System catalog unit of work

Required wait shape:

```text
open unit of work -> durable lookup or claim -> commit/close
wait on subprocess, IPC, uv, or pnpm
open unit of work -> persist results -> commit/close
```

Cypher audits commit in the gateway's own unit of work during the action process, in the same transaction as the app statements for that `run_cypher` or `transaction()` burst. The action process is not a transaction. Implicit `run_cypher` commits immediately; a failed action can leave earlier bursts committed.

Application and Experience activation is a single pass (see §10). Waits (dependency resolve, Surface builds, migration subprocesses) run with no active unit of work. Freeze and `ACTIVE_REVISION` replacement each use one unit of work. A failed activate never switches the active edge.

Holding the parent transaction across those waits is a deadlock or isolation defect, not an atomicity feature.

Required implementations:

- `InMemoryGraphStore` for tests
- `Neo4jGraphStore` for local runtime

Required `GraphStore` capabilities:

```text
upsert_node
delete_node
create_edge
delete_edge
replace_single_edge
create_app_relation
delete_app_relation
list_app_relations
run_cypher
```

Required administration capabilities:

```text
bootstrap_schema
```

Required `GraphIntegrityService` capabilities:

```text
validate_graph_shape
repair_graph_edges
```

Application services must express graph mutations explicitly and commit related node/edge changes through one `GraphUnitOfWork`.

Mutable dict persistence hooks are prohibited as a durable write path. In-memory maps may remain as disposable caches, but durable Neo4j writes are driven by explicit unit-of-work/repository operations.

The in-memory and Neo4j implementations must satisfy the same behavioral contract for nodes, structural edges, app relations, active-edge replacement, rollback on failure, and validation. No Neo4j runtime read may silently fall back to process memory when the store returns no records.

## 5. Source Store

Source is graph-owned.

Required nodes:

- `SourceTree`
- `SourceContent`

`SourceFile` is a read DTO hydrated from `HAS_FILE` properties plus the blob. There is no `SourceChange` node.

Required behavior:

- interned trees are shared; `id` is `tree_hash`; they have no owner fields
- a working tree has `id == {revision_id}.source`, `status=draft`, and exactly one inbound `HAS_SOURCE_TREE` from that draft
- first revision with no parent points at the interned empty tree
- a new draft copies `HAS_*` / `DECLARES_*` edges from its parent, including `HAS_SOURCE_TREE`
- the first write copy-on-writes a working tree; later writes mutate that tree
- activate interns the listing by `tree_hash` and deletes the working tree
- Python/backend source remains under Application revisions; TypeScript/React frontend source remains under Experience revisions
- source MCP tools take `revision_id`; payloads still include a derived `source_tree_id`
- `expected_hash` and `expected_tree_hash` detect conflicts
- batch source writes are atomic within the active `GraphUnitOfWork`
- filesystem copies are materialized runtime artifacts and are never source authority
- `apply_source_patch` locates each unified-diff hunk by unique `-` / ` ` context; `@@` line numbers are a hint; zero or two-plus matches write nothing
- `search_source_tree` returns a capped set of regex matches with optional context
- `read_source_file` may return a 1-based line range together with `total_lines` and `content_hash`
- discard, copy-on-write, intern, and working-set delete collect interned
  blobs, trees, and declarations with no inbound edge and no in-flight
  Invocation / JobRecord / CallbackRoute pin; leftover orphans stay integrity
  warnings, not failures

### 5.1 Platform Authoring Facilities

`UiTheme`, `UiThemeRevision`, `AuthoringGuide`, `AuthoringGuideRevision`, `AppBlueprint`, and `BlueprintRevision` are generic authoring facilities, not Applications or Experiences and not alternative runtime definitions.

- durable UI themes, authoring guides, and blueprints are reachable from `N4XRoot`
- theme and guide content lives in immutable, system-published revisions; stable nodes contain identity and the active revision pointer
- packaged CSS, guide, and manifest assets seed one deterministic release into the graph, while the active graph revisions remain runtime authority
- publishing a changed theme or guide requires a new release version and atomically activates both matching revisions
- MCP exposes theme and guide inspection, never user/app create, update, or activation tools
- blueprint revisions are immutable and use one `ACTIVE_REVISION` edge
- blueprint content has a versioned, validated schema
- complete structural and semantic preflight runs before app instantiation writes anything
- instantiation delegates to the same application services used by MCP authoring
- blueprints never contain provider behavior in Host or System code
- an installed `ApplicationRevision`, not its originating blueprint, is runtime authority

## 6. Actions

`ActionRevision` is the only executable abstraction.

Migration code is `ActionRevision.kind = "migration"`. Those revisions run only during activation, once, after a checkpoint taken from `migration_metadata.mutates_application_data`. There is no kernel dry-run, operation plan, or `supports_dry_run` stage. An author who wants a preview writes a normal action that only `MATCH`es.

Actions run trusted Python source from the ApplicationRevision's current tree. Dependencies are interned `RuntimeDependency` nodes declared by the revision and installed into per-`ApplicationRevision` `uv` environments. Materialize cache keys are declaration hash plus blob hashes from that tree.

Action context (`n4x.action.context.v2`):

```python
ctx.application_id
ctx.data_space_id
ctx.secrets.get(uri)
ctx.graph.run_cypher(query, params=None)
ctx.graph.run_app_cypher_read(query, params=None)
ctx.graph.run_app_cypher_write(query, params=None)
with ctx.graph.transaction():
    ctx.graph.run_cypher(...)
```

There is no object/relation snapshot and no operation buffer. Apps mutate data with Cypher. System holds the Bolt driver and sessions. The app chooses transaction bounds:

- `run_cypher` opens one session, one transaction, commits, and closes.
- `with ctx.graph.transaction():` holds one session for the block; commit on clean exit; rollback on exception, child death, action timeout, or transaction time bound.

Session is not keyed by Application or action process. Concurrent actions are concurrent sessions; they see each other's commits. Last writer wins unless the Cypher does compare-and-set. Do not wait on provider I/O inside `transaction()`.

The kernel does not stamp `application_id` or `data_space_id`. Apps set them from `ctx`. Store fields on `ApplicationObject.values` (a map). In Cypher, assign that field as JSON text: Neo4j properties cannot be maps. Kernel repository writes encode and decode it. Experience list and checkpoints read that convention; flattened node properties do not appear in Experience list. Relationship type is `RelationTypeRevision.physical_type` (`APP_REL_…`); embed it in query text. Cypher cannot take a relationship type as a parameter.

Cypher is served through a System-owned `CypherGateway`. The gateway opens its own short unit of work (audit plus statements). It is not mounted on an open System catalog UoW. Apps have no Bolt driver or credentials. Activation rejects a revision that includes actions when the gateway cannot execute Cypher. Import/`check_only` does not open a write gateway.

The in-memory test adapter implements a limited Cypher subset for contracts. It is not a production Cypher engine.

Trusted app Cypher may read or write any data on the instance graph, including System nodes. Isolation of data between Applications is not a kernel job. Audit is the record, not a fence. Experience allowlists remain a UI capability list. `ctx.data_space_id` is advisory: a draft action can `MERGE` `data_space_id: 'production'`.

### 6.1 Action supervision

The System worker owns one generic bounded `ActionSupervisor` because only the
worker that hosts HTTP/MCP can safely admit shared work, manage subprocess
lifetime, keep the event loop responsive, renew scheduler leases, and fence
late results.
Applications still choose whether and how to split domain work, sync cursors,
batches, idempotency, retries, and the declared concurrency policy.

`n4x.action.supervisor.v1` defines:

- durable Invocation states `queued`, `running`, `succeeded`, `failed`, and
  `cancelled`, with timestamps, selected revision, `ExecutionContext`,
  correlation id, exit metadata, and bounded log-artifact references;
- one configurable worker bound and one bounded admission queue for the
  single-user runtime; queue rejection is explicit and never silently executes
  work on the request event loop;
- one process group per running Action, timeout and cancellation escalation,
  and late-result fencing so a cancelled or timed-out child cannot keep a
  gateway transaction open;
- child interpreters may be reused for a later invoke of the same
  ApplicationRevision, DataSpace, and prepared Python environment;
  a new environment, supersede, expire, timeout, cancel, crash, or
  shutdown kills those children;
- generic Action admission policies `default` and `reject_if_running`, scoped
  to stable Action identity, plus enforcement of declared Trigger overlap
  policies without interpreting Application fields or provider semantics;
- incremental, bounded, redacted stdout/stderr capture inspectable while work
  runs; log limits are host safeguards, not Application validation;
- linked Job heartbeat renewal while a scheduled child is running. Existing
  durable Job leases remain authoritative; distributed Invocation leases are
  not introduced.

Compatibility invoke operations submit and await the same supervised
Invocation. Async HTTP/MCP operations may submit, inspect/await, and cancel it.
No `GraphUnitOfWork` spans queue wait, child execution, log IO, or process
termination.

**Why can this not be implemented by a trusted Application?** An Application
cannot keep the host event loop responsive, reserve shared worker capacity,
terminate or fence a process it does not own, persist authoritative Invocation
lifecycle transitions around process failure, or renew a System-owned Job
lease. The mechanism is therefore limited to generic execution
authority; all domain workflow remains app-owned.

### 6.2 Application data volume

Every action subprocess receives `N4X_APPLICATION_DATA_ROOT`, an opaque
persistent POSIX directory selected from the Invocation's DataSpace. Production
and development DataSpaces have separate roots without exposing host paths or
mount names to Application source.

Applications use ordinary filesystem APIs and own path layout, relative references, checksums, staging, reconciliation, retention, deduplication, and every domain semantic. Graph objects store only app-relative references, never absolute host paths. The data volume survives action-process replacement, System worker restart, and Application revision activation when the deployment remounts the same persistent storage. It is not graph source authority, a derived build artifact, or a hostile-code sandbox.

`n4x.file.delivery.v1` is the generic host boundary for deliberately exposing a selected file to an Experience. A successful allowlisted Action may return a reserved delivery descriptor containing only an app-relative path and HTTP response metadata. The System worker validates that the resolved file remains beneath the selected data scope and replaces the private descriptor with a short-lived, versioned, signed URL bound to that scope, Application, Experience, active Experience revision, and expiry. The HTTP host revalidates those bindings and path confinement when serving `GET`, `HEAD`, or byte-range requests. No raw path or scope identifier is exposed to the Surface.

The runtime does not define `Blob`, `File`, attachment, media, bucket, upload-session, retention, or deduplication graph entities. It does not prevent trusted Application Python from accessing other host paths; confinement is enforced at System-controlled resolution and HTTP delivery boundaries.

## 7. Experiences, Surfaces, and Host Bridges

Experience source is graph-owned TypeScript/React built with pnpm and Vite/esbuild. One Experience revision may contain multiple independent browser applications and multiple purpose-built MCP Apps. All Surfaces share the Experience's source tree, dependency declarations, UI profile, Application access policy, validation, and atomic release, but each Surface has its own entrypoint, artifact, host metadata, and CSP. Source reuse is optional.

`ExperienceRevision.ui_profile` controls theme consumption. The default `n4x-default` profile materializes the active `UiThemeRevision.css_text` at `src/n4x-theme.css` and manages the platform frontend dependencies. `custom` and `none` do not inject them. The profile and active theme revision hash are build inputs. JavaScript `RuntimeDependency` records belong to the Experience revision. Applications have no frontend environment.

The Experience access layer derives authorization from the active Experience revision, not from client-supplied Application identity alone. Every Surface may list/read only allowlisted objects and relations and may invoke only allowlisted active Actions from Applications connected by `DECLARES_APPLICATION`. The access layer does not compose objects, relations, or actions into a new domain abstraction. It returns each Application's identifiers and payloads intact. Calls to two or more Actions are independent invocations and are not transactionally composed; clients must handle partial success explicitly.

Browser and MCP App Surfaces use host-native delivery and transport:

- A browser Surface is served as an ordinary browser application with normal assets, URLs, routing, navigation, and HTTP bridge calls. N4X injects no fetch or link monkey patches.
- An MCP App Surface is served as a self-contained `text/html;profile=mcp-app` resource. It uses MCP `tools/call` for N4X reads and actions and `ui/open-link` for a declared browser destination. It does not assume that its opaque sandbox origin is the N4X HTTP origin.
- The two hosts share authorization semantics and response schemas. They are not required to share an HTML document, bundle, router, or frontend bridge implementation.

Canonical browser routes are:

```text
GET  /experiences
GET  /experience/{experience_id}{surface_mount_path}[/{route_or_asset_path}]
GET  /bridge/contract
GET  /api/experiences/{experience_id}/apps/{application_id}/objects
GET  /api/experiences/{experience_id}/apps/{application_id}/relations
POST /api/experiences/{experience_id}/apps/{application_id}/actions/{action_id}/invoke
GET  /api/experiences/{experience_id}/apps/{application_id}/files/{token}
HEAD /api/experiences/{experience_id}/apps/{application_id}/files/{token}
GET  /api/experiences/{experience_id}/apps/{application_id}/secrets/{secret_reference_id}
PUT  /api/experiences/{experience_id}/apps/{application_id}/secrets/{secret_reference_id}
DELETE /api/experiences/{experience_id}/apps/{application_id}/secrets/{secret_reference_id}
```

The server resolves `{experience_id}` to its active revision, selects the browser Surface by longest matching explicit `mount_path` and then the optional `/` default, and serves only that Surface's artifact. Browser routing below the mount path belongs to the browser application. Missing extensionless paths SPA-fallback to the Surface index. Missing paths with a file extension return 404 and never HTML. `.webmanifest` is served as `application/manifest+json`.

When the matched `/` browser Surface declares `pwa`, the host serves the declared manifest (default `manifest.webmanifest`) by passing through author members and confine-and-resolving `start_url`, `scope`, and `id` against the live document base (`/experience/{experience_id}/` or the development prefix). Missing or `"/"` values become that base. URLs that escape the document base are rejected. The host never bakes `public_origin` into the manifest. Activation requires a JSON object at that artifact path; revisions without `pwa` skip this check.

Installability is authoring, not a Kernel transform. Same product: a new Experience revision sets `pwa: {}` on the `/` browser Surface and adds manifest source plus an HTML `<link rel="manifest">` (optional `sw.js` and `navigator.serviceWorker.register()`). New product identity: `create_experience`, then the same Surface opt-in. Desktop vs mobile is layout in one Experience, not two kinds.

Every active `mcp_app` Surface is advertised at a revision-qualified URI:

```text
ui://n4x/experience/{experience_id}/revision/{experience_revision_id}/surface/{surface_id}
```

The MIME type is `text/html;profile=mcp-app`. The model-visible open tool is bound to that resource. Surface data and mutation tools are app-visible, Experience-scoped, and server-bound to the active Experience revision and allowlist; an MCP App cannot obtain authority by supplying a different Experience id. Activation emits `notifications/resources/list_changed` and `notifications/tools/list_changed` when the active catalog changes. Revision-qualified URIs prevent hosts from reusing stale HTML after activation.

MCP CSP is emitted through standards-based `_meta.ui.csp`; narrowly scoped compatibility aliases for known hosts may be emitted only at the provider boundary. Browser CSP headers and MCP CSP metadata derive from the Surface declaration but may differ by host. Local MCP assets are bundled into the resource. External origins must be explicitly declared; permissive wildcard origins are not the production default.

Surface Hosting v4 has no Widget migration or host compatibility window.
Canonical browser routes, revision-qualified MCP App resources, and the
Experience-scoped bridge are the only Surface host contracts. Fresh authoring
never creates `Widget` or `WidgetRevision` records.

Experience builds occur during explicit build/activation work, never synchronously on first request. Every `BuildArtifact` records `experience_revision_id`, `surface_id`, host kind, input hash, and artifact manifest. Artifacts are content-addressed and immutable. Missing required artifacts fail activation; a missing artifact at serve time returns an explicit service error.

## 8. Secrets, Credentials, and Callbacks

Core primitives:

- `SecretReference`
- `CredentialRecord`
- `CallbackRoute`

Secret values are never stored in Neo4j. Preferred backend is macOS Keychain. Development fallback is an encrypted local secrets file. The instance has one secret vault. `ActionRevision.secret_refs` is an injection list, not a security fence between Applications: listed ids are loaded into the child; an unknown id fails at create or run; an action may list another Application's existing reference. There is no `MAY_READ_SECRET` grant. Unavailable optional values are omitted from the subprocess payload, and `ctx.secrets.get(uri)` fails if app code actually requires one. Inspect and the Experience bridge never return backend values. Values an action writes onto graph nodes are ordinary data.

The Experience bridge may set, replace, inspect configuration status, or delete
the value of an existing Application-owned `SecretReference` only when the
active Experience revision explicitly lists that reference in
`ApplicationAccessDeclaration.secret_reference_ids`. Missing and empty secret
allowlists deny access. The bridge never creates secret references, returns a
value, or creates an Invocation for secret management. `ctx.secrets` remains
read-only.

Provider-specific auth logic belongs to graph-app code.

Callbacks use the independently versioned `n4x.callback.v1` contract over `GET|POST /callback/{route_id}` and the generic MCP callback tools.

`AuthSession` is reserved for a later authentication lifecycle design and is not a V1 acceptance primitive until its ownership edges, refresh semantics, secret boundary, and APIs are specified.

## 9. Triggers and Jobs

V1 supports:

- manual/external triggers
- event triggers
- cron triggers

APScheduler in-process is acceptable for v1. Job records must persist retries, idempotency keys, missed jobs, terminal failures, and action heartbeats.

Neo4j owns durable job truth. APScheduler only identifies due work. A durable job state machine records scheduled, leased, running, retry-wait, succeeded, failed, and missed states, including lease owner/expiry, `JobAttempt` history, heartbeats, and restart recovery. Claiming a lease, running the action, and recording the attempt outcome are separate units of work so the action subprocess is never inside the lease transaction. Distributed execution remains out of scope.

## 10. Activation Lifecycle

Application activation is a single pass. `validate_application_revision` and `run_application_tests` are separate MCP tools; activate does not call them. A killed activate is retried from the start. A failed activate never switches `ACTIVE_REVISION`.

```text
draft revision
  -> intern source tree
  -> declared Python dependency resolution + lock
  -> declared artifact builds
  -> application-data checkpoint if migrations mutate data
  -> migrations run once if present
  -> atomic activation edge replacement
  -> runtime remount
```

`package_install` skips migrations and remount. PackageService remounts after the working set activates.

Activation changes active edges transactionally:

- remove old `ACTIVE_REVISION`
- create new `ACTIVE_REVISION`
- update stable identity node metadata

Checkpoint safety levels are explicit:

- **revision checkpoint** captures active revision pointers and definition state
- **application-data checkpoint** additionally captures app-owned objects and physical app relations
- migrations that mutate app data take an application-data checkpoint before the first migration process. Failure restores that checkpoint. Migrations are not rehearsed.

Application-data checkpoints do not capture Application volume bytes. APIs and documentation must not claim that revision rollback restores migrated Application data or external byte payloads.

### 10.1 Experience activation

Experience activation is independent of Application activation and is also a single pass. `validate_experience_revision` is a separate MCP tool.

```text
draft ExperienceRevision
  -> intern Experience source tree
  -> declared Surface builds
  -> atomic Experience ACTIVE_REVISION replacement
  -> HTTP/MCP catalog remount + list-changed notifications
```

Failure leaves the prior Experience revision active. Validate (the tool) checks that every allowlisted definition belongs to a declared Application and exists, but does not pin, activate, or mutate the Application's active revision. Runtime requests resolve active Application definitions and re-check the allowlist.

## 11. Validation, Repair, and Reset

`bootstrap_schema` applies store constraints and stamps a fresh `N4XRoot`. Worker start calls it and ignores any further integrity report. It does not run `validate_graph_shape`.

`validate_graph_shape` reports:

- unreachable durable nodes
- missing ownership edges
- missing revision edges
- stale or duplicate active edges
- invalid app relation endpoints
- old `N4X_KERNEL` edges
- old generic `N4X_RELATION` edges

`repair_graph_edges` is admin-only. It may recreate missing explicit edges from canonical records, but it is not part of normal runtime operation.

Repair reads canonical durable records from the active `GraphStore`. It must not derive repair truth solely from process-local maps. Validation and repair share a declared ownership/revision schema so every durable node type has an explicit expected path.

Bootstrap stamps `n4x.graph.metamodel.v6` only when the store is fresh: no
`N4XRoot`, or only an unstamped root and no other nodes. A graph stamped with
another version, or a non-empty unversioned graph, fails startup with a
reset-required error. The operator must call the guarded `reset_dev_graph` MCP
tool with `confirmation_database` set to the dedicated development database.
Reset deletes every node, bootstraps schema, creates `N4XRoot`, and stamps v6.
There is no predecessor graph migration.

## 12. MCP Tool Surface

MCP tools expose generic System functionality:

- app/revision lifecycle
- experience/revision lifecycle, source, allowlists, Surfaces, build, and activation
- source tree/file operations
- object type and relation type authoring
- action/trigger/test authoring for Applications
- browser and MCP App Surface authoring for Experiences
- dependency declaration
- draft and active action execution
- activation/rollback/checkpoints
- object and relation inspection
- secret/callback/credential metadata
- scheduler inspection
- graph validation/repair and schema migration
- privileged Cypher with audit/checkpoint
- authoring-guide and blueprint lifecycle where those facilities are enabled

MCP tools must not contain app/provider-specific code.

The repo must not rely on app-specific seeders as the proof-app creation path. A transient developer harness may exercise MCP calls during testing, but the durable product mechanism is the generic MCP tool surface plus graph-owned source and definitions.

## 13. Host / System / App Boundary

The Host/System/app boundary remains strong even when implementation is split into focused services. Graph apps communicate only through versioned MCP, action-context, Experience bridge, callback, and graph-app authoring contracts.

Host owns:

- install file lock (one Host process)
- official System zip import into a `SystemRevision` (does not enable)
- materialize of the enabled System SourceFiles and one worker process group (start, health, stop)
- HTTP reverse proxy to the worker; localhost `n4x-host` import/enable/dump routes
- installed adapter: Bolt driver, unit of work, SourceStore writer, secret backend, kernel models

System owns:

- policy source that Host materialized (MCP catalog, Experience HTTP, activation, packages, jobs)
- Application and Experience revision lifecycle
- action, Experience Surface, trigger, and test execution infrastructure
- secret, callback, checkpoint, and artifact infrastructure as used by apps
- graph integrity validation and repair tools
- Cypher-only app `ActionContext`

Applications own:

- object types
- relation types
- action definitions and backend source
- test definitions and source
- migration definitions and source using `ActionRevision.kind = "migration"`
- provider integrations
- app-specific Cypher and domain semantics

Experiences own:

- frontend source and JavaScript dependencies
- `ui_profile`
- declared Application consumption and object/relation/action allowlists
- immutable browser and MCP App Surface declarations
- frontend build, test, and activation lifecycle

If an app hits a limitation, fix Host or System generically. Never add app-specific/provider-specific Host or System code.

Applications and Experiences must not import System services, repositories, Neo4j adapters, scheduler implementations, or Host internals. Host and System must not inspect an application or Experience id to select product- or provider-specific behavior.

## 14. Proof Applications and Office Experience

Notes, Mail, and Calendar remain separate backend Applications. One shared
`office` Experience declares all three Applications. Its release contains
exactly one browser Surface (`browser`) with Overview, Notes, Mail, and Calendar
routes and four purpose-built MCP App Surfaces (`summary`, `note`, `message`,
and `events`). All five Surfaces share the Experience revision's access policy
and activation lifecycle, while browser and MCP App builds remain independent.
No cross-Application relation is created to support the overview.

Office is app-owned release content, not Host or System bootstrap data. It is authored
through generic MCP and the graph holds its canonical declarations and source.
The runtime never bundles or auto-installs Office. Distribution uses the generic
Package tools in both directions: export a Package v2 archive from a runtime
where Office is active with `export_package` at `root_kind="experience"` and
`root_id="office"`, then install it explicitly: write the archive into the
target runtime's `packages` directory with `n4x package stage` (or copy the file
there), inspect and import it through the generic Package tools, configure any
reported bindings, and explicitly resume imported Application triggers when
appropriate.

### Notes

Object types:

- `notes.Notebook`
- `notes.Note`
- `notes.Tag`

Relation types:

- `notes.note_notebook`
- `notes.note_tag`

Actions:

- create note
- list notes
- update note
- archive note

Office browser page and optional MCP Apps:

- mature local notes UI rendered by the Office Experience

### Mail

Object types:

- `mail.MailAccount`
- `mail.Mailbox`
- `mail.Message`
- `mail.SendDraft`

Relation types:

- `mail.account_mailbox`
- `mail.account_message`
- `mail.mailbox_message`
- `mail.account_send_draft`

Actions:

- configure account
- sync mailbox
- send message

Provider implementation:

- app-owned Python actions using declared dependencies and N4X secrets

Office browser page and optional MCP Apps:

- inbox, message detail, and compose UI rendered by the Office Experience

### Calendar

Object types:

- `calendar.CalendarAccount`
- `calendar.Calendar`
- `calendar.CalendarEvent`

Relation types:

- `calendar.account_calendar`
- `calendar.calendar_event`

Actions:

- configure account
- sync events
- create/update/delete event where feasible

Provider implementation:

- app-owned Python actions using declared dependencies and N4X secrets

Office browser page and optional MCP Apps:

- calendar/list/detail UI rendered by the Office Experience

## 15. Acceptance Tests

Required architecture tests:

- no `N4X_KERNEL` edges are created in normal operation
- no generic `N4X_RELATION` edges are created for new app data
- every normal System or in-process runtime method creates required native edges immediately
- every durable node created through normal APIs is reachable from `N4XRoot` or its owning `Application`
- each active stable identity has at most one `ACTIVE_REVISION` edge
- source/action/Experience/Surface/dependency/object/relation edges exist immediately after creation
- relation creation validates app ownership and endpoint type compatibility
- every Surface belongs directly to exactly one Experience revision, has no revision/active edge of its own, and no Application defines a Surface
- browser Surface mount paths are deterministic and at most one default `/` exists per Experience revision
- Surface artifacts are keyed by Experience revision and Surface id
- Experience allowlists target definitions from declared Applications only
- bridge requests reject undeclared or non-allowlisted capabilities
- no cross-Application relation can be authored or created
- `repair_graph_edges` repairs deliberately damaged graphs but is not needed by normal flows
- a child Cypher request can complete while the parent is blocked in the action subprocess
- `uv` / `pnpm` waits do not run inside an active `GraphUnitOfWork`

Required end-to-end tests:

- fresh graph bootstrap stamps `n4x.graph.metamodel.v6`, while an older or
  non-empty unversioned graph fails with reset-required guidance
- Notes Application authored through MCP and usable through the Office browser Experience and an MCP App Surface
- Mail app authored through MCP, syncs real IMAP mail, sends a test mail
- Calendar app authored through MCP, syncs real CalDAV events
- one Office Experience serves one browser Surface with normal Overview, Notes,
  Mail, and Calendar routes
- four Office `mcp_app` Surfaces (`summary`, `note`, `message`, `events`) are
  independently built, advertised, and bound to allowlisted capabilities
- browser and MCP App Surfaces are separate builds and neither is derived at serve time from the other
- deep browser routes receive the canonical Surface-root document base and
  resolve relative artifacts from that base
- an app-owned Office Package v2 export and explicit install round-trip
  activates one browser plus four MCP App Surfaces
- separate action calls demonstrate documented non-atomic partial-success behavior
- migration actions can backfill objects and rewire relations
- full tests pass against in-memory and local Neo4j stores

Proof-app status is reported at distinct levels:

1. present in a development graph
2. reconstructable from an empty graph through generic MCP/API authoring
3. usable through its browser Experience and declared MCP App/bridge surfaces
4. provider operations verified in a configured acceptance environment
5. passing the same acceptance flow against in-memory and local Neo4j stores

Presence at one level must not be reported as satisfying a later level.

Default development and CI runs may skip live provider operations when secrets are unavailable. Fixture or memory-mode provider simulations do not satisfy level 4. Skipped Neo4j tests do not satisfy level 5.

## 17. Isolated Development Deployments

A `DevelopmentDeployment` is an expiring logical preview inside the same N4X
process, Neo4j database, runtime root, secret backend, and HTTP/MCP server as
production. N4X does not provision a second stack. The deployment pins the
candidate content hashes of one draft Experience revision, every declared
draft or active Application revision, and one DataSpace binding per
Application without changing any production `ACTIVE_REVISION`. Any later
source mutation invalidates that deployment and requires redeployment.

An `ExecutionContext` accompanies every deployment-qualified read, Action,
Cypher request, file token, Invocation, and diagnostic:

```yaml
mode: production | development
deployment_id: string | null
correlation_id: string
experience_revision_id: string | null
application_revision_id: string
application_id: string
data_space_id: string
```

Production constructs the same context with the active revisions and production
DataSpace. Context selection is server-owned; a client cannot gain authority by
supplying revision or DataSpace ids independently.

Creating a deployment freezes candidate hashes; Surface artifacts are built
explicitly and must match those candidates. Later source edits require a new
deployment generation. Preview
browser, bridge, MCP resource, file, Invocation, and diagnostic routes are
deployment-qualified and resolve only the pinned revisions and bound
DataSpaces. Production routes and active-resource catalogs retain their
existing meaning.

Development triggers and callbacks are disabled. Secret access is denied by
default and may be enabled only through an explicit deployment binding to
existing references; secret values are never cloned. Expiry revokes ingress,
cancels supervised work, invalidates file tokens, and removes isolated graph and
volume state after use. Managed ingress may serve `/development/` and
`/api/development/` as deployment-capability URLs; production `/experience/`
remains instance OIDC. Expiry still revokes Kernel ingress via
`require_available`.

A development DataSpace is either empty or initialized by a bounded clone from
the Application's production DataSpace. The trusted Application may supply
read-only declared Cypher returning canonical `object_id` values. The System
injects source scope, rejects write clauses, resolves canonical records itself,
validates ownership, includes relations whose endpoints are both selected, and
commits the clone atomically. Defaults are at most 500 objects and 2,000
relations; host maxima and requested lower limits are configurable. Exceeding a
cap writes nothing. Volumes start empty in this version.

Clone selection, fixtures, provider test accounts, mailbox/date semantics, and
seed policy remain Application-owned. System knows only ids, ownership,
closure, query safety, transaction commit, and resource caps.

**Why can this not be implemented by a trusted Application?** An Application
cannot make HTTP/MCP hosting resolve an inactive Experience revision, override
active definition selection transactionally, issue host-bound file authority,
or create colliding logical ids in an isolated graph scope. System provides
only these hosting and isolation mechanisms; Applications retain all data and
workflow policy.

Activation readiness revalidates the exact tested source, dependency, Surface,
allowlist, and definition hashes and returns the pinned revision ids. It does
not activate, promote data, or introduce another release orchestrator. Existing
Application and Experience activation operations remain the only production
transition.

## 18. Package Distribution

`Package` is a distribution noun beside Application and Experience.
It is a portable archive, not a runtime capability, Surface boundary,
graph `Package` node, blueprint, checkpoint, or marketplace
record.

`Package` is distinct from `RuntimeDependency.package`, which names an npm or PyPI dependency.

### 18.1 Roots and closure

`n4x.package.v2` exports active working sets only:

- an Experience root includes its active revision and every Application declared
  by that revision's `application_access`, each at its active revision;
- an Application root includes that Application and its active revision only;
- draft, validating, rejected, superseded, and parent revisions are not exportable.

The closure contains selected identities, source, dependencies, schema, Actions,
tests, Trigger definitions, Experience Surfaces, access declarations, and referenced secret
requirements. `include_data` additionally carries current Application objects
and relations, and any object or relation type revisions those records
reference on the same Application, even when those type revisions belong to a
prior ApplicationRevision. Parent ApplicationRevisions themselves remain
unexported. History, source changes, invocations, jobs, checkpoints, audits,
build/runtime artifacts, platform themes/guides, blueprints, aliases,
credentials, callback routes, deleted-object provenance, and secret values are
excluded. Callback references are cleared on import and reported as omitted
bindings. Secret references are recreated against the destination secret
backend; their values must be configured separately.

### 18.2 Archive and compatibility

The archive contains a strict typed manifest, canonical member payloads, ordered
entry hashes/sizes, and a package content hash. Readers never extract archive
paths and reject unsafe/duplicate paths, unknown entries, invalid source paths,
hash/size mismatches, and configured size/count limits. The archive detects
corruption, not publisher authenticity.

Import normally requires the supported Package format, graph metamodel and
schema fingerprint, action/subprocess contracts, and Experience Surface/bridge
major version. A predecessor graph metamodel is incompatible. There is no
definition-only exception for older stamps.
A readable Package v2 archive whose contracts otherwise disagree may be
imported with explicit `allow_incompatible` consent: definitions land,
Applications and Experiences stay `disabled`, data is not restored, and
activation does not run. That is not a compatibility reader; a client may
then write and activate a new revision for the current System.
The importer materializes its logical active Application/Experience working set
through ordinary v6 services and recomputes destination identities, hashes, and
physical relation types. This is Package-based working-set transfer, not a
graph migration or general cross-metamodel compatibility reader.
Dependency specifications are portable through normal activation; exact
cross-machine Python/npm lock reproduction is not guaranteed by Package v2.
`ui_profile=n4x-default` intentionally uses the destination System's active
theme.

`n4x.package.v1` is the obsolete Widget-based format and the current runtime
rejects it with a precise compatibility error. It never reinterprets Widget
members as Surfaces.

An authenticated client writes archive bytes into the destination `packages`
directory over HTTP (`PUT /packages/{archive_name}`) using the same instance
identity as `/mcp` (OIDC cookie or Bearer). That is archive intake, not
import. MCP does not carry archive bytes. Install remains inspect then import.

### 18.3 Import behavior

Import rejects every unrelated collision on preserved Application, Experience,
definition, Experience, object, relation, or secret-URI identity. Public stable ids
are never remapped or overwritten. Destination-local revision, source-tree,
dependency, secret-reference, and test ids are regenerated, every internal
reference is rewritten, relation physical types and content hashes are
recomputed, and imported data is rebound to destination schema revisions.

One durable `PackageImportAttempt`, owned by `N4XRoot`, records the package hash,
phase, regenerated-id map, created ids, completed members, counts, and error.
The same package hash may resume or retry that attempt after interruption or
failure; another package may not adopt its identities.

Applications materialize in `importing`, optional data restores before
activation, and `package_install` activation validates/builds the already-active
source working set without rerunning its tests or migration Actions. Completed
Applications transition to `triggers_paused`; ordinary Actions and Experience
bridge access work, but schedule, event, external, missed, and retry Trigger
paths remain blocked. The Experience then builds and activates. Trigger resume
is an explicit operator action after secret/callback configuration.

No `GraphUnitOfWork` remains open across archive IO, subprocess, `uv`, or `pnpm`
work. Dry-run checks are advisory; create-only writes recheck collisions inside
the materialization transaction.

Package v2 never exports or imports Application volume contents. `include_data`
captures graph objects and physical app relations only. Applications that store
relative file references must tolerate absent bytes after Package installation
or implement their own provider re-materialization policy. Volume distribution
requires a separately versioned contract and is not implied by Package data.

## 19. Out of Scope for V1

- format-changing System activate (in-place graph-format `migrate()`, dump/restore undo)
- hostile-code sandboxing
- strict capability enforcement
- multi-user auth
- cloud sync
- package marketplace
- composed objects or composed Actions
- cross-Application relations
- module federation
- hostile-browser sandboxing
- automatic graph-metamodel migration or graph compatibility readers for
  predecessor graphs; Package import requires the current metamodel stamp
  unless the caller passes explicit `allow_incompatible`
- complete provenance for arbitrary direct driver writes
- durable distributed job execution
- compatibility migration from pre-stability disposable prototype graph formats
- `AuthSession` lifecycle
- Application volume export/import, graph-checkpoint capture, coordinated backup, quota management, and deletion lifecycle
- active-active N4X replicas, replica-safe scheduling, and per-user/per-group Application data scopes
- holding a store transaction across subprocess, IPC, or package-manager waits in order to make a use case look atomic
- Package overwrite, public-id remapping, `reuse_if_identical`, signatures, full revision-history backup, secret-value transport, general cross-metamodel import beyond §18.2, and exact cross-machine dependency-lock reproduction

These may be added later without weakening the graph-native Host/System model.
