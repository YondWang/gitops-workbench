#!/usr/bin/env bash
# Run on the server after rsync. Keep the successful input/image pair across deploys.
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
STATE_DIR="$ROOT_DIR/.deploy-state"
STATE_FILE="$STATE_DIR/build-input.sha256"
FORCE="${DEPLOY_FORCE_BUILD:-false}"
[[ "$FORCE" == true || "$FORCE" == false ]] || { echo "invalid DEPLOY_FORCE_BUILD" >&2; exit 1; }
DIGEST="$(python3 deploy/build-input-digest.py "$ROOT_DIR")"
IMAGE_ID="$(docker image inspect --format '{{.Id}}' gitops-workbench:latest 2>/dev/null || true)"
PREVIOUS="$(cat "$STATE_FILE" 2>/dev/null || true)"
if [[ "$FORCE" != true && -n "$IMAGE_ID" && "$PREVIOUS" == "$DIGEST $IMAGE_ID" ]]; then
  echo "application image: inputs and image unchanged; skip build"
  exit 0
fi
echo "application image: inputs/image changed or force requested; build using Docker cache"
docker compose build
IMAGE_ID="$(docker image inspect --format '{{.Id}}' gitops-workbench:latest)"
[[ -n "$IMAGE_ID" ]] || { echo "application image missing after build" >&2; exit 1; }
mkdir -p "$STATE_DIR"
STATE_TEMP="$(mktemp "$STATE_DIR/.build.XXXXXX")"
trap 'rm -f "$STATE_TEMP"' EXIT
printf '%s %s\n' "$DIGEST" "$IMAGE_ID" > "$STATE_TEMP"
mv -f "$STATE_TEMP" "$STATE_FILE"
