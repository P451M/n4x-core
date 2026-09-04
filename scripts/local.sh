#!/bin/sh
# Start the local production instance: Host + Neo4j on http://127.0.0.1:7744
set -e
cd "$(dirname "$0")/.."
env_file=deploy/.env.local
# Ignore a leftover public profile from an older env.
unset COMPOSE_PROFILES

_rand() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import secrets; print(secrets.token_urlsafe($1))"
  else
    openssl rand -base64 48 | tr -d '/+\n=' | head -c "$1"
  fi
}

_port_in_use() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import socket; s=socket.socket(); s.settimeout(0.3); raise SystemExit(0 if s.connect_ex(('127.0.0.1', 7744)) == 0 else 1)"
  elif command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 7744
  else
    return 1
  fi
}

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker, then retry." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but the daemon is not running." >&2
  exit 1
fi

if [ ! -f "$env_file" ]; then
  umask 077
  cat >"$env_file" <<EOF
N4X_NEO4J_PASSWORD=$(_rand 24)
N4X_SECRETS_MASTER_KEY=$(_rand 32)
N4X_FILE_DELIVERY_SIGNING_KEY=$(_rand 32)
N4X_PUBLIC_ORIGIN=http://127.0.0.1:7744
EOF
fi

compose="docker compose -f deploy/compose.yaml --env-file $env_file"
if [ "$#" -eq 0 ]; then
  if _port_in_use; then
    if [ -z "$($compose ps -q n4x 2>/dev/null)" ]; then
      echo "127.0.0.1:7744 is already in use. Stop the other process, or: $compose down" >&2
      exit 1
    fi
  fi
  $compose up -d --build --wait
  echo "N4X is up at http://127.0.0.1:7744"
  echo "MCP:  http://127.0.0.1:7744/mcp"
  echo "Stop: sh scripts/local.sh down"
  exit 0
fi
exec $compose "$@"
