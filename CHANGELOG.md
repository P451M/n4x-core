# Changelog

## 0.2.4

Host control stays on loopback. Worker tools no longer call the public origin.

## 0.2.3

`apply_source_patch` documents and rejects anything but a unified diff.
A development preview whose pinned revision is no longer draft or active
requires a new deployment.

## 0.2.2

Host waits 120s for a System worker to become healthy and includes the
worker log on a failed enable. CI smokes a previous-image cutover: keep
Neo4j, recreate n4x, then import and enable.

## 0.2.1

Clone development deployments bind the Experience's declared secret
references from the instance vault. Empty deployments stay secret-less.
Secret values are never copied.

## 0.2.0

Revisions are interned commits (`n4x.graph.metamodel.v6`, `n4x.mcp.authoring.v9`).
Source writes take `revision_id`; trees intern on activate. App Cypher stores
`values` as JSON text. Existing v0.1.0 graphs are not migrated in place.

## 0.1.0

Initial public release of the N4X engine under the Apache License 2.0.
