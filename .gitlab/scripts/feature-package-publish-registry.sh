#!/usr/bin/env bash
set -euo pipefail

context_path=${1:?context required}
output_dir=${2:?output required}
publish_dir=${3:?publish directory required}
: "${CI_API_V4_URL:?CI_API_V4_URL is required}"
: "${GITOPS_FEATURE_SIMOS_PROJECT_ID:?GITOPS_FEATURE_SIMOS_PROJECT_ID is required}"
: "${CI_JOB_TOKEN:?CI_JOB_TOKEN is required}"

# The embedded verifier intentionally completes before it invokes curl. The
# Feature checkout can produce arbitrary artifacts, so a partial validation
# must never publish even one file.
python3 - "$context_path" "$output_dir" "$publish_dir" <<'PY'
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

context_path, output_root, publish_dir = map(Path, sys.argv[1:])

FORMAL_CONFIG_SOURCE = {
    "mode": "formal_matrix",
    "project": "OS/config",
    "variants": [
        {"ref": "SIMBOT_R6_A", "label": "360"},
        {"ref": "SIMBOT_R6_B", "label": "360s"},
    ],
}
RESIDENT_KEYS = {
    "resident": "resident.tar.gz",
    "resident_md5": "resident.md5",
    "simos_config": "simos.config",
    "deploy_sh": "deploy.sh",
    "remote_run_sh": "remote_run.sh",
    "checksum_md5": "checksum.md5",
}
DEB_FILE_PATTERN = re.compile(r"(?:.+\.(?:deb|ddeb|changes|buildinfo)|resident_.+\.tar\.gz|simos_.+\.zip|config\.yaml)$")
BUILD_ID_PATTERN = re.compile(r"T\d{14}_[A-Za-z0-9.-]+$")


def reject(message: str) -> None:
    raise SystemExit(f"feature Registry preflight rejected: {message}")


def load_json(path: Path, description: str) -> dict:
    if not path.is_file():
        reject(f"missing {description}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        reject(f"invalid {description}: {exc}")
    if not isinstance(value, dict):
        reject(f"invalid {description}: expected object")
    return value


def safe_name(value: object, description: str) -> str:
    text = str(value or "")
    if not text or text in {".", ".."} or "/" in text or "\\" in text or ".." in text:
        reject(f"unsafe {description}: {text!r}")
    return text


def local_relative(path: Path) -> str:
    try:
        return str(path.relative_to(output_root))
    except ValueError:
        reject(f"file is outside Feature output: {path}")
    raise AssertionError("unreachable")


def digest(path: Path) -> tuple[int, str, str]:
    if not path.is_file():
        reject(f"listed file is missing: {local_relative(path)}")
    data = path.read_bytes()
    return len(data), hashlib.md5(data).hexdigest(), hashlib.sha256(data).hexdigest()


def require_digest(path: Path, item: dict, require_size: bool = False) -> tuple[int, str, str]:
    size, md5, sha256 = digest(path)
    expected_md5 = item.get("md5")
    expected_sha256 = item.get("sha256")
    if not isinstance(expected_md5, str) or not re.fullmatch(r"[0-9a-f]{32}", expected_md5):
        reject(f"invalid MD5 for {local_relative(path)}")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        reject(f"invalid SHA-256 for {local_relative(path)}")
    if expected_md5 != md5 or expected_sha256 != sha256:
        reject(f"digest mismatch for {local_relative(path)}")
    if "size" in item:
        if not isinstance(item["size"], int) or item["size"] != size:
            reject(f"size mismatch for {local_relative(path)}")
    elif require_size:
        reject(f"missing size for {local_relative(path)}")
    return size, md5, sha256


def add_plan(plan: list[dict], *, package_name: str, registry_file: str, local: Path, label: str, kind: str, nextcloud_path: str,
             expected: dict | None = None, require_size: bool = False) -> None:
    registry_file = safe_name(registry_file, "registry file")
    size, md5, sha256 = require_digest(local, expected, require_size) if expected is not None else digest(local)
    plan.append({
        "package_name": package_name,
        "registry_file": registry_file,
        "local_path": local_relative(local),
        "label": label,
        "kind": kind,
        "size": size,
        "md5": md5,
        "sha256": sha256,
        "nextcloud_path": nextcloud_path,
    })


def one_signed_variant(manifest: dict, config_ref: str, label: str, kind: str) -> dict:
    if manifest.get("status") not in {"success", "skipped"}:
        reject(f"{kind}/{label} manifest status is not success or skipped")
    if manifest.get("config_ref") != config_ref:
        reject(f"{kind}/{label} manifest config_ref does not match signed matrix")
    variants = manifest.get("config_variants")
    if not isinstance(variants, list) or len(variants) != 1 or not isinstance(variants[0], dict):
        reject(f"{kind}/{label} manifest must contain exactly one config variant")
    variant = variants[0]
    if variant.get("config_ref") != config_ref or variant.get("label") != label:
        reject(f"{kind}/{label} manifest Config pair does not match signed matrix")
    return variant


def reject_unlisted_files(root: Path, allowed: set[Path], kind: str, label: str, predicate) -> None:
    if not root.is_dir():
        reject(f"missing {kind}/{label} package directory: {local_relative(root)}")
    for path in root.rglob("*"):
        if path.is_file() and predicate(path) and path not in allowed:
            reject(f"unlisted {kind} package file: {local_relative(path)}")


def optional_metadata(plan: list[dict], package_name: str, variant_dir: Path, label: str, kind: str, names: tuple[str, ...]) -> None:
    for relative in names:
        local = variant_dir / relative
        if not local.is_file():
            continue
        add_plan(
            plan,
            package_name=package_name,
            registry_file=f"{label}-{relative.replace('/', '-')}",
            local=local,
            label=label,
            kind=kind,
            nextcloud_path=f"{kind}/{label}/metadata/{relative}",
        )


def validate_resident(config_ref: str, label: str, plan: list[dict]) -> None:
    kind, package_name = "resident", "simos-resident"
    variant_dir = output_root / kind / label
    manifest_path = variant_dir / "package-registry-result.json"
    manifest = load_json(manifest_path, f"{kind}/{label} manifest")
    if "tag" in manifest and manifest["tag"] != "":
        reject(f"{kind}/{label} manifest has a non-empty tag")
    variant = one_signed_variant(manifest, config_ref, label, kind)
    if variant.get("artifact_path") != f"resident-packages/{label}/resident.tar.gz":
        reject(f"{kind}/{label} artifact_path is not the formal resident path")
    registry_files = variant.get("registry_files")
    if not isinstance(registry_files, dict) or set(registry_files) != set(RESIDENT_KEYS):
        reject(f"{kind}/{label} manifest registry_files is not the formal resident set")
    package_root = variant_dir / "resident-packages" / label
    local_files = {key: package_root / name for key, name in RESIDENT_KEYS.items()}
    for key, path in local_files.items():
        if not isinstance(registry_files[key], str):
            reject(f"{kind}/{label} registry file is invalid for {key}")
        if not registry_files[key].startswith(f"{label}-"):
            reject(f"{kind}/{label} registry file lacks formal label prefix")
        if not path.is_file():
            reject(f"listed file is missing: {local_relative(path)}")
    require_digest(local_files["resident"], variant)
    listed_md5 = local_files["resident_md5"].read_text(encoding="utf-8").split()
    if not listed_md5 or listed_md5[0] != digest(local_files["resident"])[1]:
        reject(f"resident.md5 does not match {local_relative(local_files['resident'])}")
    reject_unlisted_files(package_root, set(local_files.values()), kind, label, lambda _path: True)
    for key, local in local_files.items():
        add_plan(
            plan,
            package_name=package_name,
            registry_file=registry_files[key],
            local=local,
            label=label,
            kind=kind,
            nextcloud_path=f"resident/{label}/{local.name}",
            expected=variant if key == "resident" else None,
        )
    optional_metadata(
        plan, package_name, variant_dir, label, kind,
        (
            "build-info.json", "checksums.txt", "config-build-info.env",
            "resident-package-info/build-info.json", "resident-package-info/checksums.txt",
            "resident-package-info/artifact-path.txt", "resident-package-info/package-registry-result.json",
        ),
    )
    add_plan(
        plan,
        package_name=package_name,
        registry_file=f"{label}-package-registry-result.json",
        local=manifest_path,
        label=label,
        kind=kind,
        nextcloud_path=f"resident/{label}/metadata/package-registry-result.json",
    )


def validate_deb(config_ref: str, label: str, plan: list[dict]) -> None:
    kind, package_name = "deb", "simos-debs"
    variant_dir = output_root / kind / label
    manifest_path = variant_dir / "deb-package-registry-result.json"
    manifest = load_json(manifest_path, f"{kind}/{label} manifest")
    if "tag" not in manifest or manifest["tag"] != "":
        reject(f"{kind}/{label} manifest must contain an empty tag")
    variant = one_signed_variant(manifest, config_ref, label, kind)
    files = manifest.get("files")
    variant_files = variant.get("files")
    if not isinstance(files, list) or not files or not isinstance(variant_files, list) or len(files) != len(variant_files):
        reject(f"{kind}/{label} manifest files do not match its Config variant")
    package_root = variant_dir / "deb-packages" / label
    allowed: set[Path] = set()
    for item, variant_item in zip(files, variant_files):
        if not isinstance(item, dict) or not isinstance(variant_item, dict):
            reject(f"{kind}/{label} manifest contains a non-object file entry")
        if item.get("label") != label or item.get("config_ref") != config_ref:
            reject(f"{kind}/{label} file entry does not match signed Config pair")
        if variant_item != {key: value for key, value in item.items() if key not in {"label", "config_ref"}}:
            reject(f"{kind}/{label} manifest files do not match its Config variant")
        local = package_root / safe_name(item.get("file"), "deb manifest file")
        registry_file = safe_name(item.get("registry_file"), "deb registry file")
        if not registry_file.startswith(f"{label}-"):
            reject(f"{kind}/{label} registry file lacks formal label prefix")
        allowed.add(local)
        add_plan(
            plan,
            package_name=package_name,
            registry_file=registry_file,
            local=local,
            label=label,
            kind=kind,
            nextcloud_path=f"deb/{label}/{local.name}",
            expected=item,
            require_size=True,
        )
    reject_unlisted_files(package_root, allowed, kind, label, lambda path: bool(DEB_FILE_PATTERN.fullmatch(path.name)))
    optional_metadata(
        plan, package_name, variant_dir, label, kind,
        (
            "config-build-info.env", "vehicle.info", "deb-package-info/build-info.json",
            "deb-package-info/deb-package-registry-result.json",
        ),
    )
    add_plan(
        plan,
        package_name=package_name,
        registry_file=f"{label}-deb-package-registry-result.json",
        local=manifest_path,
        label=label,
        kind=kind,
        nextcloud_path=f"deb/{label}/metadata/deb-package-registry-result.json",
    )


context = load_json(context_path, "feature context")
if context.get("schema") != 2 or context.get("config_source") != FORMAL_CONFIG_SOURCE:
    reject("feature context does not contain the exact formal Config policy")
build_id = context.get("build_id")
if not isinstance(build_id, str) or not BUILD_ID_PATTERN.fullmatch(build_id):
    reject("feature context has an invalid Feature build id")
registry = context.get("registry")
if not isinstance(registry, dict) or not isinstance(registry.get("project"), str) or not registry["project"]:
    reject("feature context has an invalid Registry target")
if not output_root.is_dir():
    reject(f"Feature output directory is missing: {output_root}")

upload_plan: list[dict] = []
for signed_variant in FORMAL_CONFIG_SOURCE["variants"]:
    validate_resident(signed_variant["ref"], signed_variant["label"], upload_plan)
    validate_deb(signed_variant["ref"], signed_variant["label"], upload_plan)

seen_registry = set()
for item in upload_plan:
    key = (item["package_name"], item["registry_file"])
    if key in seen_registry:
        reject(f"duplicate Registry target: {key[0]}/{key[1]}")
    seen_registry.add(key)

base = os.environ["CI_API_V4_URL"].rstrip("/")
project_id = os.environ["GITOPS_FEATURE_SIMOS_PROJECT_ID"]
for item in upload_plan:
    item["registry_url"] = f"{base}/projects/{project_id}/packages/generic/{item['package_name']}/{build_id}/{item['registry_file']}"

# No external write is allowed until every artifact, digest, Config pair, and
# Registry path has passed the complete preflight above.
for item in upload_plan:
    subprocess.run([
        "curl", "--fail", "--silent", "--show-error", "--location",
        "--header", f"JOB-TOKEN: {os.environ['CI_JOB_TOKEN']}",
        "--upload-file", str(output_root / item["local_path"]), item["registry_url"],
    ], check=True)

publish_dir.mkdir(parents=True, exist_ok=True)
result = {
    "build_id": build_id,
    "project": registry["project"],
    "resident": {"package_name": "simos-resident", "package_version": build_id},
    "deb": {"package_name": "simos-debs", "package_version": build_id},
    "files": upload_plan,
}
(publish_dir / "registry-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
