#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-admin1@192.168.110.222}"
APP_DIR="${APP_DIR:-/opt/gitops-workbench}"
DATA_DIR="${DATA_DIR:-/data/gitops-workbench}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${PUBLISHER_SOURCE:-}" ]]; then
  for candidate in \
    "$ROOT_DIR/../simos/ci/resident/server/simos-ci-publish-resident" \
    "$ROOT_DIR/../ubuntu2004/project/simos/ci/resident/server/simos-ci-publish-resident" \
    "/home/simpleai/ubuntu2004/project/simos/ci/resident/server/simos-ci-publish-resident" \
    "/mnt/c/Users/18262/CodeManage/simos/ci/resident/server/simos-ci-publish-resident"; do
    if [[ -x "$candidate" ]]; then PUBLISHER_SOURCE="$candidate"; break; fi
  done
fi
PUBLISHER_DEST="${PUBLISHER_DEST:-/usr/local/bin/simos-ci-publish-resident}"

die() { echo "deploy-to-server: ERROR: $*" >&2; exit 1; }
[[ -n "${PUBLISHER_SOURCE:-}" && -x "$PUBLISHER_SOURCE" ]] || die "publisher source not found; set PUBLISHER_SOURCE=/path/to/simos/ci/resident/server/simos-ci-publish-resident"
echo "deploy-to-server: publisher source=$PUBLISHER_SOURCE"
echo "deploy-to-server: target=$SSH_TARGET"

ssh "$SSH_TARGET" "test -d '$APP_DIR' && test -w '$APP_DIR'" || die "cannot write remote APP_DIR=$APP_DIR"

# Feature package CI uses the SimOS publisher installed on the protected cloud
# host. Workbench owns this deployment step; SimOS only supplies the source.
PUBLISHER_TMP="/tmp/simos-ci-publish-resident.$$"
trap 'rm -f "$PUBLISHER_TMP"' EXIT
scp "$PUBLISHER_SOURCE" "$SSH_TARGET:$PUBLISHER_TMP" || die "failed to upload publisher"
# admin1 on the publishing host requires a tty for sudo. Keep installation and
# capability checks in one interactive SSH session so the operator enters the
# admin password once; the final Runner check deliberately remains -n.
ssh -tt "$SSH_TARGET" "sudo -v && sudo install -o root -g root -m 0755 '$PUBLISHER_TMP' '$PUBLISHER_DEST' && rm -f '$PUBLISHER_TMP' && sudo '$PUBLISHER_DEST' --capability config-matrix-runtime-switchable-isolated-layout-v2 >/dev/null && sudo '$PUBLISHER_DEST' --capability feature-package-v1 >/dev/null && sudo -u gitlab-runner -H sudo -n '$PUBLISHER_DEST' --capability feature-package-v1 >/dev/null" || die "publisher installation or Runner sudo check failed"
ssh "$SSH_TARGET" "test -f /etc/gitlab/ssl/chancee-shanghai.cn-crt.pem && test -f /etc/gitlab/ssl/chancee-shanghai.cn-key.pem"
ssh "$SSH_TARGET" "test -f '$APP_DIR/.env'"
ssh "$SSH_TARGET" "docker ps >/dev/null"

rsync -az --delete \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'deploy/certs/' \
  --exclude 'gitlab-local/' \
  "$ROOT_DIR/" "$SSH_TARGET:$APP_DIR/"

ssh "$SSH_TARGET" "if ! docker volume inspect gitops-workbench-data >/dev/null 2>&1; then docker volume create gitops-workbench-data >/dev/null; if test -d '$DATA_DIR/data'; then docker run --rm -v gitops-workbench-data:/target -v '$DATA_DIR/data':/source:ro gitops-workbench:latest sh -c 'cp -a /source/. /target/'; fi; fi"
ssh "$SSH_TARGET" "cd '$APP_DIR' && docker compose up -d --build --remove-orphans"
ssh "$SSH_TARGET" "cd '$APP_DIR' && docker compose ps"
ssh "$SSH_TARGET" "docker exec gitops-workbench test -s /app/data/repositories.json"
ssh "$SSH_TARGET" "curl -kfsS https://127.0.0.1:9910/api/session >/dev/null"
ssh "$SSH_TARGET" "docker exec gitops-workbench python -c 'import server; app, _ = server.build_app(None); assert app.repositories()[\"repositories\"]'"

echo "Deployment finished: https://www.chancee-shanghai.cn:9910"
