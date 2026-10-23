#!/usr/bin/env python3
"""Hash actual local image inputs; never emit configuration contents."""
import hashlib
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
digest = hashlib.sha256()
files = [root / name for name in ("Dockerfile", "docker-compose.yml", ".dockerignore", ".env")]
files.extend(path for path in (root / "webapp").rglob("*")
             if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"})
for path in sorted(files):
    digest.update(str(path.relative_to(root)).encode() + b"\0")
    if path.exists():
        digest.update(str(path.stat().st_mode & 0o777).encode() + b"\0")
        digest.update(path.read_bytes())
    digest.update(b"\0")
print(digest.hexdigest())
