# Contributing

PRs are welcome. By contributing you license your work under the [Apache License 2.0](./LICENSE). There is no separate CLA.

## Setup

You need Python 3.13+, [uv](https://docs.astral.sh/uv/), and (for the local stack) [Docker](https://docs.docker.com/get-docker/).

```bash
uv sync --extra dev
uv run pytest -q
```

Without configured Neo4j, Neo4j-marked tests skip (about 25). That is expected.

```bash
# Optional Neo4j parity
export N4X_NEO4J_URI=bolt://localhost:7687
export N4X_NEO4J_USER=neo4j
export N4X_NEO4J_PASSWORD=<local-password>
export N4X_NEO4J_DATABASE=neo4j
N4X_NEO4J_REQUIRED=1 uv run pytest -q -m neo4j
```

Local instance:

```bash
sh scripts/local.sh
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for authoring and smoke commands.

## Pull requests

- Keep Host/System generic. App and provider behavior belongs on the graph.
- Do not invent MCP tools, Surface kinds, or graph nouns. Kernel kinds are `browser` and `mcp_app` only.
- If a change alters user-visible behavior, update the matching `/docs` pages in the sibling website repo `n4x-www`.
