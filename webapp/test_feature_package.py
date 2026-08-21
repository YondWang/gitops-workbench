from __future__ import annotations

import unittest
import io
from typing import Any

import auth
import server
from repository_store import RepositoryConfig


VERSION_INFO = """Version:V3.1.24.020
NextVersion:""
PreVersion:V3.1.24.019

simos_commitid:simos-source
business_commitid:business-source

simos_branch:feature/release_login
business_branch:release

Date:2026-07-02 10:00:00
"""


class FeatureStore:
    def __init__(self, repositories: list[RepositoryConfig]) -> None:
        self.repositories = repositories

    def list(self) -> list[RepositoryConfig]:
        return list(self.repositories)

    def enabled(self) -> list[RepositoryConfig]:
        return [repo for repo in self.repositories if repo.enabled]

    def get(self, repo_id: str) -> RepositoryConfig:
        for repo in self.repositories:
            if repo.id == repo_id:
                return repo
        raise ValueError(f"repo not found: {repo_id}")


class FeatureClient:
    def __init__(self, repo_id: str, branches: dict[str, str], tags: list[str] | None = None) -> None:
        self.repo_id = repo_id
        self.branches = dict(branches)
        self.tags = list(tags or [])
        self.calls: list[tuple[str, Any]] = []

    def project(self) -> dict[str, Any]:
        self.calls.append(("project",))
        return {"id": self.repo_id}

    def branch_names(self) -> list[str]:
        self.calls.append(("branch_names",))
        return list(self.branches)

    def tag_names(self) -> list[str]:
        self.calls.append(("tag_names",))
        return list(self.tags)

    def branch(self, name: str) -> dict[str, Any]:
        self.calls.append(("branch", name))
        if name not in self.branches:
            raise server.GitLabError(f"missing branch {name}", status=404, payload={})
        return {"name": name, "commit": {"id": self.branches[name], "parent_ids": []}}

    def get_file_text(self, file_path: str, ref: str) -> str:
        self.calls.append(("get_file_text", file_path, ref))
        if file_path == server.VERSION_INFO_PATH and ref == "feature/release_login" and self.repo_id == "simos":
            return VERSION_INFO
        raise server.GitLabError("missing file", status=404, payload={})

    def create_branch(self, branch: str, ref: str) -> dict[str, Any]:
        self.calls.append(("create_branch", branch, ref))
        self.branches[branch] = f"{self.repo_id}-build-base"
        return {"name": branch, "ref": ref}

    def create_commit(self, branch: str, message: str, actions: list[dict[str, str]]) -> dict[str, Any]:
        self.calls.append(("create_commit", branch, message, actions))
        self.branches[branch] = f"{self.repo_id}-build-commit"
        return {"id": f"{self.repo_id}-build-commit", "short_id": "build"}

    def create_tag(self, tag_name: str, ref: str, message: str = "") -> dict[str, Any]:
        self.calls.append(("create_tag", tag_name, ref, message))
        if tag_name in self.tags:
            raise server.GitLabError("tag exists", status=409, payload={})
        self.tags.append(tag_name)
        return {"name": tag_name, "target": ref}


def make_feature_app() -> tuple[server.GitOpsApp, dict[str, FeatureClient]]:
    repositories = [
        RepositoryConfig("simos", "simos", "https://gitlab.test", "group/simos", True, "main"),
        RepositoryConfig("business", "business", "https://gitlab.test", "group/business", True, "main"),
    ]
    clients = {
        "simos": FeatureClient("simos", {"release": "simos-release", "feature/release_login": "simos-feature"}),
        "business": FeatureClient("business", {"release": "business-release"}),
    }
    app = server.GitOpsApp(FeatureStore(repositories), auth.AuthManager.from_environment())
    app.client_for = lambda repo: clients[repo.id]  # type: ignore[method-assign]
    app.token_loaded = lambda repo: True  # type: ignore[method-assign]
    return app, clients


class FeaturePackageVersionTest(unittest.TestCase):
    def test_feature_package_increments_fourth_part_from_source_when_week_has_no_tags(self) -> None:
        self.assertEqual(
            server.feature_package_version(
                "3.1.24.020",
                [],
                "2026-07-02T16:00:00+08:00",
                force_week_bump=False,
            ),
            "3.1.24.021",
        )

    def test_feature_package_keeps_existing_calculated_fourth_part_when_forcing_week_bump(self) -> None:
        self.assertEqual(
            server.feature_package_version(
                "3.1.24.020",
                [],
                "2026-07-03T16:00:00+08:00",
                force_week_bump=True,
            ),
            "3.1.25.021",
        )

    def test_feature_package_increments_existing_week_fourth_part_without_reset(self) -> None:
        tags = ["feature-release_login_T3.1.24.020_202607021000"]
        self.assertEqual(
            server.feature_package_version(
                "3.1.24.020",
                tags,
                "2026-07-03T16:00:00+08:00",
                force_week_bump=False,
            ),
            "3.1.24.021",
        )

    def test_feature_package_rejects_non_four_part_source_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "四段"):
            server.feature_package_version(
                "3.1.24",
                [],
                "2026-07-03T16:00:00+08:00",
                force_week_bump=False,
            )


class FeaturePackagePermissionTest(unittest.TestCase):
    def test_user_can_create_feature_package_but_not_generic_tag(self) -> None:
        self.assertIn("create_feature_package", auth.ROLE_PERMISSIONS["user"])
        self.assertNotIn("create_tag", auth.ROLE_PERMISSIONS["user"])

    def test_feature_package_preview_is_read_only_and_returns_t_version(self) -> None:
        app, clients = make_feature_app()
        result = app.feature_package_preview(
            {"ref": "feature/release_login", "now": "2026-07-02T16:00:00+08:00", "force_week_bump": False}
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["version"], "T3.1.24.021")
        self.assertTrue(result["tag_name"].startswith("feature-release_login_T3.1.24.021_"))
        self.assertFalse(any(call[0] in {"create_branch", "create_commit", "create_tag"} for client in clients.values() for call in client.calls))


class FeaturePackageOperationTest(unittest.TestCase):
    def test_feature_package_creates_isolated_build_ref_and_t_tags(self) -> None:
        app, clients = make_feature_app()

        result = app.create_feature_package(
            {
                "ref": "feature/release_login",
                "now": "2026-07-02T16:00:00+08:00",
                "force_week_bump": False,
            }
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["version"], "T3.1.24.021")
        self.assertTrue(result["tag_name"].startswith("feature-release_login_T3.1.24.021_"))
        self.assertTrue(any(call[0] == "create_branch" and call[2] == "feature/release_login" for call in clients["simos"].calls))
        self.assertTrue(any(call[0] == "create_commit" for call in clients["simos"].calls))
        self.assertTrue(any(call[0] == "create_tag" for call in clients["simos"].calls))
        self.assertTrue(any(call[0] == "create_tag" for call in clients["business"].calls))
        self.assertFalse(any(call[0] == "update_file" for call in clients["simos"].calls))

    def test_feature_package_rejects_non_feature_before_any_write(self) -> None:
        app, clients = make_feature_app()

        result = app.create_feature_package({"ref": "release", "now": "2026-07-02T16:00:00+08:00"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "precheck")
        self.assertIn("feature/*", result["error"])
        self.assertFalse(any(call[0] in {"create_branch", "create_commit", "create_tag"} for client in clients.values() for call in client.calls))

    def test_feature_package_reports_execute_failure_when_build_commit_fails(self) -> None:
        app, clients = make_feature_app()

        def fail_commit(branch: str, message: str, actions: list[dict[str, str]]) -> dict[str, Any]:
            clients["simos"].calls.append(("create_commit", branch, message, actions))
            raise server.GitLabError("simulated write failure", status=500, payload={})

        clients["simos"].create_commit = fail_commit  # type: ignore[method-assign]
        result = app.create_feature_package({"ref": "feature/release_login", "now": "2026-07-02T16:00:00+08:00"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "execute")
        self.assertIn("simulated write failure", result["error"])
        self.assertFalse(any(call[0] == "create_tag" for client in clients.values() for call in client.calls))


class FeaturePackageRouteTest(unittest.TestCase):
    def test_user_feature_package_route_is_allowed_and_generic_tag_route_is_not(self) -> None:
        app, _ = make_feature_app()
        app.create_feature_package = lambda payload: {"ok": True, "operation": "create_feature_package"}  # type: ignore[method-assign]
        user_token = app.auth.login("user", "user123")["token"]
        handler = object.__new__(server.make_handler(app))
        handler.path = "/api/feature-package/create"
        handler.headers = {"Cookie": server.login_cookie(user_token), "Content-Length": "2"}
        handler.rfile = io.BytesIO(b"{}")
        handler.wfile = io.BytesIO()
        handler.extra_headers = {}
        statuses: list[int] = []
        handler.send_response = lambda status, *args: statuses.append(status)
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None

        handler.do_POST()

        self.assertEqual(statuses, [200])

        handler.path = "/api/tags/create"
        handler.rfile = io.BytesIO(b"{}")
        statuses.clear()
        handler.do_POST()
        self.assertEqual(statuses, [403])

    def test_feature_package_form_contract_exists(self) -> None:
        index = (server.STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        app_js = (server.STATIC_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="featurePackageForm"', index)
        self.assertIn('id="featurePackageForceWeek"', index)
        self.assertIn('id="featurePackageVersionPreview"', index)
        self.assertIn("/api/feature-package/create", app_js)
        self.assertIn("/api/feature-package/preview", app_js)
        self.assertIn("feature_branches", app_js)

    def test_feature_package_rejects_custom_tag_name(self) -> None:
        app, clients = make_feature_app()

        result = app.create_feature_package(
            {"ref": "feature/release_login", "tag_name": "V3.1.24.021", "now": "2026-07-02T16:00:00+08:00"}
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "precheck")
        self.assertIn("不能手动指定", result["error"])
        self.assertFalse(any(call[0] in {"create_branch", "create_commit", "create_tag"} for client in clients.values() for call in client.calls))


if __name__ == "__main__":
    unittest.main()
