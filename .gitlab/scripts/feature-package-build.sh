#!/usr/bin/env bash
set -euo pipefail
source_dir=${1:?source directory required}
output_dir=${2:?output directory required}
# A shell executor ignores `image:`. Fail closed until this Runner is changed
# to a non-privileged container executor without host and Docker mounts.
[[ -f /.dockerenv || -n "${KUBERNETES_SERVICE_HOST:-}" ]] || { echo "Feature build requires an isolated container executor" >&2; exit 1; }
[[ ! -S /var/run/docker.sock ]] || { echo "Docker socket must not be mounted" >&2; exit 1; }
for blocked in GITOPS_FEATURE_REGISTRY_TOKEN GITOPS_FEATURE_NEXTCLOUD_PASSWORD SIMOS_OTA_SECRET_KEY SIMOS_OTA_APP_KEY; do
  [[ -z "${!blocked:-}" ]] || { echo "unexpected credential in Feature build: $blocked" >&2; exit 1; }
done
mkdir -p "$output_dir"
( cd "$source_dir" && bash ./build_all_debs.sh "${SIMOS_FEATURE_BUILD_MODE:-release}" )
find "$source_dir" -type f \( -name '*.zip' -o -name '*.deb' -o -name '*.ddeb' -o -name '*.changes' -o -name '*.buildinfo' \) -print0 | xargs -0 -r -I{} cp -f {} "$output_dir/"
find "$output_dir" -type f | grep -q . || { echo "Feature build produced no package artifact" >&2; exit 1; }
