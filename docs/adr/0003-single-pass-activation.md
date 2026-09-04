# 0003. Application and Experience activate are a single pass

## Decision

Application and Experience activation is one ordered pass. A killed activate
is retried from the start. A failed activate never switches `ACTIVE_REVISION`.

Mutating Application migrations take an application-data checkpoint first and
restore it on failure. `validate_*` and `run_application_tests` are separate
MCP tools; activate does not call them.

App and Experience keep the verb `activate` (in-worker `ACTIVE_REVISION`
switch). System revision enable is a Host restart and uses `enable`.

## Rejected

- Durable `ActivationAttempt` / stage rows
- Interrupt-resume of a half-finished activate
- Required quality stages (schema validate, tests, trigger checks) inside
  activate
- Treating System enable as the same machine as app activate
