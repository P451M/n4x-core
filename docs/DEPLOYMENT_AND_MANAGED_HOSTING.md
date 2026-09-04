# Deployment and instance operations

Present-state operator notes for one N4X instance. Architecture is
[SPEC.md](../SPEC.md). Why these boundaries exist is in [docs/adr/](adr/).
This file does not authorize in-process multi-tenancy or `AuthSession`.

Managed hosting is a separate product. This repository is the engine.

## What an instance is

One Host, one System worker, one Neo4j database, one secret backend, and
one persistent `N4X_RUNTIME_ROOT`. Many people means many instances.

Supported start of this image:

- **Local:** `scripts/local.sh` on loopback.

DIY public ingress is not a shipped product. The operator who publishes a
public origin adds identity themselves.

## System update

Official System is a zip. Host **imports** it as a `SystemRevision` and
does not enable it. **Enable** points the graph edge, rematerializes
current SourceFiles, and restarts the worker.

```text
n4x host import --archive official-system.zip
n4x host enable <revision_id>
```

MCP: `import_official_system`, then `enable_system_revision`. Empty first
boot imports and enables. A later import does not enable.

`/n4x-host/*` mutating and inspect/export routes are localhost-only. Anyone
who can reach MCP can still invoke dump, import, enable, and reset. Put
identity in front of `/mcp` on a public origin.

## Dump and restore

`n4x host dump` writes an instance bundle: Neo4j dump, runtime tree,
secret ciphertext. The master key is not in the archive.

`n4x host restore` applies a bundle on this box.

If `n4x serve` is already holding the install lock, the CLI talks to the
running Host HTTP first. Dump and restore stay on the box.

## Health

`/health` reports Host adapter and enabled System `content_root` as
separate fields. `content_root` is official-import provenance, not the
container image pin and not a boot seal.
