#!/bin/sh
# Offline instance dump on the compose host. Neo4j Community dump needs a stopped DB.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
OUT="${1:-$ROOT/../instance.n4xi}"
cd "$ROOT"
COMPOSE="docker compose --env-file $ROOT/.env"

$COMPOSE stop n4x
$COMPOSE stop neo4j
$COMPOSE run --rm --no-deps --entrypoint neo4j-admin neo4j \
  database dump neo4j --to-path=/dumps --overwrite-destination=true
$COMPOSE start neo4j
$COMPOSE start n4x
$COMPOSE up -d --wait n4x
$COMPOSE exec -T n4x mkdir -p /var/lib/n4x/exports
$COMPOSE exec -T n4x \
  n4x host dump --output /var/lib/n4x/exports/instance.n4xi --neo4j-dump /dumps/neo4j.dump
$COMPOSE cp n4x:/var/lib/n4x/exports/instance.n4xi "$OUT"
echo "wrote $OUT"
