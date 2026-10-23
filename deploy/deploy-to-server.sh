#!/usr/bin/env bash
set -euo pipefail
trap 'rc=$?; echo "deploy-to-server: ERROR: line $LINENO failed (exit $rc); deployment is incomplete" >&2; exit "$rc"' ERR

SSH_TARGET="${SSH_TARGET:-admin1@192.168.110.17}"
APP_DIR="${APP_DIR:-/opt/gitops-workbench}"
DATA_DIR="${DATA_DIR:-/data/gitops-workbench}"
INTERACTIVE_SUDO="${DEPLOY_INTERACTIVE_SUDO:-true}"
FORCE_BUILD="${DEPLOY_FORCE_BUILD:-false}"
NEXTCLOUD_CONTAINER="${DEPLOY_NEXTCLOUD_CONTAINER:-envs-app-1}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() { echo "deploy-to-server: ERROR: $*" >&2; exit 1; }
run_remote_root() {
  local command="$1"
  if ssh -o BatchMode=yes "$SSH_TARGET" "sudo -n true" 2>/dev/null; then
    ssh "$SSH_TARGET" "sudo -n $command"
  elif [[ "$INTERACTIVE_SUDO" == "true" && -t 0 ]]; then
    echo "deploy-to-server: host dependencies changed; sudo authentication is required."
    ssh -t "$SSH_TARGET" "sudo $command"
  else
    die "host dependencies need installation/update. Run this deploy script from a terminal for the sudo prompt, or on the server: sudo bash '$APP_DIR/deploy/setup-host.sh' --apply"
  fi
}
echo "deploy-to-server: target=$SSH_TARGET"
[[ "$NEXTCLOUD_CONTAINER" =~ ^[a-zA-Z0-9_.-]+$ ]] || die "invalid DEPLOY_NEXTCLOUD_CONTAINER"
[[ "$FORCE_BUILD" == "true" || "$FORCE_BUILD" == "false" ]] || die "DEPLOY_FORCE_BUILD must be true or false"

# Paths are used in remote shell commands and rsync's destination.
for remote_dir in "$APP_DIR" "$DATA_DIR"; do
  [[ "$remote_dir" =~ ^/[a-zA-Z0-9_./-]+$ && "$remote_dir" != / ]] || die "invalid remote directory: $remote_dir"
done
ssh -o BatchMode=yes "$SSH_TARGET" true || die "SSH key authentication failed for $SSH_TARGET; configure this WSL user's key before deploying"

if ! ssh "$SSH_TARGET" "test -d '$APP_DIR' && test -w '$APP_DIR'"; then
  echo "deploy-to-server: $APP_DIR missing or not writable; provisioning remote directories..."
  if ssh -o BatchMode=yes "$SSH_TARGET" "sudo -n mkdir -p '$APP_DIR' '$DATA_DIR/data' '$DATA_DIR/certs' '$DATA_DIR/logs' && sudo -n chown -R \$(id -un):\$(id -gn) '$APP_DIR' '$DATA_DIR'" 2>/dev/null; then
    echo "deploy-to-server: provisioned $APP_DIR and $DATA_DIR"
  elif [[ "$INTERACTIVE_SUDO" == "true" && -t 0 ]]; then
    echo "deploy-to-server: non-interactive sudo is unavailable; running the one-time bootstrap with sudo."
    # Keep stdin connected to the terminal for sudo's password prompt.
    bootstrap_encoded="$(base64 -w 0 "$ROOT_DIR/deploy/bootstrap-sudo.sh")"
    ssh -t "$SSH_TARGET" "sudo env APP_DIR='$APP_DIR' DATA_DIR='$DATA_DIR' bash -c \"\$(printf '%s' '$bootstrap_encoded' | base64 -d)\""
  else
    die "cannot write remote APP_DIR=$APP_DIR. Run once on the server: sudo bash deploy/bootstrap-sudo.sh (or set DEPLOY_INTERACTIVE_SUDO=true from a terminal)."
  fi
fi

ssh "$SSH_TARGET" "test -d '$APP_DIR' && test -w '$APP_DIR'" || die "bootstrap did not make APP_DIR writable"

ssh "$SSH_TARGET" "test -f /etc/gitlab/ssl/chancee-shanghai.cn-crt.pem && test -f /etc/gitlab/ssl/chancee-shanghai.cn-key.pem" || die "TLS certificate files are missing on $SSH_TARGET"
ssh "$SSH_TARGET" "test -f '$APP_DIR/.env'" || die "remote $APP_DIR/.env is missing; securely copy your production configuration there and chmod 600 before deploying (template: deploy/gitops-workbench.env.example)"
ssh "$SSH_TARGET" "docker ps >/dev/null" || die "remote user cannot access Docker"
ssh "$SSH_TARGET" "docker compose version >/dev/null && test \"\$(docker inspect --format '{{.State.Running}}' '$NEXTCLOUD_CONTAINER')\" = true" || die "Docker Compose or running Nextcloud container $NEXTCLOUD_CONTAINER is unavailable"

echo "deploy-to-server: syncing application files..."
rsync -az --delete \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude 'deploy/certs/' \
  --exclude 'gitlab-local/' \
  --exclude '.deploy-state/' \
  --exclude '.worktrees/' \
  --exclude '.superpowers/' \
  --exclude '.codex/' \
  --exclude '.agents/' \
  --exclude '.aws/' \
  --exclude '__pycache__/' \
  "$ROOT_DIR/" "$SSH_TARGET:$APP_DIR/"

echo "deploy-to-server: checking host dependencies..."
host_status=0
ssh "$SSH_TARGET" "bash '$APP_DIR/deploy/setup-host.sh' --check" || host_status=$?
case "$host_status" in
  0) ;;
  10)
    run_remote_root "bash '$APP_DIR/deploy/setup-host.sh' --apply"
    ssh "$SSH_TARGET" "bash '$APP_DIR/deploy/setup-host.sh' --check" || die "host dependency verification failed"
    ;;
  *) die "host dependency check failed (exit $host_status)" ;;
esac

echo "deploy-to-server: checking application image inputs..."
ssh "$SSH_TARGET" "DEPLOY_FORCE_BUILD='$FORCE_BUILD' bash '$APP_DIR/deploy/build-app.sh'"
# Build before referencing the image on a fresh server. Docker initializes a
# new volume from /app/data in the image; import legacy bind data only if present.
ssh "$SSH_TARGET" "if ! docker volume inspect gitops-workbench-data >/dev/null 2>&1; then docker volume create gitops-workbench-data >/dev/null; docker run --rm -v gitops-workbench-data:/app/data -v '$DATA_DIR/data':/source:ro gitops-workbench:latest sh -c 'if test -s /source/repositories.json && ! test -f /app/data/.legacy-imported; then cp -a /source/. /app/data/; touch /app/data/.legacy-imported; fi'; fi"
echo "deploy-to-server: starting service..."
ssh "$SSH_TARGET" "cd '$APP_DIR' && docker compose up -d --wait --wait-timeout 120 --remove-orphans"
ssh "$SSH_TARGET" "cd '$APP_DIR' && docker compose ps"
ssh "$SSH_TARGET" "docker exec gitops-workbench test -s /app/data/repositories.json"
ssh "$SSH_TARGET" "curl -kfsS https://127.0.0.1:9910/api/session >/dev/null"
ssh "$SSH_TARGET" "docker exec gitops-workbench python -c 'import server; app, _ = server.build_app(None); assert app.repositories()[\"repositories\"]'"

echo "Deployment finished: https://www.chancee-shanghai.cn:9910"
