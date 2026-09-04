#!/bin/sh
# Record RSS of a running compose stack. Run after Office is imported and one Action has run.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
COMPOSE="docker compose -f $ROOT/compose.yaml"
echo "timestamp $(date -u +%Y-%m-%dT%H:%M:%SZ)"
$COMPOSE stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
