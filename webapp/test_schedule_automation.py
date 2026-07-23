from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import server
from gitlab_client import GitLabClient, GitLabConfig
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

    def merge_request(self, iid: int) -> dict[str, Any]:
        self.calls.append(("merge_request", iid))
        for item in self._merge_requests:
            if item.get("iid") == iid:
                return item
        raise server.GitLabError("missing", status=404, payload={})

    def accept_merge_request(self, iid: int) -> dict[str, Any]:
        self.calls.append(("accept_merge_request", iid))
        return self.merge_request(iid)

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

        self.assertEqual(continued["run"]["status"], "merging_version_mr")
        self.assertEqual(continued["run"]["tag_name"], "fix_V3.1.25.020_202607031600")
        self.assertTrue(any(call[0] == "create_merge_request" for call in self.client.calls))
        self.assertIn(("accept_merge_request", 7), self.client.calls)
        self.assertFalse(any(call[0] == "create_tag" for call in self.client.calls))

    def test_schedule_worker_tick_refreshes_builds_before_merges_and_due_schedules(self) -> None:
        self.app.refresh_building_release_runs = Mock(return_value=[{"id": "release-build"}])  # type: ignore[method-assign]
        self.app.resume_pending_release_runs = Mock(return_value=[{"id": "release-1"}])  # type: ignore[method-assign]
        self.app.run_due_schedules = Mock(return_value=[{"id": "release-2"}])  # type: ignore[method-assign]
        now = datetime.fromisoformat("2026-07-04T16:00:00+08:00")

        result = server.schedule_worker_tick(self.app, now)

        self.assertEqual(result, {"builds": [{"id": "release-build"}], "resumed": [{"id": "release-1"}], "due": [{"id": "release-2"}]})
        self.app.refresh_building_release_runs.assert_called_once_with(now)
        self.app.resume_pending_release_runs.assert_called_once_with(now)
        self.app.run_due_schedules.assert_called_once_with(now)

    def test_refresh_building_release_run_publishes_after_successful_pipeline(self) -> None:
        run = {
            "id": "release-build-success",
            "execution_type": "full_release",
            "status": "building",
            "tag_name": "fix_V3.1.27.039_202607221616",
            "started_at": "2026-07-22T16:16:00+08:00",
            "updated_at": "2026-07-22T16:16:00+08:00",
            "finished_at": "",
        }
        server.append_release_run(run)
        self.app.resident_package = Mock(  # type: ignore[method-assign]
            return_value={
                "status": "success",
                "pipeline": {"status": "success", "web_url": "https://gitlab.example/pipelines/42"},
                "pipeline_url": "https://gitlab.example/pipelines/42",
                "cloud_dir": "/public/Versions/2026-07-22_V3.1.27.039/CI",
                "artifact_path": "/public/Versions/2026-07-22_V3.1.27.039/CI/resident.tar.gz",
            }
        )
        completed_at = datetime.fromisoformat("2026-07-22T16:20:00+08:00")

        updated = self.app.refresh_building_release_runs(completed_at)

        self.assertEqual([item["id"] for item in updated], [run["id"]])
        persisted = server.load_release_runs()[0]
        self.assertEqual(persisted["status"], "published")
        self.assertEqual(persisted["finished_at"], completed_at.isoformat())
        self.assertEqual(persisted["package"]["status"], "success")
        self.assertEqual(persisted["pipeline_url"], "https://gitlab.example/pipelines/42")
        self.assertEqual(persisted["cloud_dir"], "/public/Versions/2026-07-22_V3.1.27.039/CI")

    def test_refresh_building_release_run_stays_building_while_pipeline_is_active(self) -> None:
        run = {
            "id": "release-build-running",
            "execution_type": "full_release",
            "status": "building",
            "tag_name": "fix_V3.1.27.039_202607221616",
            "started_at": "2026-07-22T16:16:00+08:00",
            "updated_at": "2026-07-22T16:16:00+08:00",
            "finished_at": "",
        }
        server.append_release_run(run)
        self.app.resident_package = Mock(  # type: ignore[method-assign]
            return_value={
                "status": "pending_or_missing",
                "pipeline": {"status": "running", "web_url": "https://gitlab.example/pipelines/42"},
                "pipeline_url": "https://gitlab.example/pipelines/42",
                "progress": {"active": True},
            }
        )

        updated = self.app.refresh_building_release_runs(datetime.fromisoformat("2026-07-22T16:20:00+08:00"))

        self.assertEqual([item["id"] for item in updated], [run["id"]])
        persisted = server.load_release_runs()[0]
        self.assertEqual(persisted["status"], "building")
        self.assertEqual(persisted["finished_at"], "")
        self.assertEqual(persisted["package"]["pipeline"]["status"], "running")

    def test_release_tasks_refreshes_a_completed_build_before_returning_runs(self) -> None:
        run = {
            "id": "release-build-listing",
            "execution_type": "full_release",
            "status": "building",
            "tag_name": "fix_V3.1.27.039_202607221616",
            "started_at": "2026-07-22T16:16:00+08:00",
            "updated_at": "2026-07-22T16:16:00+08:00",
            "finished_at": "",
        }
        server.append_release_run(run)
        self.app.resident_package = Mock(return_value={"status": "success", "pipeline": {"status": "success"}})  # type: ignore[method-assign]

        result = self.app.release_tasks()

        self.assertEqual(result["runs"][0]["status"], "published")
        self.assertTrue(result["runs"][0]["finished_at"])

    def test_release_run_poll_seconds_uses_a_safe_environment_override(self) -> None:
        with patch.dict("os.environ", {"GITOPS_RELEASE_RUN_POLL_SECONDS": "15"}):
            self.assertEqual(server.release_run_poll_seconds(), 15)
        with patch.dict("os.environ", {"GITOPS_RELEASE_RUN_POLL_SECONDS": "invalid"}):
            self.assertEqual(server.release_run_poll_seconds(), 10)

    def test_gitlab_client_queries_one_merge_request_by_iid(self) -> None:
        client = GitLabClient(GitLabConfig("https://gitlab.example", "OS/simos", "token"))
        client.request = Mock(return_value={"iid": 17, "state": "opened"})  # type: ignore[method-assign]

        merge_request = client.merge_request(17)

        self.assertEqual(merge_request["iid"], 17)
        client.request.assert_called_once_with("GET", "/projects/OS%2Fsimos/merge_requests/17")

    def test_gitlab_auto_merge_uses_required_squash_without_pipeline_wait(self) -> None:
        client = GitLabClient(GitLabConfig("https://gitlab.example", "OS/simos", "token"))
        client.request = Mock(return_value={"iid": 17, "state": "opened"})  # type: ignore[method-assign]

        client.accept_merge_request(17)

        client.request.assert_called_once_with(
            "PUT",
            "/projects/OS%2Fsimos/merge_requests/17/merge",
            payload={
                "should_remove_source_branch": False,
                "squash": True,
            },
        )

    def test_full_release_requests_version_merge_and_persists_retry_metadata(self) -> None:
        self.app.save_schedule({"id": "daily-simos-resident-release", "config_ref": "SIMBOT_R6_B"})

        result = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-04T16:00:00+08:00")

        run = result["run"]
        self.assertEqual(run["status"], "merging_version_mr")
        self.assertEqual(run["version_merge"]["iid"], 7)
        self.assertEqual(run["version_merge"]["state"], "opened")
        self.assertEqual(run["version_merge"]["attempt_count"], 1)
        self.assertTrue(run["version_merge"]["requested_at"])
        self.assertTrue(run["version_merge"]["last_checked_at"])
        self.assertTrue(run["version_merge"]["next_retry_at"])
        self.assertEqual(run["version_merge"]["last_error"], "")
        self.assertIn(("accept_merge_request", 7), self.client.calls)
        self.assertFalse(any(call[0] == "create_tag" for call in self.client.calls))

    def test_resume_pending_release_runs_tags_only_after_the_version_mr_is_merged(self) -> None:
        self.app.save_schedule({"id": "daily-simos-resident-release", "config_ref": "SIMBOT_R6_B"})
        created = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-04T16:00:00+08:00")["run"]
        self.client._merge_requests[0]["state"] = "merged"
        self.client.branch_commit = {"id": "version-commit", "short_id": "version", "parent_ids": ["simos-new"]}

        resumed = self.app.resume_pending_release_runs(datetime.fromisoformat("2026-07-04T16:00:20+08:00"))

        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0]["id"], created["id"])
        self.assertEqual(resumed[0]["status"], "building")
        self.assertIn(("merge_request", 7), self.client.calls)
        tag_calls = [call for call in self.client.calls if call[0] == "create_tag"]
        self.assertEqual(len(tag_calls), 1)
        self.app.resume_pending_release_runs(datetime.fromisoformat("2026-07-04T16:00:40+08:00"))
        self.assertEqual(len([call for call in self.client.calls if call[0] == "create_tag"]), 1)

    def test_resume_retries_until_max_attempts_then_manual_retry_resets_merge_state(self) -> None:
        self.app.save_schedule({"id": "daily-simos-resident-release", "config_ref": "SIMBOT_R6_B"})
        created = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-04T16:00:00+08:00")["run"]
        self.client.accept_merge_request = Mock(side_effect=server.GitLabError("merge blocked"))  # type: ignore[method-assign]
        base = datetime.fromisoformat(created["version_merge"]["next_retry_at"])

        for offset in range(5):
            self.app.resume_pending_release_runs(base + timedelta(seconds=offset * 10))

        failed = next(run for run in server.load_release_runs() if run["id"] == created["id"])
        self.assertEqual(failed["status"], "auto_merge_failed")
        self.assertEqual(failed["version_merge"]["attempt_count"], 6)
        self.assertFalse(any(call[0] == "create_tag" for call in self.client.calls))

        retried = self.app.retry_version_merge(created["id"])["run"]

        self.assertEqual(retried["status"], "waiting_version_mr_retry")
        self.assertEqual(retried["version_merge"]["attempt_count"], 1)
        self.assertFalse(any(call[0] == "create_tag" for call in self.client.calls))

    def test_not_yet_mergeable_response_waits_without_consuming_merge_attempt(self) -> None:
        self.app.save_schedule({"id": "daily-simos-resident-release", "config_ref": "SIMBOT_R6_B"})
        self.client.accept_merge_request = Mock(side_effect=server.GitLabError("pipeline is still running", status=405))  # type: ignore[method-assign]

        created = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-04T16:00:00+08:00")["run"]

        self.assertEqual(created["status"], "waiting_version_mr_retry")
        self.assertEqual(created["version_merge"]["attempt_count"], 0)
        self.assertIn("pipeline", created["version_merge"]["last_error"])
        self.assertTrue(created["version_merge"]["next_retry_at"])

    def test_save_release_runs_replaces_a_sibling_temp_file_atomically(self) -> None:
        with patch.object(server.os, "replace") as replace:
            server.save_release_runs([{"id": "run-atomic"}])

        replace.assert_called_once()
        temporary_path, target_path = (Path(value) for value in replace.call_args.args)
        self.assertEqual(target_path, server.RELEASE_RUNS_PATH)
        self.assertEqual(temporary_path.parent, server.RELEASE_RUNS_PATH.parent)
        self.assertNotEqual(temporary_path, target_path)

    def test_closed_version_mr_ends_the_release_run_without_tagging(self) -> None:
        self.app.save_schedule({"id": "daily-simos-resident-release", "config_ref": "SIMBOT_R6_B"})
        created = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-04T16:00:00+08:00")["run"]
        self.client._merge_requests[0]["state"] = "closed"

        resumed = self.app.resume_pending_release_runs(datetime.fromisoformat("2026-07-04T16:00:20+08:00"))

        self.assertEqual(resumed[0]["status"], "closed")
        self.assertIn("关闭", resumed[0]["version_merge"]["last_error"])
        self.assertFalse(any(call[0] == "create_tag" for call in self.client.calls))
        with self.assertRaisesRegex(ValueError, "只有自动合并失败"):
            self.app.retry_version_merge(created["id"])

    def test_continue_release_run_rejects_building_run_without_duplicate_tag(self) -> None:
        self.app.save_schedule({"id": "daily-simos-resident-release", "config_ref": "SIMBOT_R6_B"})
        created = self.app.schedule_run_now("daily-simos-resident-release", now="2026-07-04T16:00:00+08:00")["run"]
        self.client._merge_requests[0]["state"] = "merged"
        self.client.branch_commit = {"id": "version-commit", "short_id": "version", "parent_ids": ["simos-new"]}
        self.app.resume_pending_release_runs(datetime.fromisoformat("2026-07-04T16:00:20+08:00"))
        tag_count = len([call for call in self.client.calls if call[0] == "create_tag"])

        with self.assertRaisesRegex(ValueError, "不可继续"):
            self.app.continue_release_run(created["id"])

        self.assertEqual(len([call for call in self.client.calls if call[0] == "create_tag"]), tag_count)

    def test_release_task_previews_return_a_plan_or_error_for_every_task(self) -> None:
        self.app.save_schedule(
            {
                "id": "invalid-preview-task",
                "daily_time": "18:30",
                "default_ref": "missing-ref",
                "config_ref": "SIMBOT_R6_B",
            }
        )

        previews = self.app.release_task_previews("2026-07-04T16:00:00+08:00")

        self.assertTrue(previews["ok"])
        by_id = {item["task_id"]: item for item in previews["previews"]}
        self.assertTrue(by_id["daily-simos-resident-release"]["ok"])
        self.assertIn("plan", by_id["daily-simos-resident-release"])
        self.assertEqual(by_id["daily-simos-resident-release"]["version"], by_id["daily-simos-resident-release"]["plan"]["version"])
        self.assertEqual(by_id["daily-simos-resident-release"]["tag_name"], by_id["daily-simos-resident-release"]["plan"]["tag_name"])
        self.assertEqual(by_id["daily-simos-resident-release"]["source_ref"], by_id["daily-simos-resident-release"]["plan"]["source_ref"])
        self.assertEqual(by_id["daily-simos-resident-release"]["config_matrix"], by_id["daily-simos-resident-release"]["plan"]["config_matrix"])
        self.assertEqual(by_id["daily-simos-resident-release"]["calculated_at"], by_id["daily-simos-resident-release"]["plan"]["planned_at"])
        self.assertFalse(by_id["invalid-preview-task"]["ok"])
        self.assertIn("来源 ref 不存在", by_id["invalid-preview-task"]["error"])

    def test_retry_version_merge_post_route_uses_create_tag_permission(self) -> None:
        token = self.app.auth.login("admin", "admin123")["token"]
        self.app.retry_version_merge = Mock(return_value={"ok": True, "run": {"id": "run-1"}})  # type: ignore[method-assign]
        handler = object.__new__(server.make_handler(self.app))
        handler.path = "/api/release-runs/run-1/retry-version-merge"
        handler.headers = {"Cookie": server.login_cookie(token), "Content-Length": "2"}
        handler.rfile = io.BytesIO(b"{}")
        handler.wfile = io.BytesIO()
        handler.extra_headers = {}
        statuses: list[int] = []
        handler.send_response = lambda status, *args: statuses.append(status)
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None

        handler.do_POST()

        self.assertEqual(statuses, [200])
        self.app.retry_version_merge.assert_called_once_with("run-1")

    def test_release_task_previews_get_route(self) -> None:
        token = self.app.auth.login("admin", "admin123")["token"]
        self.app.release_task_previews = Mock(return_value={"ok": True, "previews": []})  # type: ignore[method-assign]
        handler = object.__new__(server.make_handler(self.app))
        handler.path = "/api/release-task-previews"
        handler.headers = {"Cookie": server.login_cookie(token)}
        handler.wfile = io.BytesIO()
        handler.extra_headers = {}
        statuses: list[int] = []
        handler.send_response = lambda status, *args: statuses.append(status)
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None

        handler.do_GET()

        self.assertEqual(statuses, [200])
        self.app.release_task_previews.assert_called_once_with()

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

    def test_delete_release_run_removes_only_the_requested_record(self) -> None:
        server.save_release_runs(
            [
                {"id": "run-keep", "tag_name": "keep"},
                {"id": "run-delete", "tag_name": "delete"},
            ]
        )

        result = self.app.delete_release_run("run-delete")

        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], "run-delete")
        self.assertEqual([item["id"] for item in result["runs"]], ["run-keep"])
        self.assertEqual([item["id"] for item in server.load_release_runs()], ["run-keep"])

    def test_delete_release_run_rejects_an_unknown_record(self) -> None:
        server.save_release_runs([{"id": "run-keep"}])

        with self.assertRaisesRegex(ValueError, "发版运行不存在：run-missing"):
            self.app.delete_release_run("run-missing")

        self.assertEqual([item["id"] for item in server.load_release_runs()], ["run-keep"])

    def test_delete_release_run_rejects_an_empty_id_without_changing_history(self) -> None:
        server.save_release_runs([{"id": "run-keep"}])

        with self.assertRaisesRegex(ValueError, "发版运行 ID 不能为空"):
            self.app.delete_release_run("")

        self.assertEqual([item["id"] for item in server.load_release_runs()], ["run-keep"])

    def test_delete_release_runs_trailing_slash_returns_an_empty_id_error(self) -> None:
        server.save_release_runs([{"id": "run-keep"}])
        token = self.app.auth.login("admin", "admin123")["token"]
        handler = object.__new__(server.make_handler(self.app))
        handler.path = "/api/release-runs/"
        handler.headers = {"Cookie": server.login_cookie(token)}
        handler.wfile = io.BytesIO()
        handler.extra_headers = {}
        statuses: list[int] = []
        handler.send_response = lambda status, *args: statuses.append(status)
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None

        handler.do_DELETE()

        response = json.loads(handler.wfile.getvalue())
        self.assertEqual(statuses, [400])
        self.assertEqual(response["error"], "发版运行 ID 不能为空")
        self.assertEqual([item["id"] for item in server.load_release_runs()], ["run-keep"])

    def test_clear_release_runs_persists_an_empty_history(self) -> None:
        server.save_release_runs([{"id": "run-one"}, {"id": "run-two"}])

        result = self.app.clear_release_runs()

        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(result["runs"], [])
        self.assertEqual(server.load_release_runs(), [])

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
        self.assertEqual(continued["run"]["status"], "merging_version_mr")
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

        self.assertEqual(continued["run"]["status"], "merging_version_mr")
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
        self.assertEqual(continued["run"]["status"], "merging_version_mr")
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
