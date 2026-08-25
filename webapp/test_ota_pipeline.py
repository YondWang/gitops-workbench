from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock

import server
from gitlab_client import GitLabClient, GitLabConfig
from test_schedule_automation import FakeClient, FakeStore
from repository_store import RepositoryConfig


class OtaPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_release_runs_path = server.RELEASE_RUNS_PATH
        self.previous_schedule_runs_path = server.SCHEDULE_RUNS_PATH
        server.RELEASE_RUNS_PATH = Path(self.tmp.name) / "release_runs.json"
        server.SCHEDULE_RUNS_PATH = Path(self.tmp.name) / "schedule_runs.json"
        self.repo = RepositoryConfig(
            id="simos",
            name="simos",
            base_url="https://gitlab.example",
            project="OS/simos",
            token_env="SIMOS_TOKEN",
        )
        self.client = FakeClient()
        self.client.create_pipeline = Mock(return_value={"id": 42, "web_url": "https://gitlab.example/OS/simos/-/pipelines/42"})
        self.app = server.GitOpsApp(FakeStore([self.repo]), server.AuthManager.from_environment())
        self.app.client_for = lambda repo: self.client  # type: ignore[method-assign]
        self.app.token_loaded = lambda repo: True  # type: ignore[method-assign]

    def tearDown(self) -> None:
        server.RELEASE_RUNS_PATH = self.previous_release_runs_path
        server.SCHEDULE_RUNS_PATH = self.previous_schedule_runs_path
        self.tmp.cleanup()

    def test_gitlab_client_creates_api_pipeline_with_multiple_ota_environments(self) -> None:
        client = GitLabClient(GitLabConfig("https://gitlab.example", "OS/simos", "token"))
        client.request = Mock(return_value={"id": 42})  # type: ignore[method-assign]

        result = client.create_pipeline("fix_V3.1.24.021_202608071530", {"SIMOS_OTA_TARGET_ENVS": "dev,test"})

        self.assertEqual(result, {"id": 42})
        client.request.assert_called_once_with(
            "POST",
            "/projects/OS%2Fsimos/pipeline",
            payload={
                "ref": "fix_V3.1.24.021_202608071530",
                "variables": [{"key": "SIMOS_OTA_TARGET_ENVS", "value": "dev,test"}],
            },
        )

    def test_ota_target_environments_default_to_test_and_reject_unknown_or_empty_values(self) -> None:
        self.assertEqual(server.normalize_release_task({"id": "ota-default"})["ota_target_envs"], ["test"])
        self.assertEqual(server.normalize_ota_target_envs(["prod", "dev", "prod"]), ["dev", "prod"])
        with self.assertRaisesRegex(ValueError, "OTA 上传环境"):
            server.normalize_ota_target_envs(["staging"])
        with self.assertRaisesRegex(ValueError, "至少"):
            server.normalize_ota_target_envs([])

    def test_existing_tag_rerun_creates_one_api_pipeline_with_multiple_selected_environments(self) -> None:
        self.app.resident_package = Mock(return_value={"status": "pending_or_missing"})  # type: ignore[method-assign]

        result = self.app.rerun_tag_release(
            {"tag_name": "fix_V3.1.24.021_202608071530", "ota_target_envs": ["dev", "prod"]}
        )

        self.assertTrue(result["ok"])
        self.client.create_pipeline.assert_called_once_with(
            "fix_V3.1.24.021_202608071530", {"SIMOS_OTA_TARGET_ENVS": "dev,prod"}
        )
        self.assertEqual(result["run"]["ota_target_envs"], ["dev", "prod"])
        self.assertEqual(result["run"]["pipeline_url"], "https://gitlab.example/OS/simos/-/pipelines/42")

    def test_regular_tag_for_non_simos_repository_does_not_start_simos_pipeline(self) -> None:
        business_repo = RepositoryConfig(
            id="business",
            name="business",
            base_url="https://gitlab.example",
            project="OS/business",
            token_env="BUSINESS_TOKEN",
        )
        business_client = FakeClient("business")
        self.app.store = FakeStore([self.repo, business_repo])  # type: ignore[assignment]
        self.app.client_for = lambda repo: self.client if repo.id == "simos" else business_client  # type: ignore[method-assign]

        result = self.app.create_tag(
            {
                "scope": "single",
                "repository_id": "business",
                "ref": "fix",
                "tag_name": "fix_V3.1.24.021_202608071530",
            }
        )

        self.assertTrue(result["ok"])
        self.client.create_pipeline.assert_not_called()

    def test_manual_release_and_rerun_forms_use_multi_select_ota_environments(self) -> None:
        index = (server.STATIC_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertEqual(index.count('name="ota_target_envs" multiple'), 3)
        for value in ("dev", "test", "prod"):
            self.assertIn(f'<option value="{value}"', index)


if __name__ == "__main__":
    unittest.main()
