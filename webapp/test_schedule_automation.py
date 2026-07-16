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
    def __init__(self, repo_id: str = "simos") -> None:
        self.repo_id = repo_id
        self.calls: list[tuple[str, Any]] = []
        self._branch_names = ["fix", "fix_360", "main"] if repo_id == "simos" else ["fix", "main"]
        self._tag_names: list[str] = []
        self._merge_requests: list[dict[str, Any]] = []
        self.branch_commit = {"id": f"{repo_id}-new", "short_id": f"{repo_id}-new", "parent_ids": []}

    def project(self) -> dict[str, Any]:
        return {"id": self.repo_id}

    def branch_names(self) -> list[str]:
        self.calls.append(("branch_names",))
        return list(self._branch_names)

    def tags(self, search: str = "") -> list[dict[str, Any]]:
        self.calls.append(("tags", search))
        return [
            {"name": name, "commit": {"id": f"{self.repo_id}-{name}-commit", "short_id": name[:8]}}
            for name in self._tag_names
            if not search or search in name
        ]

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
        return {"name": name, "commit": self.branch_commit}

    def create_branch(self, branch: str, ref: str) -> dict[str, Any]:
        self.calls.append(("create_branch", branch, ref))
        return {"name": branch, "ref": ref}

    def create_commit(self, branch: str, message: str, actions: list[dict[str, str]]) -> dict[str, Any]:
        self.calls.append(("create_commit", branch, message, actions))
        return {"id": "version-commit", "short_id": "version"}

    def merge_requests(self, source_branch: str, target_branch: str, state: str = "all") -> list[dict[str, Any]]:
        self.calls.append(("merge_requests", source_branch, target_branch, state))
        return [
            item
            for item in self._merge_requests
            if item["source_branch"] == source_branch
            and item["target_branch"] == target_branch
            and (state == "all" or item.get("state") == state)
        ]

    def opened_merge_requests(self, source_branch: str, target_branch: str) -> list[dict[str, Any]]:
        self.calls.append(("opened_merge_requests", source_branch, target_branch))
        return [
            item
            for item in self._merge_requests
            if item["source_branch"] == source_branch
            and item["target_branch"] == target_branch
            and item.get("state") == "opened"
        ]

    def create_merge_request(self, source_branch: str, target_branch: str, title: str) -> dict[str, Any]:
        self.calls.append(("create_merge_request", source_branch, target_branch, title))
        existing = self.opened_merge_requests(source_branch, target_branch)
        if existing:
            return existing[0]
        item = {"iid": 7, "source_branch": source_branch, "target_branch": target_branch, "title": title, "state": "opened"}
        self._merge_requests.append(item)
        return item

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
        self.assertEqual(
            [(item["config_ref"], item["label"], item["enabled"]) for item in schedule["config_matrix"]],
            [("SIMBOT_R6_A", "360", True), ("SIMBOT_R6_B", "360s", True)],
        )

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
        self.assertIn("SIMOS_CONFIG_MATRIX=SIMBOT_R6_A:360,SIMBOT_R6_B:360s", result["plan"]["message"])
        self.assertTrue(result["plan"]["requires_weekly_version_confirmation"])
        self.assertFalse(any(call[0] == "create_tag" for call in self.client.calls))

    def test_run_now_waits_for_friday_weekly_version_confirmation(self) -> None:
        result = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-03T16:00:00+08:00")

        self.assertTrue(result["ok"])
        self.assertEqual(result["run"]["status"], "waiting_weekly_version_confirmation")
        self.assertFalse(any(call[0] == "create_merge_request" for call in self.client.calls))
        self.assertFalse(any(call[0] == "create_tag" for call in self.client.calls))
        runs = self.app.schedule_runs("daily-simos-resident-release")
        self.assertEqual(runs["runs"][0]["tag_name"], "fix_V3.1.25.020_202607031600")

    def test_continue_after_weekly_confirmation_creates_version_mr(self) -> None:
        self.app.save_schedule({"id": "daily-simos-resident-release", "config_ref": "SIMBOT_R6_B"})
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
                "config_ref": "SIMBOT_R6_B",
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
                "config_ref": "SIMBOT_R6_B",
            }
        )
        self.client._tag_names.append("fix_V3.1.25.020_202607031600")
        result = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-04T16:00:00+08:00")
        continued = self.app.continue_release_run(result["run"]["id"])

        self.assertEqual(continued["run"]["cloud_dir"], "/public/Versions/2026-07-04_V3.1.25.021/车机/夜间构建")
        self.assertTrue(any(call[0] == "create_merge_request" for call in self.client.calls))
        payload_message = continued["run"]["create_tag_payload"]["message"]
        self.assertIn("SIMOS_CLOUD_CATEGORY=车机/夜间构建", payload_message)


    def test_config_ref_is_written_to_tag_message_and_code_repos_use_same_ref(self) -> None:
        business_repo = RepositoryConfig(
            id="business",
            name="business",
            base_url="https://gitlab.example",
            project="OS/business",
            token_env="BUSINESS_TOKEN",
        )
        simos_client = FakeClient("simos")
        business_client = FakeClient("business")
        self.app.store = FakeStore([self.repo, business_repo])  # type: ignore[assignment]
        self.app.client_for = lambda repo: simos_client if repo.id == "simos" else business_client  # type: ignore[method-assign]

        self.app.save_schedule(
            {
                "id": "daily-simos-resident-release",
                "daily_time": "16:00",
                "default_ref": "fix",
                "config_ref": "SIMBOT_R6_B",
                "cloud_category": "车机/CI自动构建/360",
            }
        )
        simos_client._tag_names.append("fix_V3.1.25.020_202607031600")
        result = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-04T16:00:00+08:00")
        continued = self.app.continue_release_run(result["run"]["id"])
        self.assertEqual(continued["run"]["status"], "waiting_version_mr")
        simos_client._merge_requests[0]["state"] = "merged"
        simos_client.branch_commit = {"id": "version-commit", "short_id": "version", "parent_ids": ["simos-new"]}
        tagged = self.app.continue_release_run(result["run"]["id"])

        self.assertEqual(tagged["run"]["status"], "building")
        tag_name = "fix_V3.1.25.021_202607041600"
        message = tagged["run"]["create_tag_payload"]["message"]
        self.assertIn("SIMOS_CONFIG_MATRIX=SIMBOT_R6_B:360s", message)
        self.assertIn("SIMOS_CONFIG_REFS=SIMBOT_R6_B", message)
        self.assertIn("SIMOS_CONFIG_REF=SIMBOT_R6_B", message)
        self.assertIn(("create_tag", tag_name, "version-commit", message), simos_client.calls)
        self.assertIn(("create_tag", tag_name, "fix", message), business_client.calls)

    def test_manual_version_number_is_not_incremented_by_weekly_policy(self) -> None:
        self.app.save_schedule(
            {
                "id": "daily-simos-resident-release",
                "daily_time": "16:00",
                "default_ref": "fix_360",
                "version_source": "manual",
                "manual_version_number": "3.1.25.045",
                "cloud_category": "车机/CI自动构建/360",
                "config_ref": "SIMBOT_R6_B",
            }
        )
        self.client._tag_names.append("fix_360_V3.1.25.045_202607071600")

        result = self.app.schedule_dry_run("daily-simos-resident-release", now="2026-07-08T16:29:00+08:00")

        self.assertEqual(result["plan"]["version_number"], "3.1.25.045")
        self.assertEqual(result["plan"]["version"], "V3.1.25.045")
        self.assertEqual(result["plan"]["tag_name"], "fix_360_V3.1.25.045_202607081629")
        self.assertFalse(result["plan"].get("requires_weekly_version_confirmation", False))

    def test_config_ref_is_not_written_to_version_update_files(self) -> None:
        business_repo = RepositoryConfig(
            id="business",
            name="business",
            base_url="https://gitlab.example",
            project="OS/business",
            token_env="BUSINESS_TOKEN",
        )
        simos_client = FakeClient("simos")
        business_client = FakeClient("business")
        self.app.store = FakeStore([self.repo, business_repo])  # type: ignore[assignment]
        self.app.client_for = lambda repo: simos_client if repo.id == "simos" else business_client  # type: ignore[method-assign]
        self.app.save_schedule(
            {
                "id": "daily-simos-resident-release",
                "daily_time": "16:00",
                "default_ref": "fix",
                "config_ref": "SIMBOT_R6_B",
                "version_source": "manual",
                "manual_version_number": "3.1.25.045",
                "cloud_category": "车机/CI自动构建/360",
            }
        )

        result = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-08T16:29:00+08:00")
        continued = self.app.continue_release_run(result["run"]["id"])

        self.assertEqual(continued["run"]["status"], "waiting_version_mr")
        commit_calls = [call for call in simos_client.calls if call[0] == "create_commit"]
        self.assertEqual(len(commit_calls), 1)
        actions = commit_calls[0][3]
        version_info = next(action["content"] for action in actions if action["file_path"] == "version.info")
        software_yaml = next(action["content"] for action in actions if action["file_path"] == "software.yaml")
        tag_name = "fix_V3.1.25.045_202607081629"
        self.assertIn("Version:V3.1.25.045", version_info)
        self.assertIn(f"business_branch:{tag_name}", version_info)
        self.assertNotIn("SIMBOT_R6_B", version_info)
        self.assertIn(f'  business: "{tag_name}"', software_yaml)
        self.assertNotIn("SIMBOT_R6_B", software_yaml)

    def test_config_repository_is_not_tagged_in_full_release(self) -> None:
        config_repo = RepositoryConfig(
            id="config",
            name="config",
            base_url="https://gitlab.example",
            project="OS/config",
            token_env="CONFIG_TOKEN",
            enabled=True,
        )
        business_repo = RepositoryConfig(
            id="business",
            name="business",
            base_url="https://gitlab.example",
            project="OS/business",
            token_env="BUSINESS_TOKEN",
        )
        simos_client = FakeClient("simos")
        business_client = FakeClient("business")
        config_client = FakeClient("config")
        self.app.store = FakeStore([self.repo, business_repo, config_repo])  # type: ignore[assignment]
        self.app.client_for = lambda repo: {"simos": simos_client, "business": business_client, "config": config_client}[repo.id]  # type: ignore[method-assign]
        self.app.save_schedule(
            {
                "id": "daily-simos-resident-release",
                "daily_time": "16:00",
                "default_ref": "fix",
                "config_ref": "SIMBOT_R6_B",
                "version_source": "manual",
                "manual_version_number": "3.1.25.045",
                "cloud_category": "车机/CI自动构建/360",
            }
        )

        result = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-08T16:45:00+08:00")
        continued = self.app.continue_release_run(result["run"]["id"])
        self.assertEqual(continued["run"]["status"], "waiting_version_mr")
        simos_client._merge_requests[0]["state"] = "merged"
        simos_client.branch_commit = {"id": "version-commit", "short_id": "version", "parent_ids": ["simos-new"]}
        tagged = self.app.continue_release_run(result["run"]["id"])
        tag_name = "fix_V3.1.25.045_202607081645"
        message = tagged["run"]["create_tag_payload"]["message"]
        self.assertIn(("create_tag", tag_name, "fix", message), business_client.calls)
        self.assertFalse(any(call[0] == "create_tag" for call in config_client.calls))

    def test_deleting_last_schedule_leaves_empty_list(self) -> None:
        deleted = self.app.delete_schedule("daily-simos-resident-release")

        self.assertTrue(deleted["ok"])
        self.assertEqual(self.app.schedules()["schedules"], [])


if __name__ == "__main__":
    unittest.main()
