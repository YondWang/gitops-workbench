from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import server
from repository_store import RepositoryConfig


class EmptyStore:
    def list(self):
        return []

    def enabled(self):
        return []


class FakeStore:
    def __init__(self, repos: list[RepositoryConfig]) -> None:
        self.repos = repos

    def list(self) -> list[RepositoryConfig]:
        return self.repos

    def enabled(self) -> list[RepositoryConfig]:
        return [repo for repo in self.repos if repo.enabled]


class FakeArtifactClient:
    def __init__(self) -> None:
        self.config = server.GitLabConfig(
            base_url="https://gitlab.example",
            project="OS/simos",
            token="token",
        )
        self._pipelines: list[dict[str, Any]] = []
        self._jobs: dict[int, list[dict[str, Any]]] = {}
        self._artifact_texts: dict[tuple[int, str], str] = {}

    def pipelines(self, ref: str = "", status: str = "", source: str = "") -> list[dict[str, Any]]:
        result = list(self._pipelines)
        if ref:
            result = [item for item in result if item.get("ref") == ref]
        if status:
            result = [item for item in result if item.get("status") == status]
        if source:
            result = [item for item in result if item.get("source") == source]
        return result

    def pipeline_jobs(self, pipeline_id: int | str) -> list[dict[str, Any]]:
        return list(self._jobs.get(int(pipeline_id), []))

    def job_artifact_file_url(self, job_id: int | str, artifact_path: str) -> str:
        return f"https://gitlab.example/api/v4/projects/OS%2Fsimos/jobs/{job_id}/artifacts/{artifact_path}"

    def job_artifact_file_text(self, job_id: int | str, artifact_path: str) -> str:
        key = (int(job_id), artifact_path)
        if key not in self._artifact_texts:
            raise server.GitLabError("missing", status=404, payload={})
        return self._artifact_texts[key]


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
        result = self.app.resident_package("fix_3.1.22.046_202606241430")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "pending_or_missing")
        self.assertEqual(result["tag"], "fix_3.1.22.046_202606241430")
        self.assertTrue(result["artifact_path"].endswith("/fix_3.1.22.046_202606241430/resident.tar.gz"))

    def test_existing_build_info_returns_ready_package(self) -> None:
        artifact_dir = server.RESIDENT_ARTIFACT_ROOT / "release_3.1.22.046_202606241430"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "build-info.json").write_text(
            json.dumps(
                {
                    "tag": "release_3.1.22.046_202606241430",
                    "status": "success",
                    "sha256": "abc123",
                    "built_at": "2026-06-22T15:09:15+08:00",
                }
            ),
            encoding="utf-8",
        )

        result = self.app.resident_package("release_3.1.22.046_202606241430")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["sha256"], "abc123")
        self.assertTrue(result["artifact_dir"].endswith("/release_3.1.22.046_202606241430"))
        self.assertTrue(result["artifact_path"].endswith("/release_3.1.22.046_202606241430/resident.tar.gz"))

    def test_existing_manifest_adds_cloud_package_details(self) -> None:
        artifact_dir = server.RESIDENT_ARTIFACT_ROOT / "release_3.1.24.019_202607031600"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "build-info.json").write_text(
            json.dumps(
                {
                    "tag": "release_3.1.24.019_202607031600",
                    "status": "success",
                    "sha256": "root-sha",
                    "built_at": "2026-07-03T16:45:00+08:00",
                }
            ),
            encoding="utf-8",
        )
        (artifact_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "cloud_dir": "/public/Versions/2026-07-03_3.1.24.019/车机/CI自动构建",
                    "packages": {
                        "dev": {
                            "prefix": "Vd",
                            "path": "Vd-3.1.24.019/resident.tar.gz",
                            "sha256": "dev-sha",
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.app.resident_package("release_3.1.24.019_202607031600")

        self.assertTrue(result["ok"])
        self.assertEqual(result["artifact_source"], "cloud")
        self.assertEqual(result["cloud_dir"], "/public/Versions/2026-07-03_3.1.24.019/车机/CI自动构建")
        self.assertEqual(result["artifact_path"], "/public/Versions/2026-07-03_3.1.24.019/车机/CI自动构建")
        self.assertEqual(result["packages"]["dev"]["sha256"], "dev-sha")

    def test_source_branch_suffix_tag_is_accepted(self) -> None:
        artifact_dir = server.RESIDENT_ARTIFACT_ROOT / "fix-3.2.0.0-rc1_3.2.0.0_202606261528"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "build-info.json").write_text(
            json.dumps(
                {
                    "tag": "fix-3.2.0.0-rc1_3.2.0.0_202606261528",
                    "status": "success",
                    "sha256": "def456",
                    "built_at": "2026-06-26T15:28:00+08:00",
                }
            ),
            encoding="utf-8",
        )

        result = self.app.resident_package("fix-3.2.0.0-rc1_3.2.0.0_202606261528")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["sha256"], "def456")

    def test_invalid_tag_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "resident 自动构建范围"):
            self.app.resident_package("feature-test")

    def test_legacy_day_stamp_tag_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "resident 自动构建范围"):
            self.app.resident_package("release_3.1.22.046_20260624")


class GitLabArtifactResidentPackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_root = server.RESIDENT_ARTIFACT_ROOT
        self.tmp = tempfile.TemporaryDirectory()
        server.RESIDENT_ARTIFACT_ROOT = Path(self.tmp.name)
        self.repo = RepositoryConfig(
            id="simos",
            name="simos",
            base_url="https://gitlab.example",
            project="OS/simos",
            token_env="SIMOS_TOKEN",
        )
        self.client = FakeArtifactClient()
        self.app = server.GitOpsApp(FakeStore([self.repo]), server.AuthManager.from_environment())
        self.app.client_for = lambda repo: self.client  # type: ignore[method-assign]
        self.app.token_loaded = lambda repo: True  # type: ignore[method-assign]

    def tearDown(self) -> None:
        server.RESIDENT_ARTIFACT_ROOT = self.previous_root
        self.tmp.cleanup()

    def test_gitlab_artifact_success_is_returned_when_local_build_info_missing(self) -> None:
        tag = "release_3.1.22.046_202606241430"
        self.client._pipelines = [
            {
                "id": 11,
                "ref": tag,
                "status": "success",
                "source": "push",
                "web_url": "https://gitlab.example/OS/simos/-/pipelines/11",
                "created_at": "2026-06-26T14:30:00+08:00",
            }
        ]
        self.client._jobs = {
            11: [
                {
                    "id": 99,
                    "name": "resident",
                    "status": "success",
                    "web_url": "https://gitlab.example/OS/simos/-/jobs/99",
                    "artifacts_file": {"filename": "artifacts.zip"},
                    "finished_at": "2026-06-26T14:32:00+08:00",
                }
            ]
        }
        self.client._artifact_texts[(99, "build-info.json")] = json.dumps(
            {
                "status": "success",
                "sha256": "abc123",
                "built_at": "2026-06-26T14:32:00+08:00",
            }
        )

        result = self.app.resident_package(tag)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["artifact_source"], "gitlab")
        self.assertEqual(result["sha256"], "abc123")
        self.assertEqual(result["pipeline_url"], "https://gitlab.example/OS/simos/-/pipelines/11")
        self.assertEqual(result["job_url"], "https://gitlab.example/OS/simos/-/jobs/99")
        self.assertTrue(result["artifact_url"].endswith("/jobs/99/artifacts/resident.tar.gz"))

    def test_gitlab_artifact_job_success_waits_for_the_active_pipeline(self) -> None:
        tag = "fix_3.1.27.039_202607221616"
        self.client._pipelines = [
            {
                "id": 16,
                "ref": tag,
                "status": "running",
                "source": "push",
                "web_url": "https://gitlab.example/OS/simos/-/pipelines/16",
            }
        ]
        self.client._jobs = {
            16: [
                {
                    "id": 106,
                    "name": "resident",
                    "status": "success",
                    "stage": "package",
                    "web_url": "https://gitlab.example/OS/simos/-/jobs/106",
                    "artifacts_file": {"filename": "artifacts.zip"},
                },
                {
                    "id": 107,
                    "name": "publish",
                    "status": "running",
                    "stage": "publish",
                    "artifacts_file": {},
                },
            ]
        }

        result = self.app.resident_package(tag)

        self.assertEqual(result["status"], "pending_or_missing")
        self.assertEqual(result["pipeline"]["status"], "running")
        self.assertEqual(result["job"]["id"], 106)
        self.assertTrue(result["progress"]["active"])
        self.assertIn("等待 Tag Pipeline 全部完成", result["message"])

    def test_gitlab_pipeline_success_without_resident_artifact_reports_configuration_error(self) -> None:
        tag = "fix_3.1.22.046_202606241430"
        self.client._pipelines = [
            {
                "id": 12,
                "ref": tag,
                "status": "success",
                "source": "push",
                "web_url": "https://gitlab.example/OS/simos/-/pipelines/12",
            }
        ]
        self.client._jobs = {
            12: [
                {
                    "id": 100,
                    "name": "resident",
                    "status": "success",
                    "web_url": "https://gitlab.example/OS/simos/-/jobs/100",
                    "artifacts_file": {},
                }
            ]
        }

        result = self.app.resident_package(tag)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("artifacts.paths", result["message"])
        self.assertIn("resident.tar.gz", result["message"])
        self.assertEqual(result["pipeline_url"], "https://gitlab.example/OS/simos/-/pipelines/12")

    def test_gitlab_running_pipeline_exposes_job_progress_before_artifact_exists(self) -> None:
        tag = "fix_3.1.22.046_202606241430"
        self.client._pipelines = [
            {
                "id": 13,
                "ref": tag,
                "status": "running",
                "source": "push",
                "web_url": "https://gitlab.example/OS/simos/-/pipelines/13",
                "created_at": "2026-06-26T14:30:00+08:00",
                "updated_at": "2026-06-26T14:32:00+08:00",
            }
        ]
        self.client._jobs = {
            13: [
                {
                    "id": 101,
                    "name": "prepare",
                    "stage": "prepare",
                    "status": "success",
                    "web_url": "https://gitlab.example/OS/simos/-/jobs/101",
                    "started_at": "2026-06-26T14:30:00+08:00",
                    "finished_at": "2026-06-26T14:31:00+08:00",
                    "duration": 60,
                },
                {
                    "id": 102,
                    "name": "resident",
                    "stage": "package",
                    "status": "running",
                    "web_url": "https://gitlab.example/OS/simos/-/jobs/102",
                    "started_at": "2026-06-26T14:31:00+08:00",
                    "artifacts_file": {},
                },
            ]
        }

        result = self.app.resident_package(tag)

        self.assertEqual(result["pipeline"]["id"], 13)
        self.assertEqual(result["progress"], {"total": 2, "completed": 1, "running": 1, "pending": 0, "failed": 0, "active": True})
        self.assertEqual([job["id"] for job in result["jobs"]], [101, 102])
        resident_job = next(job for job in result["jobs"] if job["id"] == 102)
        self.assertEqual(resident_job["status"], "running")
        self.assertFalse(resident_job["artifacts_available"])

    def test_gitlab_manual_or_skipped_resident_job_is_terminal_not_pending(self) -> None:
        tag = "fix_3.1.22.046_202606241430"
        self.client._pipelines = [{"id": 14, "ref": tag, "status": "success", "source": "push"}]
        self.client._jobs = {
            14: [
                {"id": 103, "name": "resident", "status": "manual", "stage": "package", "artifacts_file": {}},
                {"id": 104, "name": "resident-check", "status": "skipped", "stage": "package", "artifacts_file": {}},
            ]
        }

        result = self.app.resident_package(tag)

        self.assertEqual(result["status"], "manual_action_required")
        self.assertTrue(result["terminal"])
        self.assertTrue(result["operator_action_required"])
        self.assertFalse(result["progress"]["active"])
        self.assertNotEqual(result["status"], "pending_or_missing")

    def test_gitlab_manual_pipeline_with_no_jobs_is_terminal_not_pending(self) -> None:
        tag = "fix_3.1.22.046_202606241430"
        self.client._pipelines = [{"id": 15, "ref": tag, "status": "manual", "source": "push"}]
        self.client._jobs = {15: []}

        result = self.app.resident_package(tag)

        self.assertEqual(result["status"], "manual_action_required")
        self.assertTrue(result["terminal"])
        self.assertTrue(result["operator_action_required"])
        self.assertEqual(result["pipeline"]["id"], 15)
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["progress"]["total"], 0)

    def test_gitlab_job_summary_emits_duration_seconds(self) -> None:
        summary = self.app.resident_job_summary({"id": 105, "name": "resident", "status": "success", "duration": 61.5})

        self.assertEqual(summary["duration_seconds"], 61.5)


if __name__ == "__main__":
    unittest.main()
