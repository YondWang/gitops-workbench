#!/usr/bin/env bash
set -euo pipefail
context_path=${1:?context required}; output_dir=${2:?output required}; publish_dir=${3:?publish directory required}
mkdir -p "$publish_dir"
python3 - "$context_path" "$output_dir" "$publish_dir" <<'PY'
import json, os, shutil, subprocess, sys
from pathlib import Path
c, out, dest = json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]), Path(sys.argv[3])
base = f"{os.environ['CI_API_V4_URL']}/projects/{os.environ['GITOPS_FEATURE_SIMOS_PROJECT_ID']}/packages/generic/simos-debs/{c['build_id']}"
files=[]
for path in out.iterdir():
    if not path.is_file(): continue
    subprocess.run(["curl", "--fail", "--silent", "--show-error", "--header", f"JOB-TOKEN: {os.environ['CI_JOB_TOKEN']}", "--upload-file", str(path), base + "/" + path.name], check=True)
    files.append({"name": path.name, "url": base + "/" + path.name})
Path(dest / "registry-result.json").write_text(json.dumps({"project": c["registry"]["project"], "package_version": c["build_id"], "files": files}, ensure_ascii=False), encoding="utf-8")
PY
