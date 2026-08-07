from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class DeploymentFilesTest(unittest.TestCase):
    def test_docker_build_uses_configurable_debian_mirrors_with_timeouts(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("ARG DEBIAN_APT_MIRROR=https://mirrors.aliyun.com/debian", dockerfile)
        self.assertIn("ARG DEBIAN_APT_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security", dockerfile)
        self.assertIn("Acquire::http::Timeout=30", dockerfile)
        self.assertIn("Acquire::https::Timeout=30", dockerfile)
        self.assertIn("Acquire::Retries=3", dockerfile)
        self.assertIn("DEBIAN_APT_MIRROR", compose)
        self.assertIn("DEBIAN_APT_SECURITY_MIRROR", compose)

