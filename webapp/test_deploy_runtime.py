from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "deploy/host-dependencies.py"
BUILD = ROOT / "deploy/build-input-digest.py"


class DeployRuntimeTest(unittest.TestCase):
    def run_host(self, root: Path, mode: str, source: Path | None = None):
        return subprocess.run(["python3", str(HOST), mode, "--destdir", str(root),
                               "--publishers", str(source or ROOT / "deploy/publishers")],
                              text=True, capture_output=True)

    def test_install_skip_and_update_only_changed_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "host"
            source = Path(temp) / "publishers"
            shutil.copytree(ROOT / "deploy/publishers", source)
            self.assertEqual(self.run_host(root, "--check", source).returncode, 10)
            installed = self.run_host(root, "--apply", source)
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            sim = root / "usr/local/bin/simos-ci-publish-sim"
            resident = root / "usr/local/bin/simos-ci-publish-resident"
            stamp = sim.stat().st_mtime_ns
            resident_stamp = resident.stat().st_mtime_ns
            self.assertEqual(self.run_host(root, "--check", source).returncode, 0)
            self.assertEqual(self.run_host(root, "--apply", source).returncode, 0)
            self.assertEqual(sim.stat().st_mtime_ns, stamp)
            with (source / sim.name).open("a") as stream:
                stream.write("\n# updated release\n")
            self.assertEqual(self.run_host(root, "--check", source).returncode, 10)
            self.assertEqual(self.run_host(root, "--apply", source).returncode, 0)
            self.assertEqual(sim.read_bytes(), (source / sim.name).read_bytes())
            self.assertEqual(resident.stat().st_mtime_ns, resident_stamp)

    def test_permission_and_sudoers_drift_are_repaired_preserving_existing_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "etc/sudoers.d/gitlab-runner"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("existing resident rule\n")
            self.assertEqual(self.run_host(root, "--apply").returncode, 0)
            helper = root / "usr/local/bin/simos-ci-publish-sim"
            helper.chmod(0o777)
            self.assertEqual(self.run_host(root, "--check").returncode, 10)
            self.assertEqual(self.run_host(root, "--apply").returncode, 0)
            self.assertEqual(helper.stat().st_mode & 0o777, 0o755)
            rule = root / "etc/sudoers.d/gitops-workbench-publishers"
            original = rule.read_text()
            rule.chmod(0o640)
            rule.write_text(original.replace("NOPASSWD", "PASSWDXX"))
            self.assertEqual(self.run_host(root, "--check").returncode, 10)
            self.assertEqual(self.run_host(root, "--apply").returncode, 0)
            self.assertEqual(rule.read_text(), original)
            self.assertEqual(rule.stat().st_mode & 0o777, 0o440)
            self.assertEqual(legacy.read_text(), "existing resident rule\n")

    def test_missing_receipt_never_counts_as_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(self.run_host(root, "--apply").returncode, 0)
            (root / "var/lib/gitops-workbench-deploy/host-state.json").unlink()
            self.assertEqual(self.run_host(root, "--check").returncode, 10)

    def test_incoming_without_directory_access_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(self.run_host(root, "--apply").returncode, 0)
            incoming = root / "data/simos-ci/incoming"
            incoming.chmod(0o600)
            try:
                self.assertEqual(self.run_host(root, "--check").returncode, 10)
                repaired = self.run_host(root, "--apply")
                self.assertEqual(repaired.returncode, 0, repaired.stderr)
                self.assertEqual(incoming.stat().st_mode & 0o777, 0o750)
                self.assertEqual(self.run_host(root, "--check").returncode, 0)
            finally:
                incoming.chmod(0o750)

    def test_build_digest_tracks_inputs_not_deployment_files_or_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("webapp", "deploy"):
                (root / name).mkdir()
            for name in ("Dockerfile", "docker-compose.yml", ".dockerignore", ".env", "webapp/server.py"):
                (root / name).write_text(name)
            def digest():
                result = subprocess.run(["python3", str(BUILD), str(root)], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()
            initial = digest()
            os.utime(root / "webapp/server.py", (1, 1))
            (root / "deploy/helper").write_text("new deployment asset")
            self.assertEqual(digest(), initial)
            for name in ("webapp/server.py", ".env", "Dockerfile", "docker-compose.yml", ".dockerignore"):
                before = digest()
                with (root / name).open("a") as stream:
                    stream.write("changed")
                self.assertNotEqual(digest(), before, name)
            before = digest()
            (root / "webapp/server.py").unlink()
            self.assertNotEqual(digest(), before)

    def test_build_skips_unchanged_inputs_rebuilds_missing_image_and_retries_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / "deploy", root / "deploy")
            (root / "webapp").mkdir()
            (root / "webapp/server.py").write_text("version one")
            (root / "Dockerfile").write_text("FROM test")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
root=pathlib.Path(os.environ["FAKE_DOCKER_ROOT"])
if sys.argv[1:3] == ["image", "inspect"]:
    image=root/"image"
    if not image.exists(): sys.exit(1)
    print(image.read_text())
elif sys.argv[1:] == ["compose", "build"]:
    with (root/"build-log").open("a") as stream: stream.write("build\\n")
    if os.environ.get("FAIL_BUILD") == "true": sys.exit(42)
    (root/"image").write_text("image-"+str((root/"build-log").read_text().count("build")))
else: sys.exit(99)
''')
            docker.chmod(0o755)
            env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_DOCKER_ROOT": str(root)}
            def run(**extra):
                return subprocess.run(["bash", str(root / "deploy/build-app.sh")],
                                      env={**env, **extra}, text=True, capture_output=True)
            first = run()
            self.assertEqual(first.returncode, 0, first.stderr)
            receipt = root / ".deploy-state/build-input.sha256"
            first_receipt = receipt.read_text()
            self.assertIn("skip build", run().stdout)
            self.assertEqual((root / "build-log").read_text(), "build\n")
            (root / "webapp/server.py").write_text("version two")
            self.assertEqual(run(FAIL_BUILD="true").returncode, 42)
            self.assertEqual(receipt.read_text(), first_receipt)
            self.assertEqual(run().returncode, 0)
            self.assertEqual((root / "build-log").read_text().count("build"), 3)
            (root / "image").unlink()
            self.assertEqual(run().returncode, 0)
            self.assertEqual((root / "build-log").read_text().count("build"), 4)
            self.assertEqual(run(DEPLOY_FORCE_BUILD="true").returncode, 0)
            self.assertEqual((root / "build-log").read_text().count("build"), 5)

    def test_deploy_installs_dependencies_once_and_stops_on_setup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            ssh = bin_dir / "ssh"
            ssh.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
root=pathlib.Path(os.environ["FAKE_DEPLOY_ROOT"])
command=sys.argv[-1]
with (root/"ssh-log").open("a") as log: log.write(command+"\\n")
if "setup-host.sh" in command:
    if "--check" in command: sys.exit(0 if (root/"ready").exists() else 10)
    if os.environ.get("FAIL_SETUP") == "true": sys.exit(42)
    (root/"ready").touch()
    with (root/"setup-count").open("a") as log: log.write("setup\\n")
sys.exit(0)
''')
            ssh.chmod(0o755)
            rsync = bin_dir / "rsync"
            rsync.write_text("#!/bin/sh\nexit 0\n")
            rsync.chmod(0o755)
            env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                   "FAKE_DEPLOY_ROOT": str(root), "DEPLOY_INTERACTIVE_SUDO": "false"}
            def deploy(**extra):
                return subprocess.run(["bash", str(ROOT / "deploy/deploy-to-server.sh")],
                                      env={**env, **extra}, text=True, capture_output=True)
            first = deploy()
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertIn("Deployment finished", first.stdout)
            self.assertEqual(deploy().returncode, 0)
            self.assertEqual((root / "setup-count").read_text(), "setup\n")
            (root / "ready").unlink()
            (root / "ssh-log").write_text("")
            failed = deploy(FAIL_SETUP="true")
            self.assertNotEqual(failed.returncode, 0)
            self.assertNotIn("Deployment finished", failed.stdout)
            self.assertNotIn("docker compose up", (root / "ssh-log").read_text())


if __name__ == "__main__":
    unittest.main()
