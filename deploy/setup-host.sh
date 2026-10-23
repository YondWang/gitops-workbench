#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ "$#" -eq 1 && ( "$1" == --check || "$1" == --apply ) ]] || {
  echo "usage: bash $0 --check|--apply" >&2; exit 1;
}
if ! command -v python3 >/dev/null; then
  if [[ "$1" == --check ]]; then
    echo "host dependencies: python3 missing; installation required"
    exit 10
  fi
  [[ "$EUID" -eq 0 ]] || { echo "python3 installation requires sudo" >&2; exit 1; }
  apt-get update
  apt-get install -y --no-install-recommends python3
fi
exec python3 "$SCRIPT_DIR/host-dependencies.py" "$1"
