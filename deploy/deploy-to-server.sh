#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-admin1@192.168.110.222}"
APP_DIR="${APP_DIR:-/opt/gitops-workbench}"
DATA_DIR="${DATA_DIR:-/data/gitops-workbench}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ssh "$SSH_TARGET" "test -d '$APP_DIR' && test -w '$APP_DIR'"
ssh "$SSH_TARGET" "test -d '$DATA_DIR/data' && test -w '$DATA_DIR/data'"
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

ssh "$SSH_TARGET" "cd '$APP_DIR' && docker compose up -d --build --remove-orphans"
ssh "$SSH_TARGET" "cd '$APP_DIR' && docker compose ps"
ssh "$SSH_TARGET" "curl -kfsS https://127.0.0.1:9910/api/session >/dev/null"

echo "Deployment finished: https://www.chancee-shanghai.cn:9910"
