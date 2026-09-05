#!/bin/sh
# Smoke the official image the way a managed instance updates:
# first-activate on an empty graph, then previous image (if set) on a
# new Neo4j, recreate n4x only, import+enable the candidate zip.
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
IMAGE=${IMAGE:?IMAGE is the candidate image tag}
PREVIOUS_IMAGE=${PREVIOUS_IMAGE:-}
PREFIX="n4x-smoke-$$"
COMPOSE_FILE="$ROOT/deploy/compose.smoke.yaml"
WORKDIR=$(mktemp -d)
ENV_FILE="$WORKDIR/.env"
: > "$ENV_FILE"
PASSWORD="smoke-$$"
MASTER="smoke-master-key-$$-0123456789abcdef"
PROJECT=

write_env() {
  image=$1
  cat > "$ENV_FILE" <<EOF
N4X_IMAGE=${image}
N4X_NEO4J_PASSWORD=${PASSWORD}
N4X_SECRETS_MASTER_KEY=${MASTER}
N4X_PUBLIC_ORIGIN=http://127.0.0.1:7744
N4X_HOST_CONTROL_ORIGIN=http://127.0.0.1:7744
N4X_SYSTEM_RELEASE_INDEX=
EOF
  export N4X_IMAGE="$image"
  export N4X_NEO4J_PASSWORD="$PASSWORD"
  export N4X_SECRETS_MASTER_KEY="$MASTER"
  export N4X_PUBLIC_ORIGIN=http://127.0.0.1:7744
  export N4X_HOST_CONTROL_ORIGIN=http://127.0.0.1:7744
}

compose() {
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

down_project() {
  project=$1
  docker compose -p "$project" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down -v >/dev/null 2>&1 || true
}

cleanup() {
  down_project "${PREFIX}-fresh"
  down_project "${PREFIX}-cutover"
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

write_env "$IMAGE"

up() {
  write_env "$1"
  compose up -d --wait --wait-timeout 600
}

health() {
  compose exec -T n4x python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:7744/health', timeout=3).read().decode())"
}

wait_health() {
  deadline=$(( $(date +%s) + 180 ))
  last="not reached"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if last=$(health 2>/dev/null); then
      printf '%s\n' "$last"
      return 0
    fi
    sleep 3
  done
  echo "health timed out ($last)" >&2
  compose logs n4x >&2 || true
  return 1
}

require_worker() {
  body=$1
  printf '%s' "$body" | python3 -c "
import json, sys
body = json.load(sys.stdin)
if body.get('status') != 'ok' or not body.get('worker'):
    raise SystemExit('worker is not running: ' + json.dumps(body))
if not (body.get('system') or {}).get('content_root'):
    raise SystemExit('health has no system content_root: ' + json.dumps(body))
"
}

enable_official() {
  if ! compose exec -T n4x python - < "$ROOT/scripts/enable_official_system.py"; then
    echo "enable official System failed" >&2
    compose logs n4x >&2 || true
    compose exec -T n4x sh -c 'tail -n 200 /var/lib/n4x/runtime/*.log 2>/dev/null || true' >&2 || true
    return 1
  fi
}

echo "fresh boot ${IMAGE}"
PROJECT="${PREFIX}-fresh"
up "$IMAGE"
require_worker "$(wait_health)"
echo "enable official System on fresh graph"
enable_official
require_worker "$(wait_health)"
compose down -v

if [ -n "$PREVIOUS_IMAGE" ]; then
  echo "upgrade ${PREVIOUS_IMAGE} -> ${IMAGE}"
  PROJECT="${PREFIX}-cutover"
  up "$PREVIOUS_IMAGE"
  require_worker "$(wait_health)"
  echo "replace Host with ${IMAGE}"
  write_env "$IMAGE"
  compose up -d --no-deps --force-recreate --wait --wait-timeout 600 n4x
  wait_health >/dev/null
  echo "enable official System from candidate zip"
  enable_official
  require_worker "$(wait_health)"
fi

echo "release smoke ok"
