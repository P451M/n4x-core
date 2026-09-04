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

PREV=$(git tag --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true)
CANDIDATE=${IMAGE##*:}
if [ -n "$PREV" ] && [ "$PREV" != "$CANDIDATE" ]; then
  docker pull "${REPO}:${PREV}"
  PREVIOUS_IMAGE="${REPO}:${PREV}"
  export PREVIOUS_IMAGE
fi

IMAGE="$IMAGE" sh scripts/release_smoke.sh
echo "ci ok ${IMAGE}"
