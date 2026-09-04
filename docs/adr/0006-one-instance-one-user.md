# 0006. One instance, one user

## Decision

One N4X instance is one Host, one System worker, one Neo4j database, one
secret backend, and one persistent runtime volume. Many people means many
instances, not tenants in one process.

The product website (accounts, waitlist, provisioner) is a separate
repository. It is not Host or System. There is no HTTP god-token into a
tenant instance. Instance access on a public origin is ingress OIDC, not a
Kernel `AuthSession`. Dump, restore, and System enable stay on the box;
the control plane reaches them over SSH, not a public Host API.

## Rejected

- In-process multi-tenancy
- A fat external admin that writes the tenant graph
- `AuthSession` as V1 instance login
- Public Host activate/dump/restore URLs
