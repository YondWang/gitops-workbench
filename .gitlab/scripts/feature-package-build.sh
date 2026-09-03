#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 3 ]] || { echo "usage: $0 <resident|deb> <source-dir> <output-dir>" >&2; exit 2; }
kind=$1; source_dir=$2; output_dir=$3
[[ $kind == resident || $kind == deb ]] || { echo "unsupported Feature build kind: $kind" >&2; exit 2; }
[[ -f /.dockerenv || -n ${KUBERNETES_SERVICE_HOST:-} ]] || { echo "Feature build requires an isolated container executor" >&2; exit 1; }
[[ ! -S /var/run/docker.sock ]] || { echo "Docker socket must not be mounted" >&2; exit 1; }
for blocked in GITOPS_FEATURE_REGISTRY_TOKEN GITOPS_FEATURE_NEXTCLOUD_PASSWORD SIMOS_OTA_SECRET_KEY SIMOS_OTA_APP_KEY; do [[ -z ${!blocked:-} ]] || { echo "unexpected credential in Feature build: $blocked" >&2; exit 1; }; done
[[ -f feature-context.json ]] || { echo "validated feature-context.json is required" >&2; exit 1; }
python3 - feature-context.json <<'PY'
import json,re,sys
c=json.load(open(sys.argv[1]))
if c.get('schema') != 3: raise SystemExit('feature context rejected: unsupported schema')
if not re.fullmatch(r'T\d{14}_[A-Za-z0-9.-]+', str(c.get('build_id',''))): raise SystemExit('feature context rejected: invalid Feature build id')
if not isinstance(c.get('components'),list) or not any(x.get('repository_id')=='simos' for x in c['components']): raise SystemExit('feature context rejected: missing simos component')
PY
[[ -d $source_dir ]] || { echo "Feature source directory is missing: $source_dir" >&2; exit 1; }
source_dir=$(cd "$source_dir" && pwd -P)
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd -P)
target="$output_dir/$kind"
rm -rf "$target"
mkdir -p "$target"
manifest=package-registry-result.json
entry=ci/resident/ci-build-resident.sh
if [[ $kind == deb ]]; then manifest=deb-package-registry-result.json; entry=ci/deb/ci-build-debs.sh; fi
set +e
if [[ $kind == resident ]]; then
  CI_PROJECT_DIR="$source_dir" CI_COMMIT_TAG="" SIMOS_CONFIG_MATRIX_DISABLED=true SIMOS_BUILD_IMAGE="${SIMOS_BUILD_IMAGE:-}" SIMOS_PACKAGE_REGISTRY_NAME=simos-resident SIMOS_PACKAGE_REGISTRY_UPLOAD_ENABLED=false SIMOS_PACKAGE_REGISTRY_UPLOAD_REQUIRED=false bash "$source_dir/$entry"
else
  CI_PROJECT_DIR="$source_dir" CI_COMMIT_TAG="" SIMOS_CONFIG_MATRIX_DISABLED=true SIMOS_DEB_BUILD_IMAGE="${SIMOS_DEB_BUILD_IMAGE:-}" SIMOS_DEB_BUILD_MODE=all SIMOS_DEB_BUILD_JOBS=16 SIMOS_DEB_PACKAGE_REGISTRY_NAME=simos-debs SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_ENABLED=false SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_REQUIRED=false bash "$source_dir/$entry"
fi
child_status=$?
set -e
for path in resident-packages resident-package-info deb-packages deb-package-info package-registry-result.json deb-package-registry-result.json build-info.json checksums.txt checksum.md5 config-build-info.env vehicle.info; do
  [[ -e "$source_dir/$path" ]] && cp -a "$source_dir/$path" "$target/"
done
if [[ $child_status -ne 0 ]]; then echo "formal $kind build failed; preserved available diagnostics in $target" >&2; exit $child_status; fi
[[ -f "$target/$manifest" ]] || { echo "formal $kind manifest is missing: $manifest" >&2; exit 1; }
