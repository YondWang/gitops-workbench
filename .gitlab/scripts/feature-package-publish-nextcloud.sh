#!/usr/bin/env bash
set -euo pipefail

kind=${1:?kind required}
context_path=${2:?context required}
publish_dir=${3:?registry result directory required}
result_path=${4:?result required}
case "$kind" in resident|deb) ;; *) echo "feature Nextcloud: kind must be resident or deb" >&2; exit 2;; esac
: "${CI_JOB_TOKEN:?CI_JOB_TOKEN is required}"
: "${CI_API_V4_URL:?CI_API_V4_URL is required}"
: "${CI_PROJECT_ID:?CI_PROJECT_ID is required}"

# Registry publication has already verified the frozen formal manifests. This
# publisher trusts only that artifact and the signed context, never the
# Feature checkout or a locally scanned output directory.
python3 - "$kind" "$context_path" "$publish_dir/registry-result.json" "$result_path" <<'PY'
import hashlib
import json
import os
import re
import subprocess
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit


kind, context_path, registry_path, result_path = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
BUILD_ID_PATTERN = re.compile(r"T\d{14}_[A-Za-z0-9.-]+$")
MD5_PATTERN = re.compile(r"[0-9a-f]{32}$")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}$")
PROJECT_ID_PATTERN = re.compile(r"[1-9][0-9]*$")
ITEM_KEYS = {"package_name", "registry_file", "registry_url", "local_path", "kind", "size", "md5", "sha256", "nextcloud_path"}
PACKAGE_NAMES = {"resident": "feature-resident", "deb": "feature-debs"}


def reject(message: str) -> None:
    raise SystemExit(f"feature Nextcloud preflight rejected: {message}")


def load_object(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        reject(f"invalid {description}: {exc}")
    if not isinstance(value, dict):
        reject(f"invalid {description}: expected object")
    return value


def safe_relative_path(value: object, description: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        reject(f"{description} must be a non-empty relative path")
    if value.startswith("/") or value.endswith("/") or "\\" in value:
        reject(f"unsafe {description}: {value!r}")
    parts = value.split("/")
    for part in parts:
        if not part or part in {".", ".."} or "\x00" in part or any(char in "\r\n\t" for char in part):
            reject(f"unsafe {description}: {value!r}")
    return tuple(parts)


def safe_leaf(value: object, description: str) -> str:
    parts = safe_relative_path(value, description)
    if len(parts) != 1:
        reject(f"unsafe {description}: {value!r}")
    return parts[0]


def validate_url(value: object, description: str) -> str:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        reject(f"invalid {description}")
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        reject(f"invalid {description}")
    return value


def protected_text(value: object, description: str) -> str:
    if not isinstance(value, str) or not value or any(char in "\x00\r\n" for char in value):
        reject(f"invalid protected {description}")
    return value


def digest(path: Path) -> tuple[int, str, str]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            md5.update(chunk)
            sha256.update(chunk)
    return size, md5.hexdigest(), sha256.hexdigest()


context = load_object(context_path, "Feature context")
build_id = context.get("build_id")
if not isinstance(build_id, str) or not BUILD_ID_PATTERN.fullmatch(build_id):
    reject("Feature context has an invalid build_id")
cloud_parts = safe_relative_path(context.get("cloud_category"), "cloud category")
if context.get("schema") != 3 or "config_source" in context:
    reject("Feature context must be schema 3 without config_source")
registry_target = context.get("registry")
if not isinstance(registry_target, dict) or not isinstance(registry_target.get("project"), str) or not registry_target["project"]:
    reject("Feature context has an invalid Registry target")
gitlab_api = validate_url(os.environ["CI_API_V4_URL"], "GitLab API endpoint").rstrip("/")
project_id = os.environ["CI_PROJECT_ID"]
if not PROJECT_ID_PATTERN.fullmatch(project_id):
    reject("invalid protected SimOS project id")

registry = load_object(registry_path, "Registry result")
if registry.get("build_id") != build_id:
    reject("Registry result build_id does not match Feature context")
if registry.get("project") != registry_target["project"]:
    reject("Registry result project does not match Feature context")
if registry.get("kind") != kind:
    reject("Registry result kind does not match publisher kind")
package_name = PACKAGE_NAMES[kind]
package = registry.get(kind)
if not isinstance(package, dict) or package.get("package_name") != package_name or package.get("package_version") != build_id:
    reject(f"Registry result has an invalid {kind} package")
files = registry.get("files")
if not isinstance(files, list):
    reject("Registry result files must be a list")

# Complete every structural validation before the first curl. The publishing
# plan is immutable after this point, preventing partially accepted paths.
plan: list[dict] = []
targets: set[tuple[str, ...]] = set()
for position, item in enumerate(files):
    if not isinstance(item, dict) or set(item) != ITEM_KEYS:
        reject(f"Registry result file #{position} has an invalid schema")
    item_kind = item["kind"]
    if item_kind not in PACKAGE_NAMES:
        reject(f"Registry result file #{position} has an invalid kind")
    if item_kind != kind:
        reject(f"Registry result file #{position} kind does not match publisher kind")
    if item["package_name"] != PACKAGE_NAMES[item_kind]:
        reject(f"Registry result file #{position} has an invalid package name")
    registry_file = safe_leaf(item["registry_file"], f"Registry result file #{position} registry_file")
    local_parts = safe_relative_path(item["local_path"], f"Registry result file #{position} local_path")
    nextcloud_parts = safe_relative_path(item["nextcloud_path"], f"Registry result file #{position} nextcloud_path")
    if nextcloud_parts[0] != kind or local_parts[0] != kind:
        reject(f"Registry result file #{position} path does not match its kind")
    registry_url = validate_url(item["registry_url"], f"Registry result file #{position} registry_url")
    expected_registry_url = (
        f"{gitlab_api}/projects/{project_id}/packages/generic/"
        f"{quote(PACKAGE_NAMES[kind], safe='')}/{quote(build_id, safe='')}/{quote(registry_file, safe='')}"
    )
    if registry_url != expected_registry_url:
        reject(f"Registry result file #{position} registry_url is not the protected Generic Package endpoint")
    size, md5, sha256 = item["size"], item["md5"], item["sha256"]
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        reject(f"Registry result file #{position} has an invalid size")
    if not isinstance(md5, str) or not MD5_PATTERN.fullmatch(md5):
        reject(f"Registry result file #{position} has an invalid MD5")
    if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
        reject(f"Registry result file #{position} has an invalid SHA-256")
    if nextcloud_parts in targets:
        reject(f"Registry result has a duplicate Nextcloud path: {item['nextcloud_path']}")
    targets.add(nextcloud_parts)
    plan.append({
        **item,
        "registry_file": registry_file,
        # Route only to the protected endpoint constructed above, even though
        # the inter-job artifact was required to match it exactly.
        "registry_url": expected_registry_url,
        "nextcloud_parts": nextcloud_parts,
    })

for path in targets:
    for other in targets:
        if path != other and len(path) < len(other) and other[:len(path)] == path:
            reject(f"Registry result has a file/directory path conflict: {'/'.join(path)}")

job_token = protected_text(os.environ["CI_JOB_TOKEN"], "Job Token")
publisher = "/usr/local/bin/simos-ci-publish-resident"
if not plan:
    reject("Registry result contains no files")
directory_parts = (*cloud_parts, build_id)

# Reconstruct the formal server publisher input from verified local paths.
incoming_targets = set()
for item in plan:
    parts = safe_relative_path(item["local_path"], "local path")[1:]
    if kind == "deb" and parts and parts[0] in {"deb_packages", "deb-packages"}:
        parts = ("deb-packages", *parts[1:])
    if not parts or parts in incoming_targets:
        reject("duplicate incoming path")
    incoming_targets.add(parts)
    item["incoming_parts"] = parts
    # The server removes the package directory and retains only its supported
    # resident sidecars. Other build metadata remains available in Registry.
    if parts[0] == f"{kind}-packages" and len(parts) > 1:
        if kind == "deb" or parts[-1] in {
            "resident.tar.gz", "simos.config", "deploy.sh", "remote_run.sh",
            "checksum.md5", "resident.md5", "build-info.json",
        } and len(parts) in {2, 3}:
            item["published_path"] = "/".join((kind, *parts[1:]))
for path in incoming_targets:
    if any(path[:depth] in incoming_targets for depth in range(1, len(path))):
        reject("incoming file/directory conflict")
if not any("published_path" in item for item in plan):
    reject("Registry result contains no publishable package files")
if kind == "resident":
    package_paths = {path for path in incoming_targets if path[0] == "resident-packages"}
    variants = {path[1:-1] for path in package_paths}
    required = {"resident.tar.gz", "simos.config", "deploy.sh", "remote_run.sh", "checksum.md5"}
    if not variants or any(len(variant) > 1 for variant in variants):
        reject("invalid resident package layout")
    for variant in variants:
        if not all(("resident-packages", *variant, name) in package_paths for name in required):
            reject("resident package is missing required files")
try:
    capability = subprocess.run(
        ["sudo", "-n", publisher, "--capability", "feature-package-v1"],
        capture_output=True, text=True,
    )
except OSError:
    reject("cannot execute server publisher through sudo")
if capability.returncode != 0 or capability.stdout.strip() != "feature-package-v1":
    reject("server publisher requires feature-package-v1 and passwordless Runner sudo")


def curl_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def write_curl_config(path: Path, *, url: str, options: list[tuple[str, str]], flags: tuple[str, ...]) -> Path:
    lines = [f'url = "{curl_value(url)}"']
    lines.extend(f'{name} = "{curl_value(value)}"' for name, value in options)
    lines.extend(flags)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def run_curl(config_path: Path):
    try:
        # -q must be first: curl otherwise reads a runner-provided curlrc
        # before this protected configuration, which could enable redirects.
        return subprocess.run(["curl", "-q", "--config", str(config_path)], capture_output=True, text=True, check=False)
    except OSError:
        raise SystemExit("feature Nextcloud publication cannot execute curl") from None


result_path.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix=".feature-package-nextcloud-", dir=result_path.parent) as temporary:
    staging = Path(temporary)
    staging.chmod(0o700)
    registry_curl_config = staging / "registry-curl.conf"
    incoming = staging / "incoming"
    incoming.mkdir()
    # Download and verify the entire trusted Registry plan before issuing a
    # single Nextcloud write. This prevents a later corrupt download from
    # leaving a partially published package layout.
    for index, item in enumerate(plan):
        local = staging / f"{index:04d}-{item['registry_file']}"
        completed = run_curl(write_curl_config(
            registry_curl_config,
            url=item["registry_url"],
            options=[("header", f"JOB-TOKEN: {job_token}"), ("output", str(local)), ("write-out", "%{http_code}")],
            flags=("fail", "silent", "show-error"),
        ))
        if completed.returncode != 0 or completed.stdout.strip() != "200":
            raise SystemExit(f"feature Nextcloud publication failed to download Registry file: {item['registry_file']}")
        actual = digest(local)
        if actual != (item["size"], item["md5"], item["sha256"]):
            raise SystemExit(f"feature Nextcloud publication rejected downloaded Registry file: {item['registry_file']}")
        item["staged_path"] = local

    for item in plan:
        target = incoming.joinpath(*item["incoming_parts"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(item["staged_path"]), target)
    source = context.get("source") or {}
    command = [
        "sudo", "-n", publisher,
        "--incoming", str(incoming.resolve()),
        "--feature-build-id", build_id,
        "--tag", build_id, "--version", build_id,
        "--date", f"{build_id[1:5]}-{build_id[5:7]}-{build_id[7:9]}",
        "--category", "/".join(cloud_parts),
        "--project", registry_target["project"],
        "--commit-sha", str(source.get("sha") or ""),
        "--pipeline-id", os.environ.get("CI_PIPELINE_ID", ""),
        "--job-id", os.environ.get("CI_JOB_ID", ""),
        "--pipeline-url", os.environ.get("CI_PIPELINE_URL", ""),
    ]
    if kind == "deb":
        command.append("--deb-only")
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        raise SystemExit(f"feature server publication failed (exit {completed.returncode})")

published_files = [
    {**{key: item[key] for key in ITEM_KEYS}, "nextcloud_path": item["published_path"]}
    for item in plan if "published_path" in item
]
result = {
    "status": "success",
    "build_id": build_id,
    "components": context.get("components", []),
    "registry": registry,
    "nextcloud": {
        "cloud_dir": "/public/Versions/" + "/".join((*directory_parts, kind)),
        "files": published_files,
    },
}
temporary_result = result_path.with_name(result_path.name + ".tmp")
temporary_result.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary_result.replace(result_path)
PY
