#!/usr/bin/env bash
set -euo pipefail
context_path=${1:?context JSON required}
workspace=${2:?workspace required}
rm -rf "$workspace"
python3 - "$context_path" "$workspace" <<'PY'
import json, os, subprocess, sys
from pathlib import Path
from urllib.parse import quote

context = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = Path(sys.argv[2])
source = context["source"]
server_url = os.environ.get("CI_SERVER_URL", "").rstrip("/")
if not server_url:
    raise SystemExit("CI_SERVER_URL is required to clone the frozen Feature source")
server_host = os.environ.get("CI_SERVER_HOST", "").strip()
if not server_host:
    raise SystemExit("CI_SERVER_HOST is required to rewrite Feature submodule URLs")
job_token = quote(os.environ["CI_JOB_TOKEN"], safe="")
base = f"{server_url.split('://', 1)[0]}://gitlab-ci-token:{job_token}@{server_url.split('://', 1)[1]}/"
def run(*args, cwd=None): subprocess.run(args, cwd=cwd, check=True)

# SimOS records SSH submodules, while this isolated K8s Job must fetch them
# over the GitLab HTTPS endpoint. CI_SERVER_URL retains the non-default :9900
# port; CI_SERVER_HOST alone does not.
for old_url in (
    f"https://{server_host}/",
    f"http://{server_host}/",
    f"ssh://git@{server_host}:22222/",
    f"git@{server_host}:",
):
    run("git", "config", "--global", "--add", f"url.{base}.insteadOf", old_url)
run("git", "clone", "--no-checkout", base + source["project"] + ".git", str(root))
run("git", "checkout", "--detach", source["sha"], cwd=root)
if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip() != source["sha"]:
    raise SystemExit("simos SHA mismatch after checkout")
# Populate unselected submodules from the exact SimOS gitlinks. Selected components
# below are then replaced by their signed Feature/baseline snapshot.
run("git", "submodule", "update", "--init", "--recursive", cwd=root)
for component in context["components"]:
    if component["repo"] == source["repository_id"]:
        continue
    path = component.get("submodule_path") or "src/" + component["repo"]
    target = root / path
    if not (target / ".git").exists():
        raise SystemExit("missing initialized submodule: " + path)
    run("git", "fetch", "--depth", "1", "origin", component["sha"], cwd=target)
    run("git", "checkout", "--detach", component["sha"], cwd=target)
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target, text=True).strip() != component["sha"]:
        raise SystemExit("component SHA mismatch: " + component["repo"])
    run("git", "add", "-f", path, cwd=root)
    run("git", "update-index", "--cacheinfo", "160000," + component["sha"] + "," + path, cwd=root)
(root / "version.info").write_text(context["metadata"]["version_info"], encoding="utf-8")
(root / "software.yaml").write_text(context["metadata"]["software_yaml"], encoding="utf-8")
PY
