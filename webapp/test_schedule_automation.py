from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import server
from repository_store import RepositoryConfig


VERSION_INFO = """Version:3.1.24.019
NextVersion:""
PreVersion:3.1.24.018

simos_commitid:simos-old
business_commitid:business-new
localization_commitid:localization-new
perception_commitid:perception-new
mapengine_commitid:mapengine-new
pnc_commitid:pnc-new

simos_branch:fix
business_branch:fix
localization_branch:fix
perception_branch:fix
mapengine_branch:fix
pnc_branch:fix

Date:2026-07-02 16:00:00
"""


class FakeStore:
    def __init__(self, repos: list[RepositoryConfig]) -> None:
        self.repos = repos

    def list(self) -> list[RepositoryConfig]:
        return self.repos

    def enabled(self) -> list[RepositoryConfig]:
        return [repo for repo in self.repos if repo.enabled]

    def get(self, repo_id: str) -> RepositoryConfig:
        for repo in self.repos:
            if repo.id == repo_id:
                return repo
        raise ValueError(f"repo not found: {repo_id}")


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._branch_names = ["fix", "main"]
        self._tag_names: list[str] = []

    def project(self) -> dict[str, Any]:
        return {"id": "simos"}

    def branch_names(self) -> list[str]:
        self.calls.append(("branch_names",))
        return list(self._branch_names)

    def tag_names(self) -> list[str]:
        self.calls.append(("tag_names",))
        return list(self._tag_names)

    def get_file_text(self, file_path: str, ref: str) -> str:
        self.calls.append(("get_file_text", file_path, ref))
        if file_path == "version.info":
            return VERSION_INFO
        if file_path == "pkg.info":
            return "version:3.1.24.0\n"
        raise server.GitLabError("missing", status=404, payload={})

    def branch(self, name: str) -> dict[str, Any]:
        self.calls.append(("branch", name))
        return {"name": name, "commit": {"id": "simos-new", "short_id": "simos-new", "parent_ids": []}}

    def create_branch(self, branch: str, ref: str) -> dict[str, Any]:
        self.calls.append(("create_branch", branch, ref))
        return {"name": branch, "ref": ref}

    def create_commit(self, branch: str, message: str, actions: list[dict[str, str]]) -> dict[str, Any]:
        self.calls.append(("create_commit", branch, message, actions))
        return {"id": "version-commit", "short_id": "version"}

    def merge_requests(self, source_branch: str, target_branch: str, state: str = "all") -> list[dict[str, Any]]:
        self.calls.append(("merge_requests", source_branch, target_branch, state))
        return []

    def opened_merge_requests(self, source_branch: str, target_branch: str) -> list[dict[str, Any]]:
        self.calls.append(("opened_merge_requests", source_branch, target_branch))
        return []

    def create_merge_request(self, source_branch: str, target_branch: str, title: str) -> dict[str, Any]:
        self.calls.append(("create_merge_request", source_branch, target_branch, title))
        return {"iid": 7, "source_branch": source_branch, "target_branch": target_branch, "title": title, "state": "opened"}

    def create_tag(self, tag_name: str, ref: str, message: str = "") -> dict[str, Any]:
        self.calls.append(("create_tag", tag_name, ref, message))
        self._tag_names.append(tag_name)
        return {"name": tag_name, "target": ref, "message": message}


class ScheduleAutomationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_schedule_path = server.SCHEDULES_PATH
        self.previous_schedule_runs_path = server.SCHEDULE_RUNS_PATH
        self.previous_release_tasks_path = server.RELEASE_TASKS_PATH
        self.previous_release_runs_path = server.RELEASE_RUNS_PATH
        self.previous_version_settings_path = server.VERSION_SETTINGS_PATH
        server.SCHEDULES_PATH = Path(self.tmp.name) / "schedules.json"
        server.SCHEDULE_RUNS_PATH = Path(self.tmp.name) / "schedule-runs.json"
        server.RELEASE_TASKS_PATH = Path(self.tmp.name) / "release_tasks.json"
        server.RELEASE_RUNS_PATH = Path(self.tmp.name) / "release_runs.json"
        server.VERSION_SETTINGS_PATH = Path(self.tmp.name) / "version-settings.json"
        self.repo = RepositoryConfig(
            id="simos",
            name="simos",
            base_url="https://gitlab.example",
            project="OS/simos",
            token_env="SIMOS_TOKEN",
        )
        self.client = FakeClient()
        self.app = server.GitOpsApp(FakeStore([self.repo]), server.AuthManager.from_environment())
        self.app.client_for = lambda repo: self.client  # type: ignore[method-assign]
        self.app.token_loaded = lambda repo: True  # type: ignore[method-assign]

    def tearDown(self) -> None:
        server.SCHEDULES_PATH = self.previous_schedule_path
        server.SCHEDULE_RUNS_PATH = self.previous_schedule_runs_path
        server.RELEASE_TASKS_PATH = self.previous_release_tasks_path
        server.RELEASE_RUNS_PATH = self.previous_release_runs_path
        server.VERSION_SETTINGS_PATH = self.previous_version_settings_path
        self.tmp.cleanup()

    def test_default_schedule_is_seeded(self) -> None:
        result = self.app.schedules()

        self.assertTrue(result["ok"])
        schedule = result["schedules"][0]
        self.assertEqual(schedule["id"], "daily-simos-resident-release")
        self.assertTrue(schedule["enabled"])
        self.assertEqual(schedule["cron"], "0 16 * * *")
        self.assertEqual(schedule["timezone"], "Asia/Shanghai")
        self.assertEqual(schedule["cloud_category"], "车机/CI自动构建")

    def test_dry_run_resolves_tag_without_creating_it(self) -> None:
        result = self.app.schedule_dry_run("daily-simos-resident-release", now="2026-07-03T16:00:00+08:00")

        self.assertTrue(result["ok"])
        self.assertEqual(result["plan"]["version"], "V3.1.25.020")
        self.assertEqual(result["plan"]["version_prefix"], "V")
        self.assertEqual(result["plan"]["ref"], "fix")
        self.assertEqual(result["plan"]["tag_name"], "fix_V3.1.25.020_202607031600")
        self.assertEqual(result["plan"]["cloud_category"], "车机/CI自动构建")
        self.assertNotIn("effective_cloud_category", result["plan"])
        self.assertEqual(result["plan"]["cloud_dir"], "/public/Versions/2026-07-03_V3.1.25.020/车机/CI自动构建")
        self.assertIn("SIMOS_CLOUD_CATEGORY=车机/CI自动构建", result["plan"]["message"])
        self.assertTrue(result["plan"]["requires_weekly_version_confirmation"])
        self.assertFalse(any(call[0] == "create_tag" for call in self.client.calls))

    def test_run_now_waits_for_friday_weekly_version_confirmation(self) -> None:
        result = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-03T16:00:00+08:00")

        self.assertTrue(result["ok"])
        self.assertEqual(result["run"]["status"], "waiting_weekly_version_confirmation")
        self.assertEqual(result["run"]["tag_name"], "fix_V3.1.25.020_202607031600")
        self.assertFalse(any(call[0] == "create_merge_request" for call in self.client.calls))
        self.assertFalse(any(call[0] == "create_tag" for call in self.client.calls))
        runs = self.app.schedule_runs("daily-simos-resident-release")
        self.assertEqual(runs["runs"][0]["tag_name"], "fix_V3.1.25.020_202607031600")

    def test_continue_after_weekly_confirmation_creates_version_mr(self) -> None:
        result = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-03T16:00:00+08:00")
        continued = self.app.continue_release_run(result["run"]["id"])

        self.assertEqual(continued["run"]["status"], "waiting_version_mr")
        self.assertEqual(continued["run"]["tag_name"], "fix_V3.1.25.020_202607031600")
        self.assertTrue(any(call[0] == "create_merge_request" for call in self.client.calls))
        self.assertFalse(any(call[0] == "create_tag" for call in self.client.calls))

    def test_same_week_existing_third_only_bumps_fourth(self) -> None:
        self.client._tag_names.append("fix_V3.1.25.020_202607031600")
        result = self.app.schedule_dry_run("daily-simos-resident-release", now="2026-07-04T16:00:00+08:00")

        self.assertEqual(result["plan"]["version"], "V3.1.25.021")
        self.assertEqual(result["plan"]["tag_name"], "fix_V3.1.25.021_202607041600")
        self.assertFalse(result["plan"].get("requires_weekly_version_confirmation", False))

    def test_schedule_crud_supports_multiple_tasks(self) -> None:
        saved = self.app.save_schedule(
            {
                "id": "evening-simos-resident-release",
                "name": "晚间 resident 自动构建",
                "enabled": False,
                "daily_time": "18:30",
                "default_ref": "main",
                "cloud_category": "车机/CI自动构建",
            }
        )
        self.assertTrue(saved["ok"])

        schedules = self.app.schedules()["schedules"]
        self.assertEqual([item["id"] for item in schedules], ["daily-simos-resident-release", "evening-simos-resident-release"])
        self.assertEqual(schedules[1]["cron"], "30 18 * * *")
        self.assertEqual(schedules[1]["daily_time"], "18:30")
        self.assertFalse(schedules[1]["enabled"])

        deleted = self.app.delete_schedule("evening-simos-resident-release")
        self.assertTrue(deleted["ok"])
        self.assertEqual([item["id"] for item in self.app.schedules()["schedules"]], ["daily-simos-resident-release"])

    def test_custom_cloud_category_is_written_to_tag_message(self) -> None:
        self.app.save_schedule(
            {
                "id": "daily-simos-resident-release",
                "daily_time": "16:00",
                "default_ref": "fix",
                "cloud_category": "车机/夜间构建",
            }
        )
        self.client._tag_names.append("fix_V3.1.25.020_202607031600")
        result = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-04T16:00:00+08:00")
        continued = self.app.continue_release_run(result["run"]["id"])

        self.assertEqual(continued["run"]["cloud_dir"], "/public/Versions/2026-07-04_V3.1.25.021/车机/夜间构建")
        self.assertTrue(any(call[0] == "create_merge_request" for call in self.client.calls))
        payload_message = continued["run"]["create_tag_payload"]["message"]
        self.assertIn("SIMOS_CLOUD_CATEGORY=车机/夜间构建", payload_message)

    def test_deleting_last_schedule_leaves_empty_list(self) -> None:
        deleted = self.app.delete_schedule("daily-simos-resident-release")

        self.assertTrue(deleted["ok"])
        self.assertEqual(self.app.schedules()["schedules"], [])


if __name__ == "__main__":
    unittest.main()
