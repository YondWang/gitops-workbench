from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import server
from repository_store import RepositoryConfig, RepositoryStore
from simulated_gitlab import SimulatedGitLabClient


class FeatureCreationTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        repositories = [
            RepositoryConfig(repo_id, repo_id, "https://gitlab.invalid", project)
            for repo_id, project in (
                ("simos", "OS/simos"),
                ("business", "group/business"),
                ("config", "OS/config"),
                ("gitops-workbench", "group/gitops-workbench"),
            )
        ]
        store = RepositoryStore(root / "repositories.json", repositories)
        self.clients = {
            repo.id: SimulatedGitLabClient(
                root / "gitlab.json",
                repo_id=repo.id,
                project=repo.project,
                initial={
                    "branches": {
                        ref: {"commit_id": f"{repo.id}-{ref}", "files": {}}
                        for ref in (
                            "release", "fix", "bugfix/V1.0", "feature/release_login",
                            f"feature/{repo.id}_only",
                        )
                    },
                },
            )
            for repo in repositories
        }
        self.app = server.GitOpsApp(store, server.AuthManager.from_environment())
        self.app.client_for = lambda repo: self.clients[repo.id]

    def test_config_can_create_feature_without_simos(self) -> None:
        result = self.app.create_feature({
            "repository_ids": ["config"], "ref": "release", "desc": "payment",
        })

        self.assertTrue(result["ok"], result)
        self.assertEqual([item["repository"]["id"] for item in result["results"]], ["config"])
        self.assertEqual(
            self.clients["config"].branch("feature/release_payment")["commit"]["id"],
            "config-release",
        )
        self.assertNotIn("feature/release_payment", self.clients["simos"].branch_names())

    def test_create_from_feature_uses_each_repositories_source_and_existing_naming(self) -> None:
        for ticket, expected in (
            ("", "feature/feature-release_login_payment"),
            ("TASK-123", "feature/feature-release_login_TASK-123_payment"),
        ):
            with self.subTest(ticket=ticket):
                result = self.app.create_feature({
                    "repository_ids": ["simos", "business", "config"],
                    "ref": "feature/release_login", "desc": "payment", "ticket": ticket,
                })

                self.assertTrue(result["ok"], result)
                for repo_id in ("simos", "business", "config"):
                    self.assertEqual(
                        self.clients[repo_id].branch(expected)["commit"]["id"],
                        f"{repo_id}-feature/release_login",
                    )
                self.assertNotIn(expected, self.clients["gitops-workbench"].branch_names())

    def test_feature_sources_include_only_supported_branches_common_to_selection(self) -> None:
        self.clients["simos"].create_branch("feature/code_only", "release")
        self.clients["business"].create_branch("feature/code_only", "release")

        with_config = self.app.common_refs(["simos", "business", "config"])
        self.assertEqual(
            {item["name"] for item in with_config["feature_sources"]},
            {"release", "fix", "bugfix/V1.0", "feature/release_login"},
        )
        without_config = self.app.common_refs(["simos", "business"])
        self.assertIn("feature/code_only", {item["name"] for item in without_config["feature_sources"]})
        config_only = self.app.common_refs(["config"])
        self.assertIn("feature/config_only", {item["name"] for item in config_only["feature_sources"]})

    def test_missing_feature_source_prevents_creating_in_any_selected_repository(self) -> None:
        result = self.app.create_feature({
            "repository_ids": ["business", "config"],
            "ref": "feature/business_only", "desc": "payment",
        })

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "precheck")
        self.assertEqual(result["results"], [])
        for repo_id in ("business", "config"):
            self.assertNotIn("feature/feature-business_only_payment", self.clients[repo_id].branch_names())

    def test_existing_target_prevents_creating_in_any_selected_repository(self) -> None:
        branch = "feature/feature-release_login_payment"
        self.clients["config"].create_branch(branch, "release")

        result = self.app.create_feature({
            "repository_ids": ["business", "config"],
            "ref": "feature/release_login", "desc": "payment",
        })

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "precheck")
        self.assertNotIn(branch, self.clients["business"].branch_names())
        self.assertEqual(self.clients["config"].branch(branch)["commit"]["id"], "config-release")

    def test_config_remains_unavailable_to_other_write_operations(self) -> None:
        for operation, payload in (
            (self.app.create_release, {"ref": "main"}),
            (self.app.create_bugfix, {"ref": "release", "version": "V1.0"}),
            (self.app.create_tag, {"ref": "release", "tag_name": "candidate"}),
            (self.app.delete_tags, {"tags": ["candidate"]}),
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, "不能作为业务写入目标"):
                    operation({"repository_ids": ["config"], **payload})

    def test_feature_creation_rejects_ci_repository_and_disabled_config(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能作为业务写入目标"):
            self.app.create_feature({"repository_ids": ["gitops-workbench"], "desc": "payment"})
        self.app.store.update("config", {"enabled": False})
        with self.assertRaisesRegex(ValueError, "仓库未启用"):
            self.app.create_feature({"repository_ids": ["config"], "desc": "payment"})

    def test_unsupported_source_is_still_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.app.create_feature({"repository_ids": ["config"], "ref": "main", "desc": "payment"})


if __name__ == "__main__":
    unittest.main()
