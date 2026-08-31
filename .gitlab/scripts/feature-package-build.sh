#!/usr/bin/env bash
set -euo pipefail
if [[ "$#" -ne 3 ]]; then
  echo "usage: $0 <resident|deb> <source-dir> <output-dir>" >&2
  exit 2
fi
kind=$1
source_dir=$2
output_dir=$3
# A shell executor ignores `image:`. Fail closed until this Runner is changed
# to a non-privileged container executor without host and Docker mounts.
[[ -f /.dockerenv || -n "${KUBERNETES_SERVICE_HOST:-}" ]] || { echo "Feature build requires an isolated container executor" >&2; exit 1; }
[[ ! -S /var/run/docker.sock ]] || { echo "Docker socket must not be mounted" >&2; exit 1; }
for blocked in GITOPS_FEATURE_REGISTRY_TOKEN GITOPS_FEATURE_NEXTCLOUD_PASSWORD SIMOS_OTA_SECRET_KEY SIMOS_OTA_APP_KEY; do
  [[ -z "${!blocked:-}" ]] || { echo "unexpected credential in Feature build: $blocked" >&2; exit 1; }
done

case "$kind" in
  resident|deb) ;;
  *) echo "unsupported Feature build kind: $kind" >&2; exit 2 ;;
esac
matrix_ref=${SIMOS_MATRIX_CONFIG_REF:?SIMOS_MATRIX_CONFIG_REF is required}
matrix_label=${SIMOS_MATRIX_CONFIG_LABEL:?SIMOS_MATRIX_CONFIG_LABEL is required}
context_file=feature-context.json
[[ -f "$context_file" ]] || { echo "validated feature-context.json is required" >&2; exit 1; }

build_id="$(python3 - "$context_file" "$matrix_ref" "$matrix_label" <<'PY'
import json
import re
import sys
from pathlib import Path

expected_config_source = {
    "mode": "formal_matrix",
    "project": "OS/config",
    "variants": [
        {"ref": "SIMBOT_R6_A", "label": "360"},
        {"ref": "SIMBOT_R6_B", "label": "360s"},
    ],
}

try:
    context = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception as exc:
    raise SystemExit(f"feature context rejected: {exc}")
if not isinstance(context, dict) or context.get("schema") != 2:
    raise SystemExit("feature context rejected: unsupported schema")
if context.get("config_source") != expected_config_source:
    raise SystemExit("feature context rejected: invalid formal_matrix policy")
pair = {"ref": sys.argv[2], "label": sys.argv[3]}
if pair not in expected_config_source["variants"]:
    raise SystemExit("feature context rejected: matrix pair is not signed")
build_id = str(context.get("build_id") or "")
if not re.fullmatch(r"T\d{14}_[A-Za-z0-9.-]+", build_id):
    raise SystemExit("feature context rejected: invalid Feature build id")
print(build_id)
PY
)"

[[ -d "$source_dir" ]] || { echo "Feature source directory is missing: $source_dir" >&2; exit 1; }
source_dir="$(cd -- "$source_dir" && pwd -P)"
mkdir -p "$output_dir"
output_dir="$(cd -- "$output_dir" && pwd -P)"
variant_dir="$output_dir/$kind/$matrix_label"
rm -rf -- "$variant_dir"
mkdir -p "$variant_dir"

if [[ "$kind" == "resident" ]]; then
  CI_PROJECT_DIR="$source_dir" \
  CI_COMMIT_TAG="$build_id" \
  SIMOS_MATRIX_CONFIG_REF="$matrix_ref" \
  SIMOS_MATRIX_CONFIG_LABEL="$matrix_label" \
  SIMOS_PACKAGE_REGISTRY_NAME=simos-resident \
  SIMOS_PACKAGE_REGISTRY_UPLOAD_ENABLED=false \
  SIMOS_PACKAGE_REGISTRY_UPLOAD_REQUIRED=false \
    bash "$source_dir/ci/resident/ci-build-resident.sh"
  outputs=(
    resident-packages
    resident-package-info
    package-registry-result.json
    build-info.json
    checksums.txt
    checksum.md5
    config-build-info.env
  )
  manifest=package-registry-result.json
else
  CI_PROJECT_DIR="$source_dir" \
  CI_COMMIT_TAG="$build_id" \
  SIMOS_MATRIX_CONFIG_REF="$matrix_ref" \
  SIMOS_MATRIX_CONFIG_LABEL="$matrix_label" \
  SIMOS_DEB_PACKAGE_REGISTRY_NAME=simos-debs \
  SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_ENABLED=false \
  SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_REQUIRED=false \
    bash "$source_dir/ci/deb/ci-build-debs.sh"
  outputs=(
    deb-packages
    deb-package-info
    deb-package-registry-result.json
    config-build-info.env
    vehicle.info
  )
  manifest=deb-package-registry-result.json
fi

for output in "${outputs[@]}"; do
  if [[ -e "$source_dir/$output" || -L "$source_dir/$output" ]]; then
    cp -a -- "$source_dir/$output" "$variant_dir/"
  fi
done
[[ -f "$variant_dir/$manifest" ]] || { echo "formal $kind manifest is missing: $manifest" >&2; exit 1; }
