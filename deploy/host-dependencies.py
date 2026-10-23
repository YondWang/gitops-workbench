#!/usr/bin/env python3
"""Incremental host provisioning for an existing Docker/Runner/Nextcloud server."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import tempfile

CAPABILITIES = {
    "simos-ci-publish-resident": "config-matrix-runtime-switchable-isolated-layout-v2",
    "simos-ci-publish-sim": "sim-package-v1",
}
RULE = "".join(f"gitlab-runner ALL=(root) NOPASSWD: /usr/local/bin/{name} *\n" for name in CAPABILITIES)
RULE_DIGEST = hashlib.sha256(RULE.encode()).hexdigest()


def signature(path):
    s = path.stat()
    return [s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode), s.st_size, s.st_mtime_ns]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", dest="mode", action="store_const", const="--check")
    modes.add_argument("--apply", dest="mode", action="store_const", const="--apply")
    parser.add_argument("--destdir", type=Path)
    parser.add_argument("--publishers", type=Path, default=Path(__file__).parent / "publishers")
    args = parser.parse_args()
    root = args.destdir or Path("/")
    staging = args.destdir is not None
    if staging and (not root.is_absolute() or root == Path("/")):
        raise ValueError("--destdir must be an absolute staging directory, not /")
    def target(name):
        return root / name.lstrip("/")
    state = target("/var/lib/gitops-workbench-deploy/host-state.json")
    rule = target("/etc/sudoers.d/gitops-workbench-publishers")
    expected = {name: hashlib.sha256((args.publishers / name).read_bytes()).hexdigest() for name in CAPABILITIES}
    try:
        receipt = json.loads(state.read_text())
    except (OSError, ValueError):
        receipt = {}
    if not isinstance(receipt, dict):
        receipt = {}
    pending = []
    for name, digest in expected.items():
        path = target(f"/usr/local/bin/{name}")
        if (not path.is_file() or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
                or stat.S_IMODE(path.stat().st_mode) != 0o755
                or (not staging and (path.stat().st_uid != 0 or path.stat().st_gid != 0))):
            pending.append(name)
    rule_ok = False
    if rule.is_file() and not rule.is_symlink():
        if os.access(rule, os.R_OK):
            rule_ok = rule.read_text() == RULE
        else:
            rule_ok = (receipt.get("rule_signature") == signature(rule)
                       and receipt.get("rule_sha256") == RULE_DIGEST)
        rule_ok = rule_ok and stat.S_IMODE(rule.stat().st_mode) == 0o440
        if not staging:
            rule_ok = rule_ok and rule.stat().st_uid == 0 and rule.stat().st_gid == 0
    missing_packages = []
    if not staging:
        for cmd, package in (("curl", "curl"), ("sudo", "sudo"), ("visudo", "sudo")):
            if not shutil.which(cmd):
                missing_packages.append(package)
        for cmd in ("docker", "bash"):
            if not shutil.which(cmd):
                raise RuntimeError(f"existing server prerequisite missing: {cmd}")
        runner = pwd.getpwnam("gitlab-runner")
    directories = {"/data/simos-ci/incoming": 0o750,
                   "/data/simos-ci/artifacts": 0o755, "/data/simos-ci/sim-test-reports": 0o755}
    missing_dirs = [name for name in directories if not target(name).is_dir()]
    if target("/data/simos-ci/incoming").is_dir():
        incoming = target("/data/simos-ci/incoming").stat()
        if (stat.S_IMODE(incoming.st_mode) != 0o750
                or (not staging and (incoming.st_uid != runner.pw_uid or incoming.st_gid != runner.pw_gid))):
            missing_dirs.append("/data/simos-ci/incoming")
    receipt_ok = (receipt.get("schema") == 1 and receipt.get("publishers") == expected
                  and receipt.get("rule_sha256") == RULE_DIGEST
                  and (staging or (state.is_file() and state.stat().st_uid == 0
                                    and not state.stat().st_mode & 0o022)))
    changed = bool(pending or not rule_ok or missing_dirs or missing_packages or not receipt_ok)
    if args.mode == "--check":
        if pending:
            print("publishers to install/update: " + ", ".join(pending))
        if not rule_ok:
            print("publisher sudo rules: install/update required")
        if missing_dirs:
            print("directories to prepare: " + ", ".join(missing_dirs))
        if missing_packages:
            print("packages to install: " + ", ".join(sorted(set(missing_packages))))
        print("host dependencies: " + ("update required" if changed else "current; skip installation"))
        return 10 if changed else 0
    if not changed:
        print("host dependencies: current; skip installation")
        return 0
    if not staging and os.geteuid() != 0:
        raise RuntimeError("host dependency changes require sudo")
    if missing_packages:
        subprocess.run(["apt-get", "update"], check=True)
        subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", *sorted(set(missing_packages))], check=True)
    # Validate source and sudo rules before changing installed files.
    for name, capability in CAPABILITIES.items():
        source = args.publishers / name
        subprocess.run(["bash", "-n", str(source)], check=True)
        subprocess.run(["bash", str(source), "--capability", capability], check=True, stdout=subprocess.DEVNULL)
    rule.parent.mkdir(parents=True, exist_ok=True)
    def atomic_file(path, data, mode):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".gitops-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.chmod(temp, mode)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    if not rule_ok:
        if not staging:
            with tempfile.NamedTemporaryFile(mode="w") as temp:
                temp.write(RULE)
                temp.flush()
                subprocess.run(["visudo", "-cf", temp.name], check=True)
        atomic_file(rule, RULE.encode(), 0o440)
        print("updated: sudo rules for publishers")
    for name in pending:
        atomic_file(target(f"/usr/local/bin/{name}"), (args.publishers / name).read_bytes(), 0o755)
        print(f"installed/updated: {name}")
    for name in missing_dirs:
        path = target(name)
        path.mkdir(parents=True, exist_ok=True, mode=directories[name])
        if name == "/data/simos-ci/incoming":
            if not staging:
                os.chown(path, runner.pw_uid, runner.pw_gid)
            path.chmod(0o750)
    if not staging:
        for name, capability in CAPABILITIES.items():
            subprocess.run(["sudo", "-u", "gitlab-runner", "sudo", "-n", f"/usr/local/bin/{name}",
                            "--capability", capability], check=True)
    atomic_file(state, json.dumps({"schema": 1, "publishers": expected,
                                 "rule_sha256": RULE_DIGEST,
                                 "rule_signature": signature(rule)}, indent=2).encode(), 0o644)
    print("host dependencies: ready")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"host dependencies: ERROR: {exc}", file=os.sys.stderr)
        raise SystemExit(1)
