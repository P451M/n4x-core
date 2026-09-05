#!/bin/sh
# One test path: pytest, build this tree's image, then boot/enable/upgrade.
# Does not push GHCR or tag. official-release runs this, then publishes.
set -eu
cd "$(dirname "$0")/.."

IMAGE=${IMAGE:-n4x:ci}
REPO=${N4X_IMAGE_REPO:-ghcr.io/p451m/n4x-core}

if command -v uv >/dev/null 2>&1; then
  uv run --extra dev pytest -q
else
  pytest -q
fi

docker build -f deploy/Dockerfile -t "$IMAGE" .

CANDIDATE_REF=${IMAGE##*:}
PREV=$(
  git tag --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | grep -vx "$CANDIDATE_REF" | head -1 || true
)
unset PREVIOUS_IMAGE
if [ -n "$PREV" ]; then
  ghcr="${REPO}:${PREV}"
  engine_arch=$(docker version -f '{{.Server.Arch}}')
  if docker pull "$ghcr"; then
    image_arch=$(docker image inspect -f '{{.Architecture}}' "$ghcr")
    if [ "$image_arch" = "$engine_arch" ]; then
      PREVIOUS_IMAGE=$ghcr
      echo "previous=ghcr ${PREV}"
    fi
  fi
  if [ -z "${PREVIOUS_IMAGE:-}" ]; then
    if ! git rev-parse --verify "refs/tags/${PREV}" >/dev/null 2>&1; then
      git fetch --tags
    fi
    if ! git rev-parse --verify "refs/tags/${PREV}" >/dev/null 2>&1; then
      echo "previous tag ${PREV} is missing locally" >&2
      exit 1
    fi
    tmp=$(mktemp -d)
    git archive "$PREV" | tar -x -C "$tmp"
    docker build -f "$tmp/deploy/Dockerfile" -t n4x:previous-local "$tmp"
    rm -rf "$tmp"
    PREVIOUS_IMAGE=n4x:previous-local
    echo "previous=local-rebuild ${PREV} (not the shipped digest)"
  fi
  export PREVIOUS_IMAGE
fi

IMAGE="$IMAGE" sh scripts/release_smoke.sh
echo "ci ok ${IMAGE}"
