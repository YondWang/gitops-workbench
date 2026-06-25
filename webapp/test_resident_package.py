from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import server


class EmptyStore:
    def list(self):
        return []


class ResidentPackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_root = server.RESIDENT_ARTIFACT_ROOT
        self.tmp = tempfile.TemporaryDirectory()
        server.RESIDENT_ARTIFACT_ROOT = Path(self.tmp.name)
        self.app = server.GitOpsApp(EmptyStore(), server.AuthManager.from_environment())

    def tearDown(self) -> None:
        server.RESIDENT_ARTIFACT_ROOT = self.previous_root
        self.tmp.cleanup()

    def test_missing_package_returns_pending_or_missing(self) -> None:
        result = self.app.resident_package("fix_3.1.22.046_20260624")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "pending_or_missing")
        self.assertEqual(result["tag"], "fix_3.1.22.046_20260624")
        self.assertTrue(result["artifact_path"].endswith("/fix_3.1.22.046_20260624/resident.tar.gz"))

    def test_existing_build_info_returns_ready_package(self) -> None:
        artifact_dir = server.RESIDENT_ARTIFACT_ROOT / "release_3.1.22.046_20260624"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "build-info.json").write_text(
            json.dumps(
                {
                    "tag": "release_3.1.22.046_20260624",
                    "status": "success",
                    "sha256": "abc123",
                    "built_at": "2026-06-22T15:09:15+08:00",
                }
            ),
            encoding="utf-8",
        )

        result = self.app.resident_package("release_3.1.22.046_20260624")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["sha256"], "abc123")
        self.assertTrue(result["artifact_dir"].endswith("/release_3.1.22.046_20260624"))
        self.assertTrue(result["artifact_path"].endswith("/release_3.1.22.046_20260624/resident.tar.gz"))

    def test_invalid_tag_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "resident 自动构建范围"):
            self.app.resident_package("feature-test")


if __name__ == "__main__":
    unittest.main()