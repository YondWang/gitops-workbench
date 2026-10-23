from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import server
from repository_store import RepositoryConfig


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
        self.tags_data: list[dict[str, Any]] = []
        self.pipeline_variables: dict[str, str] | None = None
        self.pipeline_status = "running"
        self.jobs: list[dict[str, Any]] = []
        self.calls: list[tuple[str, Any]] = []
        self.ci_text = 'variables:\n  SIMOS_X86_PACKAGE: "off"\nsim_package_x86:\n  script: []\n'

    def tags(self, search: str = "") -> list[dict[str, Any]]:
        return [item for item in self.tags_data if not search or search in item.get("name", "")]

    def tag_names(self) -> list[str]:
        return [item["name"] for item in self.tags_data]

    def branch_names(self) -> list[str]:
        return ["fix"]

    def project(self) -> dict[str, Any]:
        return {"id": "simos"}

    def create_tag(self, tag_name: str, ref: str, message: str = "") -> dict[str, Any]:
        self.calls.append(("create_tag", tag_name, ref, message))
        return {"name": tag_name, "ref": ref, "message": message}

    def create_pipeline(self, ref: str, variables: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append(("create_pipeline", ref, variables))
        self.pipeline_variables = dict(variables or {})
        return {"id": 42, "web_url": "https://gitlab.example/OS/simos/-/pipelines/42"}

    def pipelines(self, ref: str = "", status: str = "", source: str = "") -> list[dict[str, Any]]:
        return [{"id": 42, "ref": ref, "status": self.pipeline_status}]

    def pipeline_jobs(self, pipeline_id: int | str) -> list[dict[str, Any]]:
        return list(self.jobs)

    def get_file_text(self, file_path: str, ref: str) -> str:
        return self.ci_text


class SimPackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.previous_tasks = server.SIM_TEST_TASKS_PATH
        self.previous_reports = server.SIM_TEST_REPORT_ROOT
        self.previous_artifacts = server.RESIDENT_ARTIFACT_ROOT
        self.previous_release_tasks = server.RELEASE_TASKS_PATH
        self.previous_release_runs = server.RELEASE_RUNS_PATH
        server.SIM_TEST_TASKS_PATH = self.root / "sim_test_tasks.json"
        server.SIM_TEST_REPORT_ROOT = self.root / "reports"
        server.RESIDENT_ARTIFACT_ROOT = self.root / "artifacts"
        server.RELEASE_TASKS_PATH = self.root / "release_tasks.json"
        server.RELEASE_RUNS_PATH = self.root / "release_runs.json"
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
        os.environ["GITOPS_SIM_PACKAGE_ENABLED"] = "true"
        os.environ["GITOPS_SIM_TEST_TIMEOUT_HOURS"] = "24"
        os.environ["SIMOS_SIM_NOTIFY_CALLBACK_TOKEN"] = "callback-token"
        os.environ["GITOPS_SIM_RESULT_URL"] = "https://gitops.example/api/sim-test-results"

    def tearDown(self) -> None:
        server.SIM_TEST_TASKS_PATH = self.previous_tasks
        server.SIM_TEST_REPORT_ROOT = self.previous_reports
        server.RESIDENT_ARTIFACT_ROOT = self.previous_artifacts
        server.RELEASE_TASKS_PATH = self.previous_release_tasks
        server.RELEASE_RUNS_PATH = self.previous_release_runs
        os.environ.pop("GITOPS_SIM_NOTIFY_TARGETS", None)
        self.tmp.cleanup()

    def make_task(self, status: str = "queued", deadline_at: str = "") -> dict[str, Any]:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        task = server.new_sim_test_task_record(
            "SIMTEST-20261030143000-a1b2c3",
            "fix_V3.1.24.020_202610301400",
            "3.1.24.020",
            "both",
            "回归",
            "panel",
            "",
            now,
        )
        task["test_status"] = status
        task["deadline_at"] = deadline_at
        if status == "notified":
            task["pipeline_id"] = "42"
        server.save_sim_test_task(task)
        return server.load_sim_test_tasks()[0]

    def test_new_sim_test_id_format(self) -> None:
        for _ in range(20):
            self.assertRegex(server.new_sim_test_id(), r"^SIMTEST-[0-9]{14}-[0-9a-f]{6}$")

    def test_start_pipeline_passes_sim_variables(self) -> None:
        pipeline = self.app.start_simos_pipeline(
            "fix_V3.1.24.020_202610301400",
            None,
            None,
            {
                "task_id": "SIMTEST-20261030143000-a1b2c3",
                "mode": "only",
                "test_target": "vehicle",
                "note": "实车回归",
            },
        )

        self.assertEqual(pipeline["id"], 42)
        self.assertEqual(self.client.pipeline_variables, {
            "SIMOS_X86_PACKAGE": "only",
            "SIMOS_SIM_TASK_ID": "SIMTEST-20261030143000-a1b2c3",
            "SIMOS_SIM_TEST_TARGET": "vehicle",
            "SIMOS_SIM_TEST_NOTE": "实车回归",
        })

    def test_pipeline_config_matrix_variable_overrides_tag_message(self) -> None:
        self.app.start_simos_pipeline(
            "fix_V3.1.24.020_202610301400",
            None,
            None,
            None,
            [{"config_ref": "SIMBOT_R6_A", "label": "360"}],
        )

        self.assertEqual(self.client.pipeline_variables["SIMOS_CONFIG_MATRIX"], "SIMBOT_R6_A:360")
        self.assertEqual(self.client.pipeline_variables["SIMOS_CONFIG_REFS"], "SIMBOT_R6_A")
        self.assertEqual(self.client.pipeline_variables["SIMOS_CONFIG_REF"], "SIMBOT_R6_A")
        self.assertEqual(self.client.pipeline_variables.get("SIMOS_CONFIG_MODE"), "single")

    def test_pipeline_declares_dual_config_mode(self) -> None:
        self.app.start_simos_pipeline(
            "fix_V3.1.24.020_202610301400", config_matrix=[
                {"config_ref": "SIMBOT_R6_A", "label": "360"},
                {"config_ref": "SIMBOT_R6_B", "label": "360s"},
            ],
        )
        self.assertEqual(self.client.pipeline_variables.get("SIMOS_CONFIG_MODE"), "matrix")

    def test_existing_single_config_tag_rerun_preserves_config_and_mode(self) -> None:
        tag = "fix_V3.1.24.020_202610301400"
        self.client.tags_data = [{"name": tag, "message": "SIMOS_CONFIG_REF=feature/v3.2_x86ci"}]
        with patch.object(self.app, "resident_package", return_value={"status": "pending_or_missing"}):
            result = self.app.rerun_tag_release({"tag_name": tag})
        self.assertTrue(result["ok"])
        self.assertEqual(self.client.pipeline_variables.get("SIMOS_CONFIG_MODE"), "single")
        self.assertEqual(self.client.pipeline_variables["SIMOS_CONFIG_MATRIX"],
                         "feature/v3.2_x86ci:feature-v3.2_x86ci")

    def test_tag_matrix_takes_precedence_over_first_ref_regardless_of_line_order(self) -> None:
        matrix = server.config_matrix_from_tag_message(
            "SIMOS_CONFIG_REF=SIMBOT_R6_A\nSIMOS_CONFIG_MATRIX=SIMBOT_R6_A:360,SIMBOT_R6_B:360s"
        )
        self.assertEqual(matrix, [{"config_ref": "SIMBOT_R6_A", "label": "360"},
                                  {"config_ref": "SIMBOT_R6_B", "label": "360s"}])

    def test_config_label_with_slash_is_accepted_for_single_branch(self) -> None:
        matrix = server.resolve_config_matrix({
            "config_matrix_enabled": False,
            "config_ref": "feature/v3.2_x86ci",
        })

        self.assertEqual(matrix, [{"config_ref": "feature/v3.2_x86ci", "label": "feature-v3.2_x86ci"}])
        self.assertEqual(server.config_matrix_value(matrix), "feature/v3.2_x86ci:feature-v3.2_x86ci")

    def test_explicit_config_label_with_slash_is_accepted(self) -> None:
        matrix = server.normalize_config_matrix_items([
            {"config_ref": "feature/v3.2_x86ci", "label": "feature/v3.2_x86ci"},
        ])

        self.assertEqual(matrix[0]["label"], "feature/v3.2_x86ci")
        self.assertEqual(server.config_matrix_value(matrix), "feature/v3.2_x86ci:feature-v3.2_x86ci")

    def test_delete_sim_test_task_removes_record_and_local_report(self) -> None:
        task = self.make_task()
        report_dir = server.SIM_TEST_REPORT_ROOT / task["id"]
        report_dir.mkdir(parents=True)
        (report_dir / "report.html").write_text("report", encoding="utf-8")

        result = self.app.delete_sim_test_task(task["id"])

        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], task["id"])
        self.assertEqual(server.load_sim_test_tasks(), [])
        self.assertFalse(report_dir.exists())

    def test_delete_unknown_sim_test_task_is_rejected(self) -> None:
        with self.assertRaises(server.SimTestNotFound):
            self.app.delete_sim_test_task("SIMTEST-20261030143000-ffffff")

    def test_release_create_tag_with_sim_task_creates_record_and_uses_with_release(self) -> None:
        tag = "fix_V3.1.24.020_202610301400"
        task_id = "SIMTEST-20261030143000-a1b2c3"
        result = self.app.create_tag(
            {
                "repository_ids": ["simos"],
                "ref": "fix",
                "tag_name": tag,
                "message": "SIMOS_CONFIG_REF=SIMBOT_R6_A",
                "config_matrix": [{"config_ref": "SIMBOT_R6_A", "label": "360"}],
                "sim_task": {
                    "task_id": task_id,
                    "mode": "with_release",
                    "test_target": "sim",
                    "note": "仿真回归",
                    "trigger": "release",
                    "release_run_id": "release-test",
                },
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["sim_task_id"], task_id)
        self.assertEqual(self.client.pipeline_variables["SIMOS_X86_PACKAGE"], "with_release")
        self.assertEqual(self.client.pipeline_variables["SIMOS_CONFIG_MATRIX"], "SIMBOT_R6_A:360")
        task = next(item for item in server.load_sim_test_tasks() if item["id"] == task_id)
        self.assertEqual(task["trigger"], "release")
        self.assertEqual(task["release_run_id"], "release-test")
        self.assertEqual(task["pipeline_id"], "42")

    def test_dual_config_release_task_rejects_sim_package(self) -> None:
        with self.assertRaisesRegex(ValueError, "双配置发版不支持"):
            self.app.save_release_task(
                {
                    "id": "dual-sim",
                    "build_sim_package": True,
                    "config_matrix_enabled": True,
                    "sim_test_target": "both",
                    "sim_test_note": "note",
                }
            )

    def test_single_config_release_task_accepts_sim_package(self) -> None:
        result = self.app.save_release_task(
            {
                "id": "single-sim",
                "build_sim_package": True,
                "config_matrix_enabled": False,
                "config_ref": "SIMBOT_R6_A",
                "sim_test_target": "sim",
                "sim_test_note": "仿真回归",
            }
        )

        self.assertTrue(result["task"]["build_sim_package"])
        self.assertEqual(result["task"]["sim_test_target"], "sim")

    def test_panel_rejects_dual_config_tag(self) -> None:
        self.client.tags_data = [
            {
                "name": "fix_V3.1.24.020_202610301400",
                "message": "SIMOS_CONFIG_MATRIX=SIMBOT_R6_A:360,SIMBOT_R6_B:360s",
            }
        ]

        with self.assertRaisesRegex(ValueError, "双配置"):
            self.app.create_sim_test_task(
                {
                    "tag": "fix_V3.1.24.020_202610301400",
                    "test_target": "both",
                    "note": "",
                }
            )

    def test_panel_rejects_tag_without_x86_ci(self) -> None:
        self.client.tags_data = [
            {
                "name": "fix_V3.1.24.020_202610301400",
                "message": "SIMOS_CONFIG_REF=SIMBOT_R6_A",
            }
        ]
        self.client.ci_text = (
            "resident_package:\n"
            "  parallel:\n"
            "    matrix:\n"
            "      - SIMOS_MATRIX_CONFIG_REF: SIMBOT_R6_A\n"
        )

        with self.assertRaisesRegex(ValueError, "不支持 x86"):
            self.app.create_sim_test_task(
                {
                    "tag": "fix_V3.1.24.020_202610301400",
                    "test_target": "sim",
                    "note": "",
                }
            )
        self.assertIsNone(self.client.pipeline_variables)
        self.assertEqual(server.load_sim_test_tasks(), [])

    def test_panel_creates_task_with_only_mode_and_variables(self) -> None:
        self.client.tags_data = [
            {
                "name": "fix_V3.1.24.020_202610301400",
                "message": "SIMOS_CONFIG_REF=SIMBOT_R6_A",
            }
        ]

        result = self.app.create_sim_test_task(
            {
                "tag": "fix_V3.1.24.020_202610301400",
                "test_target": "vehicle",
                "note": "实车回归",
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["test_status"], "queued")
        self.assertEqual(result["task"]["trigger"], "panel")
        self.assertEqual(self.client.pipeline_variables["SIMOS_X86_PACKAGE"], "only")
        self.assertEqual(self.client.pipeline_variables["SIMOS_SIM_TEST_TARGET"], "vehicle")
        self.assertEqual(self.client.pipeline_variables["SIMOS_SIM_TEST_NOTE"], "实车回归")
        self.assertEqual(self.client.pipeline_variables["SIMOS_CONFIG_MATRIX"], "SIMBOT_R6_A:360")

    def test_panel_accepts_single_config_branch_with_slash(self) -> None:
        self.client.tags_data = [
            {
                "name": "fix_V3.1.24.020_202610301400",
                "message": "SIMOS_CONFIG_REF=feature/v3.2_x86ci",
            }
        ]

        result = self.app.create_sim_test_task(
            {
                "tag": "fix_V3.1.24.020_202610301400",
                "test_target": "sim",
                "note": "x86 单分支",
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["test_status"], "queued")
        self.assertEqual(self.client.pipeline_variables["SIMOS_X86_PACKAGE"], "only")
        self.assertEqual(self.client.pipeline_variables["SIMOS_CONFIG_MATRIX"], "feature/v3.2_x86ci:feature-v3.2_x86ci")
        self.assertEqual(self.client.pipeline_variables["SIMOS_CONFIG_REF"], "feature/v3.2_x86ci")

    def test_callback_updates_status_and_stores_report_file(self) -> None:
        task = self.make_task()
        result = self.app.apply_sim_test_result(
            {
                "task_id": task["id"],
                "status": "passed",
                "summary": "全部用例通过",
                "finished_at": "2026-10-30T18:00:00+08:00",
            },
            report_file=("report.html", b"<html>passed</html>"),
        )

        updated = result["task"]
        self.assertEqual(updated["test_status"], "passed")
        self.assertEqual(updated["report"]["summary"], "全部用例通过")
        self.assertTrue(Path(updated["report"]["local_path"]).is_file())
        self.assertEqual(updated["finished_at"], "2026-10-30T18:00:00+08:00")

    def test_running_callback_resets_wait_deadline(self) -> None:
        past = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(hours=1)).isoformat()
        task = self.make_task("notified", deadline_at=past)

        result = self.app.apply_sim_test_result({"task_id": task["id"], "status": "running"})

        deadline = datetime.fromisoformat(result["task"]["deadline_at"])
        self.assertGreater(deadline, datetime.now(ZoneInfo("Asia/Shanghai")))

    def test_callback_unknown_task_is_not_found(self) -> None:
        with self.assertRaises(server.SimTestNotFound):
            self.app.apply_sim_test_result({"task_id": "SIMTEST-20261030143000-ffffff", "status": "running"})

    def test_callback_token_validation(self) -> None:
        self.assertTrue(server.validate_sim_callback_token("callback-token"))
        self.assertFalse(server.validate_sim_callback_token("wrong-token"))
        self.assertFalse(server.validate_sim_callback_token(""))
        previous = os.environ.pop("SIMOS_SIM_NOTIFY_CALLBACK_TOKEN", None)
        try:
            self.assertFalse(server.validate_sim_callback_token("callback-token"))
        finally:
            if previous is not None:
                os.environ["SIMOS_SIM_NOTIFY_CALLBACK_TOKEN"] = previous

    def test_multipart_parser_extracts_report_and_fields(self) -> None:
        handler_cls = server.make_handler(self.app)
        boundary = "----simtestboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="task_id"\r\n\r\n'
            "SIMTEST-20261030143000-a1b2c3\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="status"\r\n\r\n'
            "failed\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="report"; filename="report.html"\r\n'
            "Content-Type: text/html\r\n\r\n"
            "<html>failed</html>\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")

        fields, report_file = handler_cls.parse_sim_test_result_body(
            body,
            f"multipart/form-data; boundary={boundary}",
        )

        self.assertEqual(fields["task_id"], "SIMTEST-20261030143000-a1b2c3")
        self.assertEqual(fields["status"], "failed")
        self.assertEqual(report_file[0], "report.html")
        self.assertIn(b"failed", report_file[1])

    def test_timeout_marks_task_and_can_be_adjusted(self) -> None:
        past = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(hours=1)).isoformat()
        task = self.make_task("notified", deadline_at=past)
        updated = self.app.refresh_sim_test_tasks()
        self.assertEqual(updated[0]["test_status"], "timeout")

        result = self.app.adjust_sim_test_timeout(task["id"], {"timeout_hours": 1.5})
        self.assertEqual(result["task"]["timeout_hours"], 1.5)
        self.assertTrue(result["task"]["deadline_at"])

    def test_renotify_marks_notified_and_resets_deadline(self) -> None:
        task = self.make_task("timeout")
        os.environ["GITOPS_SIM_NOTIFY_TARGETS"] = json.dumps(
            [{"name": "cloud-platform", "url": "https://cloud.example/task", "token": "t", "enabled": True}]
        )
        with patch.object(server, "post_sim_notify_targets", return_value=[
            {"name": "cloud-platform", "status": "success", "http_code": "200", "attempts": 1, "error": ""}
        ]):
            result = self.app.renotify_sim_test_task(task["id"])

        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["test_status"], "notified")
        self.assertTrue(result["task"]["deadline_at"])
        self.assertEqual(result["task"]["payload"]["callback"]["token"], "***")

    def test_report_cloud_upload_uses_incoming_root_and_version_prefix(self) -> None:
        task = self.make_task("passed")
        report = server.write_sim_report_file(task["id"], "report.html", b"<html>report</html>")
        os.environ.update({
            "GITOPS_CLOUD_UPLOAD_SSH_KEY": "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----",
            "GITOPS_CLOUD_UPLOAD_HOST": "192.168.110.17",
            "GITOPS_CLOUD_UPLOAD_USER": "simos-ci-uploader",
            "GITOPS_CLOUD_UPLOAD_BASE": str(self.root / "incoming"),
            "GITOPS_SIM_SERVER_PUBLISHER": "/usr/local/bin/simos-ci-publish-sim",
        })
        calls: list[list[str]] = []

        def fake_run(args, **kwargs):
            calls.append([str(item) for item in args])
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"", stderr=b"")

        with patch.object(server.subprocess, "run", side_effect=fake_run):
            cloud_dir = server.upload_sim_report_to_cloud(task, report)

        remote_root = f"{self.root / 'incoming'}/{task['tag']}/{task['id']}"
        self.assertEqual(cloud_dir, f"/public/Versions/2026-10-30_V3.1.24.020/车机/CI自动构建/sim-test-report/{task['id']}")
        ssh_commands = [call for call in calls if call and call[0] == "ssh"]
        scp_commands = [call for call in calls if call and call[0] == "scp"]
        self.assertIn(remote_root, " ".join(ssh_commands[-1]))
        self.assertTrue(any(f":{remote_root}/sim-test-report/" in arg for arg in scp_commands[-1]))
        manifest = json.loads((Path(server.SIM_TEST_REPORT_ROOT) / task["id"] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["cloud_dir"], cloud_dir)

    def test_merge_artifacts_builds_registry_links(self) -> None:
        task = self.make_task()
        root = server.RESIDENT_ARTIFACT_ROOT / task["tag"]
        (root / "sim-x86").mkdir(parents=True)
        (root / "sim-x86" / "manifest.json").write_text(
            json.dumps(
                {
                    "cloud_dir": "/public/Versions/2026-10-30_3.1.24.020/车机/CI自动构建/sim-x86",
                    "files": [{"file": f"simos_x86_sim_{task['tag']}.tar.gz", "size": 10, "md5": "m", "sha256": "s"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "resident").mkdir(parents=True)
        (root / "resident" / "manifest.json").write_text(
            json.dumps(
                {
                    "cloud_dir": "/public/Versions/2026-10-30_3.1.24.020/车机/CI自动构建/resident",
                    "config_variants": [{"label": "360", "sha256": "r", "md5": "r"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "deb").mkdir(parents=True)
        (root / "deb" / "deb-package-manifest.json").write_text(
            json.dumps(
                {
                    "cloud_dir": "/public/Versions/2026-10-30_3.1.24.020/车机/CI自动构建/deb",
                    "config_variants": [{
                        "label": "360",
                        "files": [{"file": "simos_3.1.24.020.zip", "path": "deb/360/simos_3.1.24.020.zip"}],
                    }],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        merged = server.merge_sim_test_artifacts(task, self.repo)

        self.assertEqual(merged["packages"]["x86"]["cloud_dir"], "/public/Versions/2026-10-30_3.1.24.020/车机/CI自动构建/sim-x86")
        self.assertIn("packages/generic/simos-sim-x86", merged["packages"]["x86"]["registry_url"])
        self.assertEqual(merged["packages"]["arm"]["resident"]["name"], "360-resident.tar.gz")
        self.assertEqual(merged["packages"]["arm"]["debs"][0]["name"], "360-simos_3.1.24.020.zip")

    def test_frontend_defines_automated_test_view_and_single_config_guard(self) -> None:
        index = (server.STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        app_js = (server.STATIC_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view="simtests"', index)
        self.assertIn('<section id="simtests"', index)
        self.assertIn('data-sim-package-fields', index)
        self.assertIn("createSimTestTask", app_js)
        self.assertIn("syncSimPackageControls", app_js)
        self.assertIn("双配置矩阵下不支持仿真包", app_js)
        self.assertIn("/api/sim-test-tasks/create", app_js)


if __name__ == "__main__":
    unittest.main()
