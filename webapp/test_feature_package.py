from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import auth
import server
from repository_store import RepositoryConfig

VERSION_INFO = '''Version:V3.1.24.020
NextVersion:""
PreVersion:V3.1.24.019

simos_commitid:simos-source
business_commitid:business-source

simos_branch:feature/release_login
business_branch:release

Date:2026-07-02 10:00:00
'''


class FeatureStore:
    def __init__(self, repositories): self.repositories = repositories
    def list(self): return list(self.repositories)
    def enabled(self): return [repo for repo in self.repositories if repo.enabled]
    def get(self, repo_id):
        return next(repo for repo in self.repositories if repo.id == repo_id)


class FeatureClient:
    def __init__(self, repo_id, branches):
        self.repo_id, self.branch_map, self.calls, self.created_pipelines = repo_id, dict(branches), [], []
        self.jobs_by_pipeline, self.artifacts = {}, {}
    def project(self): self.calls.append(("project",)); return {"id": self.repo_id}
    def branch_names(self): self.calls.append(("branch_names",)); return list(self.branch_map)
    def branches(self, search=""):
        return [{"name": name, "commit": {"id": sha}} for name, sha in self.branch_map.items() if not search or search in name]
    def tags(self, search=""): return []
    def branch(self, name):
        self.calls.append(("branch", name))
        if name not in self.branch_map: raise server.GitLabError(f"missing branch {name}", status=404, payload={})
        return {"name": name, "commit": {"id": self.branch_map[name], "parent_ids": []}}
    def get_file_text(self, file_path, ref):
        self.calls.append(("get_file_text", file_path, ref))
        if self.repo_id == "simos" and file_path == server.VERSION_INFO_PATH and ref == "feature/release_login": return VERSION_INFO
        raise server.GitLabError("missing file", status=404, payload={})
    def create_pipeline(self, ref, variables=None):
        self.calls.append(("create_pipeline", ref, variables))
        pipeline = {"id": len(self.created_pipelines) + 1, "ref": ref, "status": "running", "web_url": f"https://gitlab.test/{self.repo_id}/-/pipelines/{len(self.created_pipelines)+1}", "variables": dict(variables or {})}
        self.created_pipelines.append(pipeline)
        return pipeline
    def pipelines(self, ref="", status="", source=""):
        return [item for item in self.created_pipelines if not ref or item["ref"] == ref]
    def pipeline_jobs(self, pipeline_id): return self.jobs_by_pipeline.get(pipeline_id, [])
    def job_artifact_file_text(self, job_id, path): return self.artifacts[(job_id, path)]


def make_feature_app():
    repos = [
        RepositoryConfig("simos", "simos", "https://gitlab.test", "OS/simos", True, "main"),
        RepositoryConfig("business", "business", "https://gitlab.test", "group/business", True, "main", submodule_path="src/business"),
        RepositoryConfig("gitops-workbench", "workbench", "https://gitlab.test", "software_hmi_app/gitops-control", False, "main"),
    ]
    clients = {
        "simos": FeatureClient("simos", {"release": "simos-release-abcdef", "feature/release_login": "simos-feature-abcdef"}),
        "business": FeatureClient("business", {"release": "business-release-abcdef", "feature/release_login": "business-feature-abcdef"}),
        "gitops-workbench": FeatureClient("gitops-workbench", {"ci/feature-package": "trusted-ci-abcdef"}),
    }
    config = {**server.DEFAULT_CONFIG, "feature_package_ci": {"repository_id": "gitops-workbench", "ref": "ci/feature-package", "registry_repository_id": "simos"}}
    app = server.GitOpsApp(FeatureStore(repos), auth.AuthManager.from_environment(), config)
    app.client_for = lambda repo: clients[repo.id]  # type: ignore[method-assign]
    app.token_loaded = lambda repo: True  # type: ignore[method-assign]
    return app, clients


class FeaturePackageTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.runs_path = server.FEATURE_PACKAGE_RUNS_PATH
        server.FEATURE_PACKAGE_RUNS_PATH = Path(self.tmpdir.name) / "feature-runs.json"
        self.env = patch.dict(os.environ, {"GITOPS_FEATURE_CONTEXT_HMAC_KEY": "test-feature-key"})
        self.env.start()
    def tearDown(self):
        self.env.stop(); server.FEATURE_PACKAGE_RUNS_PATH = self.runs_path; self.tmpdir.cleanup()

    @staticmethod
    def package_payload(**overrides):
        return {
            "ref": "feature/release_login",
            "baseline_ref": "release",
            "cloud_category": "车机/Feature测试包",
            "repository_ids": ["simos", "business"],
            **overrides,
        }

    def test_user_permission_remains_feature_only(self):
        self.assertIn("create_feature", auth.ROLE_PERMISSIONS["user"])
        self.assertIn("create_feature_package", auth.ROLE_PERMISSIONS["user"])
        self.assertNotIn("create_tag", auth.ROLE_PERMISSIONS["user"])

    def test_existing_repository_store_is_migrated_with_internal_ci_target(self):
        config = {**server.DEFAULT_CONFIG}
        with tempfile.TemporaryDirectory() as directory:
            store = server.RepositoryStore(Path(directory) / "repositories.json", [])
            for repository in server.default_repositories(config):
                if repository.id != "gitops-workbench":
                    store.add(asdict(repository))

            server.ensure_feature_package_ci_repository(store, config)

            trusted = store.get("gitops-workbench")
            self.assertEqual(trusted.project, "software_hmi_app/gitops-control")
            self.assertFalse(trusted.enabled)

    def test_preview_uses_timestamp_t_version_without_write(self):
        app, clients = make_feature_app()
        result = app.feature_package_preview(self.package_payload())
        self.assertTrue(result["ok"], result)
        self.assertRegex(result["version"], r"^T\d{14}_login$")
        self.assertEqual(result["source_version"], "V3.1.24.020")
        self.assertFalse(any(call[0].startswith("create_") for client in clients.values() for call in client.calls))

    def test_start_only_invokes_trusted_workbench_pipeline(self):
        app, clients = make_feature_app()
        result = app.create_feature_package(self.package_payload(_actor="user"))
        self.assertTrue(result["ok"], result)
        self.assertRegex(result["version"], r"^T\d{14}_login$")
        call = clients["gitops-workbench"].calls[-1]
        self.assertEqual(call[:2], ("create_pipeline", "ci/feature-package"))
        variables = call[2]
        self.assertEqual(set(variables), {"GITOPS_FEATURE_PACKAGE", "GITOPS_FEATURE_CONTEXT_B64", "GITOPS_FEATURE_CONTEXT_HMAC"})
        context = json.loads(base64.urlsafe_b64decode(variables["GITOPS_FEATURE_CONTEXT_B64"]).decode())
        self.assertEqual(context["operator"], "user")
        self.assertEqual(context["source"]["sha"], "simos-feature-abcdef")
        self.assertEqual({item["repo"] for item in context["components"]}, {"simos", "business"})
        self.assertIn(result["version"], context["metadata"]["version_info"])
        self.assertIn(result["version"], context["metadata"]["software_yaml"])
        for repo_id in ("simos", "business"):
                self.assertFalse(any(call[0] in {"create_branch", "create_commit", "create_tag", "create_pipeline"} for call in clients[repo_id].calls))
        self.assertEqual(app.feature_package_runs()["runs"][0]["operator"], "user")

    def test_feature_pipeline_never_receives_ota_variables_for_any_allowed_actor(self):
        for actor in ("user", "admin"):
            app, clients = make_feature_app()

            result = app.create_feature_package(self.package_payload(_actor=actor))

            self.assertTrue(result["ok"], result)
            variables = clients["gitops-workbench"].calls[-1][2]
            self.assertFalse(any(name.startswith("SIMOS_OTA") for name in variables))

    def test_blank_baseline_requires_all_component_feature_branches(self):
        app, clients = make_feature_app(); clients["business"].branch_map.pop("feature/release_login")
        result = app.create_feature_package(self.package_payload(baseline_ref=""))
        self.assertFalse(result["ok"]); self.assertIn("不存在 Feature 分支", result["error"])
        self.assertFalse(clients["gitops-workbench"].created_pipelines)

    def test_rejects_non_feature_forged_fields_and_bad_category(self):
        app, clients = make_feature_app()
        invalid = app.create_feature_package(self.package_payload(ref="release"))
        forged = app.create_feature_package(self.package_payload(version="T1_login"))
        category = app.feature_package_preview(self.package_payload(cloud_category="任意目录"))
        self.assertFalse(invalid["ok"]); self.assertIn("feature/*", invalid["error"])
        self.assertFalse(forged["ok"]); self.assertIn("不接受客户端参数", forged["error"])
        self.assertFalse(category["ok"]); self.assertFalse(clients["gitops-workbench"].created_pipelines)

    def test_requires_explicit_selected_repositories_including_simos(self):
        app, clients = make_feature_app()
        missing = app.create_feature_package({
            "ref": "feature/release_login",
            "cloud_category": "车机/Feature测试包",
        })
        without_simos = app.create_feature_package(self.package_payload(repository_ids=["business"]))

        self.assertFalse(missing["ok"]); self.assertIn("repository_ids", missing["error"])
        self.assertFalse(without_simos["ok"]); self.assertIn("simos", without_simos["error"])
        self.assertFalse(clients["gitops-workbench"].created_pipelines)

    def test_selected_repositories_define_common_source_and_signed_snapshot(self):
        app, clients = make_feature_app()
        clients["business"].branch_map.pop("feature/release_login")

        common = app.common_refs(["simos", "business"])
        only_simos = app.create_feature_package(self.package_payload(repository_ids=["simos"]))

        self.assertNotIn("feature/release_login", [item["name"] for item in common["feature_branches"]])
        self.assertTrue(only_simos["ok"], only_simos)
        variables = clients["gitops-workbench"].calls[-1][2]
        context = json.loads(base64.urlsafe_b64decode(variables["GITOPS_FEATURE_CONTEXT_B64"]).decode())
        self.assertEqual([item["repo"] for item in context["components"]], ["simos"])
        self.assertEqual(only_simos["run"]["repository_ids"], ["simos"])

    def test_collision_allocation_and_ui_ci_contract(self):
        now = server.datetime(2026, 8, 27, 15, 30, 45, tzinfo=server.ZoneInfo("Asia/Shanghai"))
        first = server.allocate_feature_package_build_id("feature/release_login", now, [])
        self.assertEqual(first, "T20260827153045_login")
        self.assertEqual(server.allocate_feature_package_build_id("feature/release_login", now, [{"version": first}]), "T20260827153046_login")
        index = (server.STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        app_js = (server.STATIC_ROOT / "app.js").read_text(encoding="utf-8")
        ci = (server.ROOT.parent / ".gitlab" / "ci" / "feature-package.yml").read_text(encoding="utf-8")
        verifier = (server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-validate.py").read_text(encoding="utf-8")
        self.assertIn('name="baseline_ref"', index); self.assertIn('id="featurePackageCloudCategory"', index)
        self.assertIn('id="featurePackageRepositories"', index); self.assertIn("feature-operations-grid", index)
        self.assertNotIn("featurePackageForceWeek", index); self.assertIn("/api/feature-package/runs", app_js)
        self.assertIn('["#featurePackageForm", "#featurePackageRef", "feature_branches", ""]', app_js)
        self.assertNotIn('fillSelect("#featurePackageRef", (state.commonRefs', app_js)
        self.assertIn("error: result.error", app_js)
        self.assertIn("GITOPS_FEATURE_CONTEXT_HMAC", verifier)
        self.assertNotIn("upload-ota", ci)
        self.assertNotIn("release-note", ci)
        self.assertNotIn('simos-cloud-publisher-222', ci)
        self.assertEqual(ci.count('tags: ["simos-feature-build"]'), 3)
        self.assertEqual(ci.count('tags: ["simos-feature-publisher"]'), 2)
        self.assertNotIn("GIT_STRATEGY: none", ci)
        self.assertEqual(ci.count("GIT_STRATEGY: fetch"), 5)
        self.assertNotIn("stage: upload", ci)
        self.assertNotIn("SIMOS_OTA", ci)
        prepare = (server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-prepare.sh").read_text(encoding="utf-8")
        self.assertIn('"submodule", "update", "--init", "--recursive"', prepare)

    def test_pipeline_result_is_persisted_only_after_trusted_publish_artifact(self):
        app, clients = make_feature_app()
        started = app.create_feature_package(self.package_payload())
        pipeline = clients["gitops-workbench"].created_pipelines[0]
        pipeline["status"] = "success"
        clients["gitops-workbench"].jobs_by_pipeline[pipeline["id"]] = [{"id": 41, "name": "feature_publish_nextcloud"}]
        clients["gitops-workbench"].artifacts[(41, "feature-package-result.json")] = json.dumps({"status": "success", "registry": {"package_version": started["version"]}, "nextcloud": {"cloud_dir": "车机/Feature测试包/x"}})

        run = app.feature_package_runs()["runs"][0]

        self.assertEqual(run["status"], "success")
        self.assertEqual(run["registry"]["package_version"], started["version"])
        self.assertEqual(run["nextcloud"]["cloud_dir"], "车机/Feature测试包/x")

    def test_success_pipeline_without_publish_artifact_is_recorded_as_failure(self):
        app, clients = make_feature_app()
        app.create_feature_package(self.package_payload())
        clients["gitops-workbench"].created_pipelines[0]["status"] = "success"

        run = app.feature_package_runs()["runs"][0]

        self.assertEqual(run["status"], "failed")
        self.assertIn("feature-package-result.json", run["error"])

    def test_failed_pipeline_is_persisted_without_reading_publish_artifact(self):
        app, clients = make_feature_app()
        app.create_feature_package(self.package_payload())
        clients["gitops-workbench"].created_pipelines[0]["status"] = "failed"

        run = app.feature_package_runs()["runs"][0]

        self.assertEqual(run["status"], "failed")
        self.assertIn("可信 Feature Pipeline", run["error"])

    def test_validator_rejects_missing_tampered_and_expired_contexts(self):
        script = server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-validate.py"
        context = {
            "schema": 1,
            "run_id": "feature-test",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "build_id": "T20260827153045_login",
            "source": {"repository_id": "simos", "project": "OS/simos", "ref": "feature/release_login", "sha": "a" * 40},
            "cloud_category": "车机/Feature测试包",
            "components": [{"repo": "simos", "project": "OS/simos", "submodule_path": "", "ref": "feature/release_login", "sha": "a" * 40}],
            "metadata": {"version_info": "Version:T20260827153045_login\n", "software_yaml": "version: T20260827153045_login\n"},
        }
        encoded = base64.urlsafe_b64encode(json.dumps(context).encode()).decode()
        signature = hmac.new(b"test-feature-key", encoded.encode(), hashlib.sha256).hexdigest()
        environment = {**os.environ, "GITOPS_FEATURE_PACKAGE": "1", "CI_PIPELINE_SOURCE": "api", "CI_COMMIT_REF_NAME": "ci/feature-package", "GITOPS_FEATURE_CONTEXT_HMAC_KEY": "test-feature-key", "GITOPS_FEATURE_CLOUD_CATEGORIES": "车机/Feature测试包", "GITOPS_FEATURE_CONTEXT_B64": encoded}

        valid = subprocess.run([sys.executable, str(script)], cwd=self.tmpdir.name, env={**environment, "GITOPS_FEATURE_CONTEXT_HMAC": signature}, capture_output=True, text=True)
        missing = subprocess.run([sys.executable, str(script)], cwd=self.tmpdir.name, env=environment, capture_output=True, text=True)
        tampered = subprocess.run([sys.executable, str(script)], cwd=self.tmpdir.name, env={**environment, "GITOPS_FEATURE_CONTEXT_HMAC": "0" * len(signature)}, capture_output=True, text=True)
        expired_context = {**context, "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
        expired_encoded = base64.urlsafe_b64encode(json.dumps(expired_context).encode()).decode()
        expired_signature = hmac.new(b"test-feature-key", expired_encoded.encode(), hashlib.sha256).hexdigest()
        expired = subprocess.run([sys.executable, str(script)], cwd=self.tmpdir.name, env={**environment, "GITOPS_FEATURE_CONTEXT_B64": expired_encoded, "GITOPS_FEATURE_CONTEXT_HMAC": expired_signature}, capture_output=True, text=True)

        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertNotEqual(missing.returncode, 0); self.assertIn("signature context", missing.stderr)
        self.assertNotEqual(tampered.returncode, 0); self.assertIn("HMAC mismatch", tampered.stderr)
        self.assertNotEqual(expired.returncode, 0); self.assertIn("context expired", expired.stderr)

    def test_user_route_is_allowed_and_generic_tag_route_is_not(self):
        app, _ = make_feature_app(); app.create_feature_package = lambda payload: {"ok": True}  # type: ignore[method-assign]
        token = app.auth.login("user", "user123")["token"]
        handler = object.__new__(server.make_handler(app)); handler.path = "/api/feature-package/create"
        handler.headers = {"Cookie": server.login_cookie(token), "Content-Length": "2"}; handler.rfile = io.BytesIO(b"{}"); handler.wfile = io.BytesIO(); handler.extra_headers = {}
        statuses = []; handler.send_response = lambda status, *args: statuses.append(status); handler.send_header = lambda *args: None; handler.end_headers = lambda: None
        handler.do_POST(); self.assertEqual(statuses, [200])
        handler.path = "/api/tags/create"; handler.rfile = io.BytesIO(b"{}"); statuses.clear(); handler.do_POST(); self.assertEqual(statuses, [403])


if __name__ == "__main__": unittest.main()
