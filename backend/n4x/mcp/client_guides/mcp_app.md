# MCP App Surfaces

An Experience Surface is `browser` or `mcp_app`. N4X does not derive one host's
UI from the other. Separate entrypoints when both exist.

## Authoring

1. Follow `inspect_client_guide` with `topic=experience` and load
   `inspect_experience_design_context` before Surface source.
2. `create_experience_surface` with `surface_type=mcp_app`, entrypoint, and
   complete `source_paths`.
3. `build_experience_surface`, then `validate_experience_revision`.
4. `activate_experience_revision` notifies this MCP session with
   `notifications/resources/list_changed` and `notifications/tools/list_changed`.
   Refresh the catalog after activation. Do not reuse stale HTML.

## Runtime catalog

Active MCP App Surfaces appear as resources under revision-qualified
`ui://n4x/...` URIs with MIME `text/html;profile=mcp-app`. An open tool is bound
to that resource. Development deployments use a distinct URI prefix.

Surface data and mutation tools are Experience-scoped and server-bound to the
active revision and allowlists. An MCP App cannot obtain authority by supplying
a different Experience id.

## Bridge

Call `inspect_experience_bridge` for `n4x.experience.bridge.v1`. MCP App
Surfaces call app-only tools through `window.parent.postMessage` JSON-RPC
`tools/call`. `window.openai.callTool` is an optional compatibility fallback.
Never use `/api/apps/...` from Surface code; those routes are trusted host APIs.

A missing definition allowlist permits all active definitions in the declared
Application. An empty allowlist permits none. Secret-reference access is
stricter: missing and empty both deny.
