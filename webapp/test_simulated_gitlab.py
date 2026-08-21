from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_client import GitLabError
from repository_store import RepositoryConfig
import auth
import server
from simulated_gitlab import SimulatedGitLabClient


class SimulatedGitLabClientTest(unittest.TestCase):
    def test_simulated_client_persists_branch_file_tag_and_pipeline_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "simulation-state.json"
            client = SimulatedGitLabClient(
                state_path,
                repo_id="simos",
                project="sandbox/simos",
                initial={
                    "branches": {
                        "feature/release_login": {
                            "commit_id": "feature-commit",
                            "files": {"version.info": "Version:V3.1.24.020\n"},
                        }
                    },
                    "tags": [],
                },
            )

            self.assertEqual(client.project()["path_with_namespace"], "sandbox/simos")
            self.assertEqual(client.get_file_text("version.info", "feature/release_login"), "Version:V3.1.24.020\n")
            client.create_branch("automation/test", "feature/release_login")
            commit = client.create_commit(
                "automation/test",
                "update test metadata",
                [{"action": "update", "file_path": "version.info", "content": "Version:T3.1.24.021\n"}],
            )
            tag = client.create_tag("feature-release_login_T3.1.24.021_202608071530", commit["id"], "test")

            reloaded = SimulatedGitLabClient(state_path, repo_id="simos", project="sandbox/simos")
            self.assertEqual(reloaded.get_file_text("version.info", "automation/test"), "Version:T3.1.24.021\n")
            self.assertEqual(reloaded.tag_names(), [tag["name"]])
            self.assertEqual(reloaded.pipelines(ref=tag["name"])[0]["status"], "success")

            with self.assertRaisesRegex(GitLabError, "Tag 已存在"):
                reloaded.create_tag(tag["name"], commit["id"], "duplicate")

    def test_simulated_client_reset_removes_previous_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "simulation-state.json"
            client = SimulatedGitLabClient(state_path, repo_id="simos", project="sandbox/simos")
            client.create_branch("feature/test", "main")
            client.reset()
            self.assertEqual(client.branch_names(), ["main"])
            self.assertEqual(client.tag_names(), [])

            raw = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(raw["repositories"]["simos"]["branches"]["main"]["commit_id"], "main-commit")

    def test_simulation_mode_uses_local_client_and_state_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "simulation-state.json"
            repo = RepositoryConfig("simos", "simos", "https://production.invalid", "OS/simos", True, "main")
            app = server.GitOpsApp(type("Store", (), {"list": lambda self: [repo], "enabled": lambda self: [repo], "get": lambda self, _: repo})(), auth.AuthManager.from_environment())
            with patch.dict(
                os.environ,
                {"GITOPS_MODE": "simulation", "GITOPS_SIMULATION_STATE": str(state_path)},
                clear=False,
            ):
                client = app.client_for(repo)
            self.assertIsInstance(client, SimulatedGitLabClient)
            self.assertEqual(client.state_path, state_path)


if __name__ == "__main__":
    unittest.main()
