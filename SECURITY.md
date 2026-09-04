# Security

Report vulnerabilities through [GitHub Security Advisories](https://github.com/P451M/n4x-core/security/advisories) on this repository. Do not open a public issue for a previously undisclosed vulnerability.

## What N4X is

N4X is a personal runtime. Anyone who can reach MCP can change the instance, including dump, import, enable, and reset. It is not multi-tenant SaaS and not a sandbox for untrusted code.

`/n4x-host/*` inspect, export, dump, import, enable, and restore routes are localhost-only. That is not a security boundary once `/mcp` is reachable: MCP host tools can still invoke those operations.

## Scope

In scope:

- Unexpected remote access when the instance is bound only to loopback (`scripts/local.sh`).
- Secret values appearing in the graph, logs, or package archives.
- Path traversal out of runtime or export roots.

Out of scope:

- An instance published on a public address without an identity proxy the operator added. N4X does not ship that.
- Apps an operator authored onto their own instance.
- Multi-tenant isolation.

## Local vs public

`scripts/local.sh` and `deploy/compose.yaml` publish `127.0.0.1:7744` only. The container listens on `0.0.0.0`. `docker run -p 7744:7744` publishes an unauthenticated personal runtime. Do not do that on a public network.
