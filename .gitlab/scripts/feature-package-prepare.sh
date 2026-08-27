#!/usr/bin/env bash
set -euo pipefail
context_path=${1:?context JSON required}
workspace=${2:?workspace required}
rm -rf "$workspace"
python3 - "$context_path" "$workspace" <<'PY'
import json, subprocess, sys
from pathlib import Path

context = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = Path(sys.argv[2])
source = context["source"]
base = ("%s://gitlab-ci-token:%s@%s/" % (
    __import__("os").environ.get("CI_SERVER_PROTOCOL", "https"),
    __import__("os").environ["CI_JOB_TOKEN"],
    __import__("os").environ["CI_SERVER_HOST"],
))
def run(*args, cwd=None): subprocess.run(args, cwd=cwd, check=True)
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
