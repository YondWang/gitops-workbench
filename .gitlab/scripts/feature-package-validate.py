#!/usr/bin/env python3
"""Validate the only browser-untrusted input accepted by Feature CI."""
import base64
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def fail(message):
    raise SystemExit(f"feature context rejected: {message}")


encoded = os.environ.get("GITOPS_FEATURE_CONTEXT_B64", "")
signature = os.environ.get("GITOPS_FEATURE_CONTEXT_HMAC", "")
key = os.environ.get("GITOPS_FEATURE_CONTEXT_HMAC_KEY", "")
if os.environ.get("GITOPS_FEATURE_PACKAGE") != "1":
    fail("missing feature pipeline sentinel")
if os.environ.get("CI_PIPELINE_SOURCE") != "api" or os.environ.get("CI_COMMIT_REF_NAME") != "ci/feature-package":
    fail("not a trusted API pipeline on ci/feature-package")
if not encoded or not signature or not key:
    fail("signature context or protected key is missing")
expected = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, signature):
    fail("HMAC mismatch")
try:
    context = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
except Exception as exc:
    fail(f"context decode failed: {exc}")
if not isinstance(context, dict) or context.get("schema") != 1:
    fail("unsupported schema")
try:
    expires = datetime.fromisoformat(str(context["expires_at"])).astimezone(timezone.utc)
except Exception:
    fail("invalid expiry")
if expires <= datetime.now(timezone.utc):
    fail("context expired")
if not re.fullmatch(r"T\d{14}_[A-Za-z0-9.-]+", str(context.get("build_id") or "")):
    fail("invalid Feature build id")
source = context.get("source") or {}
if (
    not isinstance(source, dict)
    or not source.get("repository_id")
    or not source.get("project")
    or not re.fullmatch(r"feature/.+", str(source.get("ref") or ""))
):
    fail("source must be feature/*")
# Git may use SHA-1 today or SHA-256 in a future repository. Short hashes are
# deliberately rejected: the server must freeze an unambiguous object ID.
sha = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
if not sha.fullmatch(str(source.get("sha") or "")):
    fail("invalid source SHA")
categories = {item.strip() for item in os.environ.get("GITOPS_FEATURE_CLOUD_CATEGORIES", "").split(",") if item.strip()}
if not categories or context.get("cloud_category") not in categories:
    fail("cloud category is not in protected allow-list")
components = context.get("components")
if not isinstance(components, list) or not components:
    fail("component snapshot missing")
for component in components:
    if not isinstance(component, dict) or not all(component.get(key) for key in ("repo", "project", "ref", "sha")) or "submodule_path" not in component:
        fail("invalid component snapshot")
    if not sha.fullmatch(str(component["sha"])):
        fail("invalid component SHA")
metadata = context.get("metadata")
if not isinstance(metadata, dict) or not isinstance(metadata.get("version_info"), str) or not isinstance(metadata.get("software_yaml"), str):
    fail("metadata payload missing")
Path("feature-context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
