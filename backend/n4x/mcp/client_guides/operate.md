# Operate an N4X instance

Use this topic for development isolation, jobs, callbacks, secrets, and
packages. It does not replace Application or Experience authoring playbooks.

## Development DataSpaces

`create_development_data_space` makes an empty isolated DataSpace.
`create_development_deployment` mounts draft Experience and Application
revisions into those spaces and returns instance-prefixed `preview_url`
values. Open `{origin}/development/{id}/experience/{experience_id}` for live
draft review after Surfaces are built. On a public origin that path is a
deployment capability; do not complete OIDC to open it. Production
`/experience/{id}` still requires instance sign-in. Do not activate to preview.

`initialization: "clone"` copies production graph objects and binds the
Experience's declared secret references (same vault; values are not copied).
`empty` is layout-only. Clone preview can invoke provider actions for the
TTL; PUT/DELETE of secret values stay on production `/api/experiences/…`.

Draft review is the clone `preview_url`. If the Cursor browser is on the
production login page, ask the operator to finish sign-in in that tab, then
continue — that is live proof, not draft preview. A new draft must be built
before open (`surface_artifact_missing` is an unbuilt surface, not an app
bug). An active-only Experience cannot deploy.

Inspect with `inspect_development_deployment`, `list_development_objects`,
and `list_development_relations`. Authoring list tools are bounded (summary
by default, `limit` 50, hard cap 200). Experience-bridge `list_objects` is
not. Run candidates with `run_development_action` /
`submit_development_action`. Expire with `expire_development_deployment`
when finished.

## Jobs and callbacks

`inspect_scheduler` shows active triggers, mounted jobs, and JobRecords.
`run_trigger` and `dispatch_event` enqueue work. `process_due_job_work` claims
due retries. `inspect_job_attempts` and `inspect_invocations` are read-only.

`create_callback_route` and `dispatch_callback_route` target an ActionRevision.
`inspect_callback_routes` never returns secret values.

## Secrets

Graph rows are metadata. `create_secret_reference` then `set_secret_value`
stores the value in the configured backend (Keychain or encrypted file).
Declare `secret_refs` on an action to inject those ids; inspect never
returns values. An action may list another Application's existing reference.
`create_credential_record` links provider accounts to those references.
`inspect_secret_references` requires `application_id`.
`inspect_credential_records` never returns values.

## Packages

`preview_package` then `export_package` writes a deterministic archive on
that instance. `list_packages` shows staged archives and `upload_url`.
Do not read or inline an archive. On a remote instance, confirm the local
path, then `n4x package stage --origin <instance> --file <path>` (requires
`n4x login` once). Loopback needs no login. `inspect_package` reports
compatibility before `import_package`. Imports activate the working set and
leave Application triggers paused. Configure secret and callback bindings,
then `resume_application_triggers`. If inspect says the archive does not
match this System, tell the user what disagrees: import can still land the
source disabled (`allow_incompatible`) so they can write a new revision.
Do that only if they ask. `create_application_revision` and
`create_experience_revision` continue the imported draft. Activating the
rewritten Application revision leaves it `triggers_paused`; activating the
Experience revision makes that Experience live.
`include_data` also carries object and relation type revisions referenced by
that data, including prior ApplicationRevision schema of the same app.
`delete_working_set` always exports `include_data=true` first, then deletes
that Package root. Application-root refuses while any Experience revision
lists the app. Experience-root deletes the Experience and every Application
in the export, and refuses if another Experience still lists those apps.
`confirmation_root_id` must equal `root_id`.

## Checkpoints and Host

`create_checkpoint` / `inspect_checkpoints` capture revision or data snapshots.
`restore_checkpoint` and `rollback_application` are destructive; use them only
with explicit user intent.

Host-control tools (`inspect_official_release`, `import_official_system`,
`enable_system_revision`, `dump_instance`, `inspect_instance_dumps`) talk to
the Host, not the graph authoring catalog. Import writes a SystemRevision
and does not enable it. Enable rematerializes current source from the enabled
revision's tree and restarts the worker. `inspect_system` returns the enabled
revision, Applications, and Experiences, each with a derived `source_tree_id`.
Heal System source with `write_source_file` on that `revision_id`, then enable
the same revision. `reset_dev_graph` wipes a dedicated development database
after exact name confirmation.

Initialize `serverInfo.version` is the enabled System provenance
`content_root`, not the container image pin. MCP `initialize` notifies
`tools/list_changed` and `resources/list_changed` so clients re-list after
worker replacement at the same `/mcp` URL. A Cursor window reload also
refreshes the catalog.
