#!/bin/sh
# Smoke the official image the way a managed instance updates:
# previous image (if set) boots a graph, the candidate Host replaces it,
# then import+enable the zip baked into the candidate.
set -eu

IMAGE=${IMAGE:?IMAGE is the candidate image tag}
PREVIOUS_IMAGE=${PREVIOUS_IMAGE:-}
PREFIX="n4x-smoke-$$"
NETWORK="${PREFIX}-net"
NEO4J="${PREFIX}-neo4j"
N4X="${PREFIX}-n4x"
VOLUME_N4X="${PREFIX}-data"
PASSWORD="smoke-$$"
MASTER="smoke-master-key-$$-0123456789abcdef"

cleanup() {
  docker rm -f "$N4X" "$NEO4J" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME_N4X" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

health() {
  docker exec "$N4X" python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:7744/health', timeout=3).read().decode())"
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
  docker logs "$N4X" >&2 || true
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
  docker exec -i "$N4X" python - <<'PY'
import json
import os
import urllib.request

origin = os.environ.get("N4X_HOST_CONTROL_ORIGIN", "http://127.0.0.1:7744").rstrip("/")
archive = "/opt/n4x/cache/official-system.zip"

def post(path, payload, timeout):
    request = urllib.request.Request(
        origin + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode())
    if not isinstance(body, dict) or body.get("error"):
        raise SystemExit(path + " failed: " + json.dumps(body))
    return body

imported = post("/n4x-host/import", {"archive": archive}, 120)
revision_id = imported.get("imported")
if not revision_id:
    raise SystemExit("import did not return a revision id: " + json.dumps(imported))
print(json.dumps(post("/n4x-host/enable", {"revision_id": revision_id}, 180)))
PY
}

start_neo4j() {
  docker network create "$NETWORK" >/dev/null
  docker volume create "$VOLUME_N4X" >/dev/null
  docker run -d --name "$NEO4J" --network "$NETWORK" --network-alias neo4j \
    -e NEO4J_AUTH="neo4j/${PASSWORD}" \
    neo4j:5-community >/dev/null
  deadline=$(( $(date +%s) + 180 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if docker exec "$NEO4J" cypher-shell -u neo4j -p "$PASSWORD" 'RETURN 1' >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  echo "neo4j did not become ready" >&2
  docker logs "$NEO4J" >&2 || true
  return 1
}

start_n4x() {
  image=$1
  docker rm -f "$N4X" >/dev/null 2>&1 || true
  docker run -d --name "$N4X" --network "$NETWORK" \
    -e N4X_RUNTIME_ROOT=/var/lib/n4x \
    -e N4X_HTTP_MODE=production \
    -e N4X_NEO4J_URI=bolt://neo4j:7687 \
    -e N4X_NEO4J_USER=neo4j \
    -e "N4X_NEO4J_PASSWORD=${PASSWORD}" \
    -e N4X_NEO4J_DATABASE=neo4j \
    -e N4X_PUBLIC_ORIGIN=http://127.0.0.1:7744 \
    -e "N4X_SECRETS_MASTER_KEY=${MASTER}" \
    -e N4X_SECRET_BACKEND=encrypted_local \
    -e N4X_HOST_CONTROL_ORIGIN=http://127.0.0.1:7744 \
    -v "${VOLUME_N4X}:/var/lib/n4x" \
    "$image" >/dev/null
}

echo "fresh boot ${IMAGE}"
start_neo4j
start_n4x "$IMAGE"
require_worker "$(wait_health)"
echo "enable official System on fresh graph"
enable_official
require_worker "$(wait_health)"

if [ -n "$PREVIOUS_IMAGE" ]; then
  echo "upgrade ${PREVIOUS_IMAGE} -> ${IMAGE}"
  docker rm -f "$N4X" "$NEO4J" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME_N4X" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  start_neo4j
  start_n4x "$PREVIOUS_IMAGE"
  require_worker "$(wait_health)"
  echo "replace Host with ${IMAGE}"
  start_n4x "$IMAGE"
  # Host must answer even if the previous System worker cannot start.
  wait_health >/dev/null
  echo "enable official System from candidate zip"
  enable_official
  require_worker "$(wait_health)"
fi

echo "release smoke ok"
