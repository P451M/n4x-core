# 0002. Host has no last-good and no recover product

## Decision

If the enabled System is broken, the operator (or their MCP client) enables
another revision already in the graph, or heals the SourceFiles and enables
again. Host does not keep a parallel “safe copy.”

The graph enabled edge is SoT for which System runs. Host has a process file
lock (one `n4x serve`). That lock is not a System policy store. If Neo4j is
down, the instance is down; Host does not boot a disk System against a dead
store.

## Rejected

- `n4x recover` / last-known-good pointer
- Host `control.json` as SoT for the active System
- Boot-time content-hash refuse
- Factory worker or a second Host-only System
