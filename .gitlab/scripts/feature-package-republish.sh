#!/usr/bin/env bash
set -euo pipefail
[[ ${GITOPS_FEATURE_REPUBLISH_JOB_ID:-} =~ ^[1-9][0-9]*$ ]] || { echo "positive build Job ID required" >&2; exit 2; }
case "${GITOPS_FEATURE_REPUBLISH_KIND:-}" in resident|deb) ;; *) echo "resident or deb kind required" >&2; exit 2;; esac
: "${CI_API_V4_URL:?}"; : "${CI_PROJECT_ID:?}"; : "${CI_JOB_TOKEN:?}"
python3 - <<'PY'
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

endpoint = os.environ["CI_API_V4_URL"].rstrip("/")
parsed = urlsplit(endpoint)
if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
        or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in endpoint)):
    raise SystemExit("invalid GitLab API endpoint")
if not re.fullmatch(r"[1-9][0-9]*", os.environ["CI_PROJECT_ID"]):
    raise SystemExit("positive project ID required")
token = os.environ["CI_JOB_TOKEN"]
if any(c in token for c in "\r\n\x00"):
    raise SystemExit("invalid Job Token")
url = f"{endpoint}/projects/{os.environ['CI_PROJECT_ID']}/jobs/{os.environ['GITOPS_FEATURE_REPUBLISH_JOB_ID']}/artifacts"
def quoted(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

with tempfile.TemporaryDirectory(prefix=".feature-republish-") as temporary:
    root = Path(temporary)
    archive_path = root / "artifacts.zip"
    config = root / "curl.conf"
    config.write_text("\n".join((
        "url = " + quoted(url), "header = " + quoted("JOB-TOKEN: " + token),
        "output = " + quoted(str(archive_path)), 'write-out = "%{http_code}"',
        "fail", "silent", "show-error", "retry = 3",
    )) + "\n")
    config.chmod(0o600)
    result = subprocess.run(["curl", "-q", "--config", str(config)], capture_output=True, text=True)
    if result.returncode or result.stdout.strip() != "200":
        raise SystemExit("cannot download source build artifacts (expected HTTP 200)")
    # Read only metadata; never extract source or executable artifacts.
    payloads = {}
    with zipfile.ZipFile(archive_path) as archive:
        for name in ("feature-context.json", "feature-publish/registry-result.json"):
            if archive.namelist().count(name) != 1:
                raise SystemExit("missing or duplicate publication metadata")
            info = archive.getinfo(name)
            if info.file_size > 10 * 1024 * 1024:
                raise SystemExit("oversized publication metadata")
            payloads[name] = archive.read(info)
    plan = json.loads(payloads["feature-publish/registry-result.json"])
    if not isinstance(plan, dict) or plan.get("kind") != os.environ["GITOPS_FEATURE_REPUBLISH_KIND"]:
        raise SystemExit("source build kind does not match republish kind")
    for name, contents in payloads.items():
        target = Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
PY
bash .gitlab/scripts/feature-package-publish-nextcloud.sh \
  "$GITOPS_FEATURE_REPUBLISH_KIND" feature-context.json feature-publish feature-package-result.json
