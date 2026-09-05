# 0007. Revisions are interned commits

## Decision

A revision is a commit. It points at interned objects (source tree, type and
action revisions, Surface declarations). It is not a mutable workspace and
has no `source_tree_id` field. MCP still returns a derived `source_tree_id`
and `tree_status` so clients can see which tree the commit currently names.

Interned trees are shared: `id` is `tree_hash`, `status=interned`, no owner.
A draft's first write copy-on-writes a working tree `{revision_id}.source`.
Later writes mutate that tree. Activate interns the listing, retargets
`HAS_SOURCE_TREE`, and deletes the working tree. Discard removes the draft
only; interned blobs and trees stay until nothing points at them.

Source MCP tools take `revision_id`. The tree id is an internal pointer, not
the authoring identity. `SourceFile` is a read DTO from `HAS_FILE` plus the
blob. There is no `SourceFile` node.

This is `n4x.graph.metamodel.v6`. Existing v5 graphs are not migrated in
place. Wipe and recreate; that is a development reset, not System enable
([0005](0005-system-update-is-import-then-enable.md)).

Graph System remains SoT ([0001](0001-graph-system-is-sot.md)). Healing still
means write the enabled revision's source and enable again. "SourceFiles" in
0001–0005 means that DTO over the interned tree, not a durable file node.

## Rejected

- `SourceFile` as a graph node, or `SourceChange` rows
- `SNAPSHOT_OF`, `CLONED_FROM`, `USES_SOURCE`, `MATERIALIZED_TO` as lookup
- MCP writes, reads, list, or search keyed by `source_tree_id`
- A unique unshared tree per revision that is never interned
- Local checkout as source authority
- In-place v5 → v6 migrate or a predecessor Package reader
