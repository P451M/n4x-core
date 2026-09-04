# 0004. MCP inspect is not a Cypher substitute

## Decision

MCP inspect tools stay when they do work Cypher cannot: secret redaction,
authoring bounds, playbook/design context, or computed preview URLs.

Thin graph projections are not tools. The client reads those with Cypher.

## Rejected

- A wrapper tool for every inspectable node type
- Listing blueprints, Experiences, data spaces, builds, Cypher audits,
  deleted-object provenance, or source-change rows through MCP when the
  graph already holds them
