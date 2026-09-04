# 0001. Graph System is the source of truth

## Decision

The graph owns System source the same way it owns Application and Experience
source. A `SystemRevision` is SourceFiles plus an enabled edge. Host imports
an official zip into that model, materializes the enabled revision's current
files, and execs the worker. After import, the zip and the container image
are not SoT.

The wheel and image install Host plus the adapter only. `import n4x.system`
fails on a Host-only install. Worker `PYTHONPATH` prepends the materialized
tree. Host never imports System Python.

`content_root` names an official import for provenance and `/health`. It is
not a boot seal. Healing SourceFiles and enabling again is the intended
repair path.

## Rejected

- Image or `PYTHONPATH=backend/` as the running System
- `revisions.json`, `:SystemSourceFile`, or a Host-side source mirror
- Exec from a bundled tree in the image (`bundled_system_root`)
- Treating the official zip as what runs after first boot
