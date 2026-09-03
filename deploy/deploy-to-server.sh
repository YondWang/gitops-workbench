#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="${SSH_TARGET:-admin1@192.168.110.222}"
APP_DIR="${APP_DIR:-/opt/gitops-workbench}"
DATA_DIR="${DATA_DIR:-/data/gitops-workbench}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ssh "$SSH_TARGET" "test -d '$APP_DIR' && test -w '$APP_DIR'"
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
