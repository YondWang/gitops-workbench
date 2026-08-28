#!/usr/bin/env bash
set -euo pipefail
source_dir=${1:?source directory required}
output_dir=${2:?output directory required}
build_mode=${SIMOS_FEATURE_BUILD_MODE:-all}
# A shell executor ignores `image:`. Fail closed until this Runner is changed
# to a non-privileged container executor without host and Docker mounts.
[[ -f /.dockerenv || -n "${KUBERNETES_SERVICE_HOST:-}" ]] || { echo "Feature build requires an isolated container executor" >&2; exit 1; }
[[ ! -S /var/run/docker.sock ]] || { echo "Docker socket must not be mounted" >&2; exit 1; }
for blocked in GITOPS_FEATURE_REGISTRY_TOKEN GITOPS_FEATURE_NEXTCLOUD_PASSWORD SIMOS_OTA_SECRET_KEY SIMOS_OTA_APP_KEY; do
  [[ -z "${!blocked:-}" ]] || { echo "unexpected credential in Feature build: $blocked" >&2; exit 1; }
done
case "$build_mode" in
  all|rebuild|simos|business|localization|mapengine|perception|pnc) ;;
  *)
    echo "Unsupported SIMOS_FEATURE_BUILD_MODE: $build_mode" >&2
    echo "Expected one of: all, rebuild, simos, business, localization, mapengine, perception, pnc" >&2
    exit 2
    ;;
esac
performance_file="$source_dir/ci/resident/build-performance.env"
if [[ -f "$performance_file" ]]; then
  # Reuse the same concurrency source as the SimOS resident CI. The Feature
  # build image is non-privileged, so only the exported build limits are used.
  # shellcheck disable=SC1090
  source "$performance_file"
fi
build_jobs=${SIMOS_FEATURE_BUILD_JOBS:-${SIMOS_BUILD_JOBS:-4}}
if [[ ! "$build_jobs" =~ ^[1-9][0-9]*$ ]]; then
  echo "SIMOS_FEATURE_BUILD_JOBS must be a positive integer: $build_jobs" >&2
  exit 2
fi
# build_all_debs.sh invokes dpkg-buildpackage directly, bypassing the newer
# ci/deb parallelism cap. Patch the checked-out package rules for this Job so
# every catkin_make invocation uses the resident CI concurrency limit.
while IFS= read -r rules_path; do
  sed -i -E "s|^LOGICAL_CORES=.*$|LOGICAL_CORES=${build_jobs}|" "$rules_path"
done < <(find "$source_dir" -path '*/debian/rules' -type f -print)
export SIMOS_BUILD_JOBS="$build_jobs" SIMOS_DEB_BUILD_JOBS="$build_jobs"
mkdir -p "$output_dir"
( cd "$source_dir" && bash ./build_all_debs.sh "$build_mode" )
find "$source_dir" -type f \( -name '*.zip' -o -name '*.deb' -o -name '*.ddeb' -o -name '*.changes' -o -name '*.buildinfo' \) -print0 | xargs -0 -r -I{} cp -f {} "$output_dir/"
# Reuse the resident CI's in-container build stage. Its Docker wrapper is
# deliberately not used: this Feature Job already runs in the isolated build
# container. The script creates the complete config-matrix directory layout.
(
  cd "$source_dir"
  SIMOS_PROJECT_ROOT="$source_dir" bash ci/resident/container-build-resident.sh
)
[[ -d "$source_dir/resident-packages" ]] || { echo "Missing resident-packages output" >&2; exit 1; }
rm -rf "$output_dir/resident-packages"
cp -a "$source_dir/resident-packages" "$output_dir/resident-packages"
find "$output_dir" -type f | grep -q . || { echo "Feature build produced no package artifact" >&2; exit 1; }
