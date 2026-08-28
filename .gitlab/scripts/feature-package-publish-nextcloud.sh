#!/usr/bin/env bash
set -euo pipefail
context_path=${1:?context required}; publish_dir=${2:?registry result required}; result_path=${3:?result required}
: "${GITOPS_FEATURE_NEXTCLOUD_URL:?protected Nextcloud endpoint is required}"
: "${GITOPS_FEATURE_NEXTCLOUD_USER:?protected Nextcloud account is required}"
: "${GITOPS_FEATURE_NEXTCLOUD_PASSWORD:?protected Nextcloud password is required}"
python3 - "$context_path" "$publish_dir/registry-result.json" "$result_path" <<'PY'
import json, os, subprocess, sys, tempfile
from pathlib import Path
from urllib.parse import quote
c, registry = json.loads(Path(sys.argv[1]).read_text()), json.loads(Path(sys.argv[2]).read_text())
directory = c["cloud_category"].strip("/") + "/" + c["build_id"]
base = os.environ["GITOPS_FEATURE_NEXTCLOUD_URL"].rstrip("/") + "/remote.php/dav/files/" + os.environ["GITOPS_FEATURE_NEXTCLOUD_USER"] + "/" + directory
auth = os.environ["GITOPS_FEATURE_NEXTCLOUD_USER"] + ":" + os.environ["GITOPS_FEATURE_NEXTCLOUD_PASSWORD"]
subprocess.run(["curl", "--fail", "--silent", "--show-error", "-u", auth, "-X", "MKCOL", base], check=False)
for item in registry["files"]:
    relative = item.get("path") or item["name"]
    encoded = quote(relative, safe="/")
    remote = base + "/" + encoded
    parent = remote.rsplit("/", 1)[0]
    parts = parent[len(base):].strip("/").split("/") if parent.startswith(base) else []
    current = base
    for part in parts:
        current += "/" + quote(part, safe="")
        subprocess.run(["curl", "--fail", "--silent", "--show-error", "-u", auth, "-X", "MKCOL", current], check=False)
    fd, local_path = tempfile.mkstemp(prefix=".feature-package-upload-")
    os.close(fd)
    local = Path(local_path)
    try:
        subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location", "--header", f"JOB-TOKEN: {os.environ['CI_JOB_TOKEN']}", "--output", str(local), item["url"]], check=True)
        subprocess.run(["curl", "--fail", "--silent", "--show-error", "-u", auth, "-T", str(local), remote], check=True)
    finally:
        local.unlink(missing_ok=True)
Path(sys.argv[3]).write_text(json.dumps({"status":"success", "registry":registry, "nextcloud":{"cloud_dir":directory}}, ensure_ascii=False), encoding="utf-8")
PY
