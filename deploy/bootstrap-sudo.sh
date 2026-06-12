#!/usr/bin/env bash
set -euo pipefail

APP_USER="${APP_USER:-${SUDO_USER:-admin1}}"
APP_DIR="${APP_DIR:-/opt/gitops-workbench}"
DATA_DIR="${DATA_DIR:-/data/gitops-workbench}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script on the server with sudo." >&2
  exit 1
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "User does not exist: $APP_USER" >&2
  exit 1
fi

mkdir -p "$APP_DIR" "$DATA_DIR/data" "$DATA_DIR/certs" "$DATA_DIR/logs"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "$DATA_DIR"
chmod 750 "$DATA_DIR/certs"

if getent group docker >/dev/null 2>&1; then
  usermod -aG docker "$APP_USER"
else
  echo "Docker group does not exist. Install Docker before deploying." >&2
  exit 1
fi

echo "Bootstrap complete."
echo "Log out and log back in as $APP_USER so the docker group membership takes effect."
echo "Place TLS files here:"
echo "  $DATA_DIR/certs/fullchain.pem"
echo "  $DATA_DIR/certs/privkey.pem"
