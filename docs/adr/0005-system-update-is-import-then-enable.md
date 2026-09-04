# 0005. System update is import then enable

## Decision

An official System artifact is a zip. Host imports it as a new
`SystemRevision` and does not enable it. Enable points the graph edge,
rematerializes current SourceFiles, and restarts the worker.

There is no format-epoch, in-place `migrate()`, or converter. Incompatible
graph shape is out of v1. Dump and restore remain a separate instance-bundle
path, not part of official update.

## Rejected

- `apply_official_system` that auto-enables
- Host apply branches A/B/C (same-epoch activate, dump-convert-rollback,
  higher-epoch refuse)
- Last-known-good fallback after a failed apply
- `format_epoch` on `content_root`, release manifests, or health
- `POST /n4x/system/migrate`
