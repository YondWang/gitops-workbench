#!/usr/bin/env python3
import base64, hashlib, hmac, json, os, re
from datetime import datetime, timezone
from pathlib import Path

def fail(message):
    raise SystemExit(f"feature context rejected: {message}")

encoded = os.environ.get("GITOPS_FEATURE_CONTEXT_B64", "")
signature = os.environ.get("GITOPS_FEATURE_CONTEXT_HMAC", "")
key = os.environ.get("GITOPS_FEATURE_CONTEXT_HMAC_KEY", "")
if os.environ.get("GITOPS_FEATURE_PACKAGE") != "1": fail("missing feature pipeline sentinel")
if os.environ.get("CI_PIPELINE_SOURCE") != "api" or os.environ.get("CI_COMMIT_REF_NAME") != "ci/feature-package": fail("not a trusted API pipeline on ci/feature-package")
if not encoded or not signature or not key: fail("signature context or protected key is missing")
expected = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, signature): fail("HMAC mismatch")
try: context = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
except Exception as exc: fail(f"context decode failed: {exc}")
if not isinstance(context, dict) or context.get("schema") != 3: fail("unsupported schema")
if "config_source" in context: fail("schema 3 does not accept config_source")
try: expires = datetime.fromisoformat(str(context["expires_at"])).astimezone(timezone.utc)
except Exception: fail("invalid expiry")
if expires <= datetime.now(timezone.utc): fail("context expired")
if not re.fullmatch(r"T\d{14}_[A-Za-z0-9.-]+", str(context.get("build_id") or "")): fail("invalid Feature build id")
source = context.get("source") or {}
sha = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
if not isinstance(source, dict) or not source.get("repository_id") or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", str(source.get("project") or "")) or not re.fullmatch(r"feature/.+", str(source.get("ref") or "")) or not sha.fullmatch(str(source.get("sha") or "")):
    fail("invalid source snapshot")
categories = {item.strip() for item in os.environ.get("GITOPS_FEATURE_CLOUD_CATEGORIES", "").split(",") if item.strip()}
if not categories or context.get("cloud_category") not in categories: fail("cloud category is not in protected allow-list")
components = context.get("components")
if not isinstance(components, list) or not components: fail("component snapshot missing")
repos, paths = set(), set(); has_simos = False
component_simos_sha = None
for component in components:
    if not isinstance(component, dict): fail("invalid component snapshot")
    required = ("repository_id", "project", "requested_ref", "resolved_ref", "commit_id", "resolution")
    if any(not component.get(k) for k in required) or "submodule_path" not in component: fail("invalid component snapshot")
    repo, path = str(component["repository_id"]), str(component["submodule_path"])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", str(component["repository_id"])): fail("invalid repository id")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", str(component["project"])): fail("invalid project")
    for ref_key in ("requested_ref", "resolved_ref"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", str(component[ref_key])): fail("invalid component ref")
    if repo in repos: fail("duplicate repository")
    if path and path in paths: fail("duplicate submodule path")
    if path and (not path.startswith("src/") or ".." in path or "\\" in path): fail("invalid submodule path")
    if not sha.fullmatch(str(component["commit_id"])): fail("invalid component SHA")
    repos.add(repo); paths.add(path); has_simos = has_simos or repo == "simos"
    if repo == "simos": component_simos_sha = str(component["commit_id"])
if not has_simos: fail("component snapshot must include simos")
if component_simos_sha != str(source.get("sha")): fail("simos component does not match source SHA")
metadata = context.get("metadata")
if not isinstance(metadata, dict) or not isinstance(metadata.get("version_info"), str) or not isinstance(metadata.get("software_yaml"), str): fail("metadata payload missing")
Path("feature-context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
