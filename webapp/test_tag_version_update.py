from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import server
from repository_store import RepositoryConfig


VERSION_INFO = """Version:1.0.0
NextVersion:""
PreVersion:0.9.9

simos_commitid:simos-old
business_commitid:business-old
localization_commitid:localization-old
perception_commitid:perception-old
mapengine_commitid:mapengine-old
pnc_commitid:pnc-old

simos_branch:release
business_branch:release
localization_branch:release
perception_branch:release
mapengine_branch:release
pnc_branch:release

Date:2026-06-01 10:00:00
"""

SIMOS_CURRENT_VERSION_INFO = VERSION_INFO.replace("simos_commitid:simos-old", "simos_commitid:simos-new")

CURRENT_VERSION_INFO = (
    VERSION_INFO.replace("simos_commitid:simos-old", "simos_commitid:simos-new")
    .replace("business_commitid:business-old", "business_commitid:business-new")
    .replace("localization_commitid:localization-old", "localization_commitid:localization-new")
    .replace("perception_commitid:perception-old", "perception_commitid:perception-new")
    .replace("mapengine_commitid:mapengine-old", "mapengine_commitid:mapengine-new")
    .replace("pnc_commitid:pnc-old", "pnc_commitid:pnc-new")
)


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
    def __init__(
        self,
        repo_id: str,
        *,
        version_info: str | None = None,
        branch_names: list[str] | None = None,
        tag_names: list[str] | None = None,
        call_log: list[tuple[str, tuple[str, Any]]] | None = None,
    ) -> None:
        self.repo_id = repo_id
        self.calls: list[tuple[str, Any]] = []
        self.call_log = call_log
        self.version_info = version_info
        self.pkg_info = "version:1.0.0\n"
        self.software_yaml = """version: "1.0.0"
components:
  main: "1.0.0"
  business: "release"
commits:
  main: "simos-old"
  business: "business-old"
date: "2026-06-01 10:00:00"
"""
        self._branch_names = branch_names or ["release"]
        self._tag_names = tag_names or []
        self.fail_create_commit_status: int | None = None
        self._merge_requests: list[dict[str, Any]] = []
        self.created_branches: set[str] = set()
        self.open_merge_request_state = "opened"
        self.branch_commit = {
            "id": f"{repo_id}-new",
            "short_id": f"{repo_id}-new",
            "title": "fix release issue",
            "parent_ids": [],
        }

    def record(self, call: tuple[str, Any]) -> None:
        self.calls.append(call)
        if self.call_log is not None:
            self.call_log.append((self.repo_id, call))

    def project(self) -> dict[str, Any]:
        return {"id": self.repo_id}

    def branches(self, search: str = "") -> list[dict[str, Any]]:
        self.record(("branches", search))
        return [{"name": name, "commit": {"id": f"{self.repo_id}-{name}"}} for name in self._branch_names]

    def tags(self, search: str = "") -> list[dict[str, Any]]:
        self.record(("tags", search))
        return [{"name": name, "commit": {"id": f"{self.repo_id}-{name}"}} for name in self._tag_names]

    def branch_names(self) -> list[str]:
        self.record(("branch_names",))
        return self._branch_names

    def tag_names(self) -> list[str]:
        self.record(("tag_names",))
        return self._tag_names

    def branch(self, name: str) -> dict[str, Any]:
        self.record(("branch", name))
        return {"name": name, "commit": self.branch_commit}

    def get_file_text(self, file_path: str, ref: str) -> str:
        self.record(("get_file_text", file_path, ref))
        if file_path == "version.info" and self.version_info is not None:
            return self.version_info
        if file_path == "pkg.info":
            return self.pkg_info
        if file_path == "software.yaml":
            return self.software_yaml
        raise server.GitLabError("missing", status=404, payload={})

    def file_exists(self, file_path: str, ref: str) -> bool:
        self.record(("file_exists", file_path, ref))
        return file_path == "version.info" and self.version_info is not None

    def create_commit(self, branch: str, commit_message: str, actions: list[dict[str, str]]) -> dict[str, Any]:
        self.record(("create_commit", branch, commit_message, actions))
        if self.fail_create_commit_status:
            raise server.GitLabError("commit api failed", status=self.fail_create_commit_status, payload={})
        return {"id": f"{self.repo_id}-version-commit", "short_id": "version"}

    def update_file(self, file_path: str, branch: str, content: str, commit_message: str) -> dict[str, Any]:
        self.record(("update_file", file_path, branch, content, commit_message))
        self.branch_commit = {
            "id": f"{self.repo_id}-file-update-commit",
            "short_id": "file-update",
            "title": commit_message,
            "parent_ids": [self.branch_commit["id"]],
        }
        return {"file_path": file_path, "branch": branch}

    def create_tag(self, tag_name: str, ref: str, message: str = "") -> dict[str, Any]:
        self.record(("create_tag", tag_name, ref, message))
        return {"name": tag_name, "target": ref}

    def create_branch(self, branch: str, ref: str) -> dict[str, Any]:
        self.record(("create_branch", branch, ref))
        self.created_branches.add(branch)
        return {"name": branch, "ref": ref}

    def opened_merge_requests(self, source_branch: str, target_branch: str) -> list[dict[str, Any]]:
        self.record(("opened_merge_requests", source_branch, target_branch))
        return [item for item in self._merge_requests if item["source_branch"] == source_branch and item["target_branch"] == target_branch and item.get("state") == "opened"]

    def merge_requests(self, source_branch: str, target_branch: str, state: str = "all") -> list[dict[str, Any]]:
        self.record(("merge_requests", source_branch, target_branch, state))
        if self._merge_requests:
            return [
                item
                for item in self._merge_requests
                if item["source_branch"] == source_branch
                and item["target_branch"] == target_branch
                and (state == "all" or item.get("state") == state)
            ]
        return []

    def create_merge_request(self, source_branch: str, target_branch: str, title: str) -> dict[str, Any]:
        self.record(("create_merge_request", source_branch, target_branch, title))
        existing = self.opened_merge_requests(source_branch, target_branch)
        if existing:
            existing[0]["_reused"] = True
            return existing[0]
        item = {
            "iid": 7,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "state": self.open_merge_request_state,
            "web_url": f"https://gitlab.example/{self.repo_id}/-/merge_requests/7",
        }
        self._merge_requests.append(item)
        return item


class TagVersionUpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.business_repo = RepositoryConfig(
            id="business",
            name="business",
            base_url="https://gitlab.example",
            project="group/business",
            token_env="GITLAB_TOKEN",
        )
        self.workbench_repo = RepositoryConfig(
            id="gitops-workbench",
            name="gitops-workbench",
            base_url="https://gitlab.example",
            project="group/gitops-workbench",
            token_env="GITLAB_TOKEN",
        )
        self.simos_repo = RepositoryConfig(
            id="simos",
            name="simos",
            base_url="https://gitlab.example",
            project="group/simos",
            token_env="GITLAB_TOKEN",
        )
        self.call_log: list[tuple[str, tuple[str, Any]]] = []
        self.business_client = FakeClient("business", version_info=None, call_log=self.call_log)
        self.workbench_client = FakeClient("gitops-workbench", version_info=None, call_log=self.call_log)
        self.simos_client = FakeClient("simos", version_info=VERSION_INFO, call_log=self.call_log)
        self.simos_client.pkg_info = """simos_branch:fix
business_branch:fix
localization_branch:fix
mapengine_branch:fix
perception_branch:fix
pnc_branch:fix
version:1.0.0
"""
        self.app = server.GitOpsApp(
            FakeStore([self.business_repo, self.workbench_repo, self.simos_repo]),
            server.AuthManager.from_environment(),
        )
        self.clients = {
            "business": self.business_client,
            "gitops-workbench": self.workbench_client,
            "simos": self.simos_client,
        }
        self.app.client_for = lambda repo: self.clients[repo.id]  # type: ignore[method-assign]

    def test_update_version_is_only_allowed_for_all_repositories_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "只能在全部启用仓库"):
            self.app.create_tag(
                {
                    "repository_id": "simos",
                    "scope": "single",
                    "ref": "release",
                    "tag_name": "release-20260615100000",
                    "message": "candidate build",
                    "update_version": True,
                }
            )

    def test_bump_version_preserves_last_segment_zero_padding(self) -> None:
        self.assertEqual(server.bump_version("3.1.21.063"), "3.1.21.064")

    def test_version_rule_uses_update_version_component_revisions(self) -> None:
        self.assertEqual(server.version_from_changed_components("3.1.21.063", ["business"]), "3.1.21.064")
        self.assertEqual(server.version_from_changed_components("3.1.22.0", ["simos", "business"], package_version="3.1.22.0"), "3.1.22.02")
        self.assertEqual(server.version_from_changed_components("3.1.22.0", ["mapengine", "perception"]), "3.1.22.024")
        self.assertEqual(
            server.version_from_changed_components(
                "3.1.22.055",
                ["simos", "business", "localization", "mapengine", "pnc"],
                package_version="3.1.22.0",
            ),
            "3.1.22.046",
        )
        self.assertEqual(
            server.version_from_changed_components(
                "3.1.22.055",
                ["simos", "business", "localization", "mapengine", "pnc"],
                '""',
                package_version="3.1.22.0",
            ),
            "3.1.22.046",
        )
        self.assertEqual(
            server.version_from_changed_components(
                "3.1.22.055",
                ["simos", "business", "localization", "mapengine", "pnc"],
                package_version="3.1.23.0",
            ),
            "3.1.23.046",
        )
        self.assertEqual(
            server.version_from_changed_components("3.1.22.0", ["simos", "business", "localization", "mapengine", "perception", "pnc"]),
            "3.1.22.063",
        )

    def test_submodule_path_mapping_skips_simos_main_component(self) -> None:
        refs = {
            "simos": {"ref": "release", "commit_id": "simos-new"},
            "business": {"ref": "release", "commit_id": "business-new"},
            "localization": {"ref": "release", "commit_id": "localization-new"},
        }

        self.assertEqual(
            server.submodule_update_paths(refs),
            {
                "business": "src/business",
                "localization": "src/localization",
            },
        )

    def test_all_repository_tag_creates_simos_version_mr_before_any_tag(self) -> None:
        result = self.app.create_tag(
            {
                "scope": "all",
                "ref": "release",
                "tag_name": "release-20260615100000",
                "message": "candidate build",
                "update_version": True,
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "waiting_version_mr")
        self.assertEqual(result["tag_name"], "release-20260615100000")
        self.assertTrue(result["blocked"])
        business_call_names = [call[0] for call in self.business_client.calls]
        workbench_call_names = [call[0] for call in self.workbench_client.calls]
        simos_call_names = [call[0] for call in self.simos_client.calls]

        self.assertNotIn("get_file_text", business_call_names)
        self.assertNotIn("create_commit", business_call_names)
        self.assertNotIn("get_file_text", workbench_call_names)
        self.assertNotIn("create_commit", workbench_call_names)
        self.assertNotIn("create_tag", business_call_names)
        self.assertNotIn("create_tag", workbench_call_names)
        self.assertNotIn("create_tag", simos_call_names)

        branch_call = next(call for call in self.simos_client.calls if call[0] == "create_branch")
        self.assertEqual(branch_call, ("create_branch", "automation/version-info/release-20260615100000", "release"))
        version_commit = next(call for call in self.simos_client.calls if call[0] == "create_commit")
        self.assertEqual(version_commit[1], "automation/version-info/release-20260615100000")
        self.assertEqual(version_commit[2], "Update version info before tag release-20260615100000")
        action_paths = [action["file_path"] for action in version_commit[3]]
        self.assertEqual(action_paths, ["version.info", "software.yaml"])
        self.assertEqual(
            next(call for call in self.simos_client.calls if call[0] == "create_merge_request"),
            (
                "create_merge_request",
                "automation/version-info/release-20260615100000",
                "release",
                "Update version info before tag release-20260615100000",
            ),
        )

        actions = version_commit[3]
        version_action = next(action for action in actions if action["file_path"] == "version.info")
        self.assertIn("Version:1.0.02", version_action["content"])
        self.assertIn("simos_commitid:simos-new", version_action["content"])
        self.assertIn("business_commitid:business-new", version_action["content"])
        software_action = next(action for action in actions if action["file_path"] == "software.yaml")
        self.assertIn('version: "1.0.02"', software_action["content"])
        self.assertNotIn("pkg.info", result["version_update"]["files"])
        self.assertEqual(result["merge_request"]["state"], "opened")

    def test_all_repository_tag_waits_for_merged_version_mr_then_tags_all_repositories(self) -> None:
        self.simos_client.version_info = CURRENT_VERSION_INFO
        self.simos_client._merge_requests = [
            {
                "iid": 7,
                "source_branch": "automation/version-info/release-20260615100000",
                "target_branch": "release",
                "title": "Update version info before tag release-20260615100000",
                "state": "merged",
                "web_url": "https://gitlab.example/simos/-/merge_requests/7",
            }
        ]
        self.simos_client.branch_commit = {
            "id": "version-head",
            "short_id": "version-head",
            "title": "Merge branch automation/version-info/release-20260615100000",
            "parent_ids": ["simos-new"],
        }

        result = self.app.create_tag(
            {
                "scope": "all",
                "ref": "release",
                "tag_name": "release-20260615100000",
                "message": "candidate build",
                "update_version": True,
                "version_update_branch": "automation/version-info/release-20260615100000",
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["phase"], "execute")
        self.assertNotIn("create_commit", [call[0] for call in self.simos_client.calls])
        business_tag = next(call for call in self.business_client.calls if call[0] == "create_tag")
        workbench_tag = next(call for call in self.workbench_client.calls if call[0] == "create_tag")
        simos_tag = next(call for call in self.simos_client.calls if call[0] == "create_tag")
        self.assertEqual(business_tag, ("create_tag", "release-20260615100000", "release", "candidate build"))
        self.assertEqual(workbench_tag, ("create_tag", "release-20260615100000", "release", "candidate build"))
        self.assertEqual(simos_tag, ("create_tag", "release-20260615100000", "version-head", "candidate build"))

    def test_closed_version_mr_terminates_pending_tag_workflow(self) -> None:
        self.simos_client._merge_requests = [
            {
                "iid": 7,
                "source_branch": "automation/version-info/release-20260615100000",
                "target_branch": "release",
                "title": "Update version info before tag release-20260615100000",
                "state": "closed",
                "web_url": "https://gitlab.example/simos/-/merge_requests/7",
            }
        ]

        result = self.app.create_tag(
            {
                "scope": "all",
                "ref": "release",
                "tag_name": "release-20260615100000",
                "message": "candidate build",
                "update_version": True,
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "version_update_aborted")
        self.assertTrue(result["terminated"])
        self.assertIn("已终止发版或打 Tag 流程", result["message"])
        self.assertNotIn("create_tag", [call[0] for call in self.simos_client.calls])

    def test_version_mr_is_created_when_non_simos_component_changed(self) -> None:
        self.simos_client.version_info = SIMOS_CURRENT_VERSION_INFO

        result = self.app.create_tag(
            {
                "scope": "all",
                "ref": "release",
                "tag_name": "release-20260615100000",
                "message": "candidate build",
                "update_version": True,
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "waiting_version_mr")
        version_commit = next(call for call in self.simos_client.calls if call[0] == "create_commit")
        version_action = next(action for action in version_commit[3] if action["file_path"] == "version.info")
        self.assertIn("Version:1.0.1", version_action["content"])
        self.assertIn("business_commitid:business-new", version_action["content"])

    def test_version_update_falls_back_to_file_api_when_commit_api_returns_500(self) -> None:
        self.skipTest("Direct release update is replaced by version MR flow.")
        self.simos_client.fail_create_commit_status = 500

        result = self.app.create_tag(
            {
                "scope": "all",
                "ref": "release",
                "tag_name": "release-20260615100000",
                "message": "candidate build",
                "update_version": True,
            }
        )

        self.assertTrue(result["ok"])
        simos_call_names = [call[0] for call in self.simos_client.calls]
        self.assertLess(simos_call_names.index("create_commit"), simos_call_names.index("update_file"))
        self.assertLess(simos_call_names.index("update_file"), simos_call_names.index("create_tag"))
        self.assertEqual(
            next(call for call in self.simos_client.calls if call[0] == "create_tag"),
            ("create_tag", "release-20260615100000", "simos-file-update-commit", "candidate build"),
        )

    def test_does_not_bump_again_when_branch_head_is_prior_version_update_commit(self) -> None:
        self.simos_client.version_info = CURRENT_VERSION_INFO
        self.simos_client.branch_commit = {
            "id": "version-head",
            "short_id": "version-head",
            "title": "Update version info before tag release-20260614100000",
            "parent_ids": ["simos-new"],
        }

        result = self.app.create_tag(
            {
                "scope": "all",
                "ref": "release",
                "tag_name": "release-20260615100000",
                "message": "candidate build",
                "update_version": True,
            }
        )

        simos_call_names = [call[0] for call in self.simos_client.calls]
        self.assertNotIn("create_commit", simos_call_names)
        self.assertEqual(
            next(call for call in self.simos_client.calls if call[0] == "create_tag"),
            ("create_tag", "release-20260615100000", "version-head", "candidate build"),
        )
        self.assertTrue(result["ok"])

    def test_present_simos_version_error_does_not_report_simos_as_disabled(self) -> None:
        self.simos_client.version_info = None

        result = self.app.create_tag(
            {
                "scope": "all",
                "ref": "release",
                "tag_name": "release-20260615100000",
                "message": "candidate build",
                "update_version": True,
            }
        )

        self.assertFalse(result["ok"])
        errors = [item.get("error", "") for item in result["precheck"]]
        self.assertTrue(any("version.info 不存在" in error for error in errors))
        self.assertFalse(any("需要启用 simos 仓库" in error for error in errors))

    def test_common_refs_returns_only_refs_available_in_every_enabled_repository(self) -> None:
        self.business_client._branch_names = ["release", "feature/business_only", "bugfix/V1.0.0"]
        self.simos_client._branch_names = ["release", "feature/simos_only", "bugfix/V1.0.0"]
        self.workbench_client._branch_names = ["release", "feature/workbench_only", "bugfix/V1.0.0"]
        self.business_client._tag_names = ["v1", "business-only"]
        self.simos_client._tag_names = ["v1", "simos-only"]
        self.workbench_client._tag_names = ["v1", "workbench-only"]

        result = self.app.common_refs()

        self.assertEqual([item["name"] for item in result["branches"]], ["bugfix/V1.0.0", "release"])
        self.assertEqual([item["name"] for item in result["tags"]], ["v1"])

    def test_render_pkg_info_preserves_existing_branch_lines(self) -> None:
        previous_pkg_info = """simos_branch:fix
business_branch:fix
localization_branch:fix
mapengine_branch:fix
perception_branch:fix
pnc_branch:fix
version:3.1.21.0
"""

        result = server.render_pkg_info("3.1.22.055", previous_pkg_info)

        self.assertEqual(
            result,
            """simos_branch:fix
business_branch:fix
localization_branch:fix
mapengine_branch:fix
perception_branch:fix
pnc_branch:fix
version:3.1.22.0
""",
        )

    def test_git_version_commit_clones_into_empty_child_directory(self) -> None:
        git_client = server.GitLabClient(
            server.GitLabConfig(
                base_url="https://gitlab.example",
                project="group/simos",
                token="secret-token",
            )
        )
        target = server.OperationTarget(self.simos_repo, git_client)
        calls: list[tuple[list[str], str | None]] = []
        clone_target_state: dict[str, Any] = {}

        def fake_run_git(args: list[str], cwd: str | None, env: dict[str, str] | None = None) -> str:
            calls.append((args, cwd))
            if args[0] == "clone":
                clone_target = Path(args[-1])
                clone_target_state["target_name"] = clone_target.name
                clone_target_state["target_exists"] = clone_target.exists()
                clone_target_state["parent_entries"] = sorted(item.name for item in clone_target.parent.iterdir())
            if args[0] == "rev-parse":
                return "abcdef1234567890\n"
            return ""

        self.app.run_git = fake_run_git  # type: ignore[method-assign]

        result = self.app.commit_version_update_with_git(
            target,
            {
                "ref": "release",
                "actions": [
                    {"action": "update", "file_path": "version.info", "content": "Version:1.0.1\n"},
                    {"action": "update", "file_path": "pkg.info", "content": "version:1.0.0\n"},
                    {"action": "update", "file_path": "software.yaml", "content": 'version: "1.0.1"\n'},
                ],
                "component_refs": {
                    "business": {"ref": "release", "commit_id": "business-new"},
                },
            },
            "automation/version-info/release-20260615100000",
            "Update version info before tag release-20260615100000",
        )

        self.assertEqual(result["id"], "abcdef1234567890")
        self.assertEqual(clone_target_state["target_name"], "repo")
        self.assertFalse(clone_target_state["target_exists"])
        self.assertEqual(clone_target_state["parent_entries"], ["git-askpass.sh"])
        working_cwds = [cwd for args, cwd in calls if args[0] != "clone"]
        self.assertTrue(all(cwd and Path(cwd).name == "repo" for cwd in working_cwds))


if __name__ == "__main__":
    unittest.main()
