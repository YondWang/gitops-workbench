from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
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

GITMODULES = '''[submodule "src/business"]
    path = src/business
    url = ssh://git@www.chancee-shanghai.cn:22222/group/business.git
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
        if self.repo_id == "simos" and file_path == ".gitmodules" and ref in {"feature/release_login", "simos-feature-abcdef"}: return GITMODULES
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
    config = {**server.DEFAULT_CONFIG, "feature_package_ci": {"repository_id": "gitops-workbench", "ref": "ci/feature-package", "registry_repository_id": "gitops-workbench"}}
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

    def test_feature_submodule_paths_come_from_simos_gitmodules(self):
        paths = server.simos_submodule_paths('''[submodule "src/mapengine"]
            path = src/mapengine
            url = ssh://git@www.chancee-shanghai.cn:22222/mapengine/mapengine.git
        [submodule "src/pnc"]
            path = src/pnc
            url = https://www.chancee-shanghai.cn:9900/pnc/pnc.git
        [submodule "src/localization"]
            path = src/localization
            url = git@www.chancee-shanghai.cn:slam/localization.git
        ''')
        self.assertEqual(paths, {
            "mapengine/mapengine": "src/mapengine",
            "pnc/pnc": "src/pnc",
            "slam/localization": "src/localization",
        })

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
        self.assertEqual(context["schema"], 3)
        self.assertNotIn("config_source", context)
        self.assertEqual(context["operator"], "user")
        self.assertEqual(context["source"]["sha"], "simos-feature-abcdef")
        self.assertEqual({item["repo"] for item in context["components"]}, {"simos", "business"})
        self.assertIn(result["version"], context["metadata"]["version_info"])
        self.assertIn(result["version"], context["metadata"]["software_yaml"])
        for repo_id in ("simos", "business"):
                self.assertFalse(any(call[0] in {"create_branch", "create_commit", "create_tag", "create_pipeline"} for call in clients[repo_id].calls))
        persisted_run = app.feature_package_runs()["runs"][0]
        self.assertEqual(persisted_run["operator"], "user")
        self.assertNotIn("config_source", persisted_run)
        self.assertNotIn("config_source", result["run"])

    def test_rejects_client_controlled_config_fields_without_creating_pipeline(self):
        for field in ("config_source", "config_ref", "config_sha", "SIMOS_CONFIG_REF", "pipeline_variables"):
            app, clients = make_feature_app()

            result = app.create_feature_package(self.package_payload(**{field: "forged"}))

            self.assertFalse(result["ok"])
            self.assertIn(field, result["error"])
            self.assertFalse(clients["gitops-workbench"].created_pipelines)

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

    def test_feature_build_wrapper_clears_t_id_before_formal_release_gate(self):
        wrapper = server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-build.sh"
        workspace = Path(self.tmpdir.name) / "release-tag-gate"
        source = workspace / "feature-source"
        output = workspace / "feature-output"
        gate = source / "ci" / "resident" / "check-release-version.sh"
        entrypoint = source / "ci" / "resident" / "ci-build-resident.sh"
        gate.parent.mkdir(parents=True)
        (workspace / "feature-context.json").write_text(json.dumps({
            "schema": 3,
            "build_id": "T20260831183045_login",
            "components": [{"repository_id": "simos"}],
        }), encoding="utf-8")
        gate.write_text(r'''#!/usr/bin/env bash
set -Eeuo pipefail
tag=${CI_COMMIT_TAG:-}
[[ -n "$tag" ]] || exit 0
if [[ ! "$tag" =~ _([VvFfTt]?[0-9]+([.][0-9]+)+)_[0-9]{12}$ ]]; then
  echo "cannot infer release version from tag: $tag" >&2
  exit 12
fi
''', encoding="utf-8")
        entrypoint.write_text(r'''#!/usr/bin/env bash
set -euo pipefail
bash "$CI_PROJECT_DIR/ci/resident/check-release-version.sh"
printf '%s' "${CI_COMMIT_TAG-unset}" > "$CI_PROJECT_DIR/formal-entrypoint-tag.txt"
mkdir -p "$CI_PROJECT_DIR/resident-packages" "$CI_PROJECT_DIR/resident-package-info"
printf 'resident' > "$CI_PROJECT_DIR/resident-packages/resident.tar.gz"
printf '{"status":"skipped","files":[]}\n' > "$CI_PROJECT_DIR/package-registry-result.json"
''', encoding="utf-8")

        rejected = subprocess.run(
            ["bash", str(gate)],
            env={**os.environ, "CI_COMMIT_TAG": "T20260831183045_login"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(rejected.returncode, 12, rejected)
        self.assertIn("cannot infer release version", rejected.stderr)

        command = ["bash", str(wrapper), "resident", "feature-source", "feature-output"]
        environment = {
            **os.environ,
            "KUBERNETES_SERVICE_HOST": "test",
            "CI_COMMIT_TAG": "formal_parent_tag_must_not_leak",
            "SIMOS_MATRIX_CONFIG_REF": "SIMBOT_R6_A",
            "SIMOS_MATRIX_CONFIG_LABEL": "360",
        }
        for blocked in (
            "GITOPS_FEATURE_REGISTRY_TOKEN",
            "GITOPS_FEATURE_NEXTCLOUD_PASSWORD",
            "SIMOS_OTA_SECRET_KEY",
            "SIMOS_OTA_APP_KEY",
        ):
            environment.pop(blocked, None)
        if Path("/var/run/docker.sock").is_socket():
            bubblewrap = shutil.which("bwrap")
            if not bubblewrap:
                self.skipTest("bwrap is required to isolate the host Docker socket")
            command = [
                bubblewrap,
                "--ro-bind", "/", "/",
                "--dev", "/dev",
                "--proc", "/proc",
                "--bind", str(workspace), str(workspace),
                "--tmpfs", "/run",
                "--chdir", str(workspace),
                *command,
            ]

        result = subprocess.run(command, cwd=workspace, env=environment, capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((source / "formal-entrypoint-tag.txt").read_text(encoding="utf-8"), "")
        manifest = json.loads((output / "resident" / "package-registry-result.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("tag", ""), "")

    def test_feature_build_wrapper_uses_formal_entrypoints(self):
        wrapper = server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-build.sh"
        wrapper_text = wrapper.read_text(encoding="utf-8")
        self.assertIn('CI_PROJECT_DIR="$source_dir"', wrapper_text)
        self.assertEqual(wrapper_text.count('CI_COMMIT_TAG=""'), 2)
        self.assertNotIn('CI_COMMIT_TAG="$build_id"', wrapper_text)
        self.assertIn('ci/resident/ci-build-resident.sh', wrapper_text)
        self.assertIn('ci/deb/ci-build-debs.sh', wrapper_text)
        self.assertIn('child_status=$?', wrapper_text)
        self.assertIn('preserved available diagnostics', wrapper_text)
        for setting in (
            'SIMOS_PACKAGE_REGISTRY_UPLOAD_ENABLED=false',
            'SIMOS_PACKAGE_REGISTRY_UPLOAD_REQUIRED=false',
            'SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_ENABLED=false',
            'SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_REQUIRED=false',
        ):
            self.assertIn(setting, wrapper_text)
        self.assertNotIn("build_all_debs.sh", wrapper_text)
        self.assertNotIn('find "$source_dir"', wrapper_text)

        workspace = Path(self.tmpdir.name) / "build-wrapper"
        source = workspace / "feature-source"
        output = workspace / "feature-output"
        source_argument = Path("feature-source")
        output_argument = Path("feature-output")
        resident_script = source / "ci" / "resident" / "ci-build-resident.sh"
        deb_script = source / "ci" / "deb" / "ci-build-debs.sh"
        legacy_script = source / "build_all_debs.sh"
        resident_script.parent.mkdir(parents=True)
        deb_script.parent.mkdir(parents=True)
        context = {
            "schema": 3,
            "build_id": "T20260831183045_login",
            "components": [{"repository_id": "simos"}],
        }
        (workspace / "feature-context.json").write_text(json.dumps(context), encoding="utf-8")
        resident_script.write_text(r'''#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json
import os
from pathlib import Path
keys = (
    "CI_PROJECT_DIR", "CI_COMMIT_TAG", "SIMOS_MATRIX_CONFIG_REF",
    "SIMOS_MATRIX_CONFIG_LABEL", "SIMOS_PACKAGE_REGISTRY_NAME",
    "SIMOS_PACKAGE_REGISTRY_UPLOAD_ENABLED", "SIMOS_PACKAGE_REGISTRY_UPLOAD_REQUIRED",
    "SIMOS_BUILD_IMAGE",
)
Path(os.environ["CI_PROJECT_DIR"], "resident-invocation.json").write_text(
    json.dumps({key: os.environ.get(key) for key in keys}), encoding="utf-8"
)
PY
mkdir -p "$CI_PROJECT_DIR/resident-packages/360/nested" "$CI_PROJECT_DIR/resident-package-info"
printf 'resident' > "$CI_PROJECT_DIR/resident-packages/360/nested/resident.tar.gz"
if [[ "${FAKE_SKIP_MANIFEST:-false}" != true ]]; then
  printf '{"status":"skipped","tag":"%s","config_ref":"%s","files":[]}\n' "$CI_COMMIT_TAG" "${SIMOS_MATRIX_CONFIG_REF:-}" > "$CI_PROJECT_DIR/package-registry-result.json"
fi
printf '{}' > "$CI_PROJECT_DIR/resident-package-info/build-info.json"
printf '{}' > "$CI_PROJECT_DIR/build-info.json"
printf 'sha256  resident-packages/360/nested/resident.tar.gz\n' > "$CI_PROJECT_DIR/checksums.txt"
printf 'md5  resident-packages/360/nested/resident.tar.gz\n' > "$CI_PROJECT_DIR/checksum.md5"
printf 'SIMOS_CONFIG_REF=%s\n' "${SIMOS_MATRIX_CONFIG_REF:-}" > "$CI_PROJECT_DIR/config-build-info.env"
printf 'must not be copied' > "$CI_PROJECT_DIR/unlisted-output.zip"
[[ "${FAKE_FAIL_AFTER_OUTPUT:-false}" != true ]] || exit 42
''', encoding="utf-8")
        deb_script.write_text(r'''#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json
import os
from pathlib import Path
keys = (
    "CI_PROJECT_DIR", "CI_COMMIT_TAG", "SIMOS_MATRIX_CONFIG_REF",
    "SIMOS_MATRIX_CONFIG_LABEL", "SIMOS_DEB_PACKAGE_REGISTRY_NAME",
    "SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_ENABLED", "SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_REQUIRED",
    "SIMOS_DEB_BUILD_IMAGE", "SIMOS_DEB_BUILD_MODE", "SIMOS_DEB_BUILD_JOBS",
)
Path(os.environ["CI_PROJECT_DIR"], "deb-invocation.json").write_text(
    json.dumps({key: os.environ.get(key) for key in keys}), encoding="utf-8"
)
PY
mkdir -p "$CI_PROJECT_DIR/deb-packages/360/nested" "$CI_PROJECT_DIR/deb-package-info"
printf 'deb' > "$CI_PROJECT_DIR/deb-packages/360/nested/app.deb"
mkdir -p "$CI_PROJECT_DIR/deb_packages"
printf 'single-config-deb' > "$CI_PROJECT_DIR/deb_packages/simos_1.0_arm64.deb"
if [[ "${FAKE_SKIP_MANIFEST:-false}" != true ]]; then
  printf '{"status":"skipped","tag":"%s","config_ref":"%s","files":[]}\n' "$CI_COMMIT_TAG" "${SIMOS_MATRIX_CONFIG_REF:-}" > "$CI_PROJECT_DIR/deb-package-registry-result.json"
fi
printf '{}' > "$CI_PROJECT_DIR/deb-package-info/build-info.json"
printf 'SIMOS_CONFIG_REF=%s\n' "${SIMOS_MATRIX_CONFIG_REF:-}" > "$CI_PROJECT_DIR/config-build-info.env"
printf 'vehicle' > "$CI_PROJECT_DIR/vehicle.info"
printf 'must not be copied' > "$CI_PROJECT_DIR/unlisted-output.deb"
[[ "${FAKE_FAIL_AFTER_OUTPUT:-false}" != true ]] || exit 43
''', encoding="utf-8")
        legacy_script.write_text(r'''#!/usr/bin/env bash
set -euo pipefail
touch "$PWD/build-all-invoked"
''', encoding="utf-8")

        def invoke(*arguments, config_ref="SIMBOT_R6_A", config_label="360", omit_manifest=False, fail_after_output=False):
            command = ["bash", str(wrapper), *map(str, arguments)]
            environment = {
                **os.environ,
                "KUBERNETES_SERVICE_HOST": "test",
                "SIMOS_MATRIX_CONFIG_REF": config_ref,
                "SIMOS_MATRIX_CONFIG_LABEL": config_label,
                "SIMOS_BUILD_IMAGE": "resident-image:test",
                "SIMOS_DEB_BUILD_IMAGE": "deb-image:test",
                "SIMOS_DEB_BUILD_MODE": "all",
                "SIMOS_DEB_BUILD_JOBS": "16",
                "FAKE_SKIP_MANIFEST": "true" if omit_manifest else "false",
                "FAKE_FAIL_AFTER_OUTPUT": "true" if fail_after_output else "false",
            }
            for blocked in (
                "GITOPS_FEATURE_REGISTRY_TOKEN",
                "GITOPS_FEATURE_NEXTCLOUD_PASSWORD",
                "SIMOS_OTA_SECRET_KEY",
                "SIMOS_OTA_APP_KEY",
            ):
                environment.pop(blocked, None)
            if Path("/var/run/docker.sock").is_socket():
                bubblewrap = shutil.which("bwrap")
                if not bubblewrap:
                    self.skipTest("bwrap is required to isolate the host Docker socket")
                command = [
                    bubblewrap,
                    "--ro-bind", "/", "/",
                    "--dev", "/dev",
                    "--proc", "/proc",
                    "--bind", str(workspace), str(workspace),
                    "--tmpfs", "/run",
                    "--chdir", str(workspace),
                    *command,
                ]
            return subprocess.run(command, cwd=workspace, env=environment, capture_output=True, text=True)

        too_few = invoke("resident", source_argument)
        too_many = invoke("resident", source_argument, output_argument, "extra")
        invalid_kind = invoke("all", source_argument, output_argument)
        for result in (too_few, too_many, invalid_kind):
            self.assertNotEqual(result.returncode, 0, result)
        self.assertFalse((source / "resident-invocation.json").exists())
        self.assertFalse((source / "deb-invocation.json").exists())

        resident = invoke("resident", source_argument, output_argument)
        self.assertEqual(resident.returncode, 0, resident.stderr)
        resident_environment = json.loads((source / "resident-invocation.json").read_text(encoding="utf-8"))
        self.assertEqual(resident_environment, {
            "CI_PROJECT_DIR": str(source),
            "CI_COMMIT_TAG": "",
            "SIMOS_MATRIX_CONFIG_REF": None,
            "SIMOS_MATRIX_CONFIG_LABEL": None,
            "SIMOS_PACKAGE_REGISTRY_NAME": "simos-resident",
            "SIMOS_PACKAGE_REGISTRY_UPLOAD_ENABLED": "false",
            "SIMOS_PACKAGE_REGISTRY_UPLOAD_REQUIRED": "false",
            "SIMOS_BUILD_IMAGE": "resident-image:test",
        })
        self.assertTrue((output / "resident" / "resident-packages" / "360" / "nested" / "resident.tar.gz").is_file())
        self.assertTrue((output / "resident" / "package-registry-result.json").is_file())
        for relative_path in (
            "resident-package-info/build-info.json",
            "build-info.json",
            "checksums.txt",
            "checksum.md5",
            "config-build-info.env",
        ):
            self.assertTrue((output / "resident" / relative_path).is_file(), relative_path)
        self.assertFalse((output / "resident.tar.gz").exists())
        self.assertFalse(any(output.rglob("unlisted-output.zip")))
        self.assertFalse((source / "build-all-invoked").exists())
        self.assertFalse((source / "deb-invocation.json").exists())

        deb = invoke("deb", source_argument, output_argument)
        self.assertEqual(deb.returncode, 0, deb.stderr)
        deb_environment = json.loads((source / "deb-invocation.json").read_text(encoding="utf-8"))
        self.assertEqual(deb_environment, {
            "CI_PROJECT_DIR": str(source),
            "CI_COMMIT_TAG": "",
            "SIMOS_MATRIX_CONFIG_REF": None,
            "SIMOS_MATRIX_CONFIG_LABEL": None,
            "SIMOS_DEB_PACKAGE_REGISTRY_NAME": "simos-debs",
            "SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_ENABLED": "false",
            "SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_REQUIRED": "false",
            "SIMOS_DEB_BUILD_IMAGE": "deb-image:test",
            "SIMOS_DEB_BUILD_MODE": "all",
            "SIMOS_DEB_BUILD_JOBS": "16",
        })
        self.assertTrue((output / "deb" / "deb-packages" / "360" / "nested" / "app.deb").is_file())
        self.assertTrue((output / "deb" / "deb_packages" / "simos_1.0_arm64.deb").is_file())
        deb_manifest_path = output / "deb" / "deb-package-registry-result.json"
        self.assertTrue(deb_manifest_path.is_file())
        deb_manifest = json.loads(deb_manifest_path.read_text(encoding="utf-8"))
        self.assertIn("tag", deb_manifest)
        self.assertEqual(deb_manifest["tag"], "")
        for relative_path in (
            "deb-package-info/build-info.json",
            "config-build-info.env",
            "vehicle.info",
        ):
            self.assertTrue((output / "deb" / relative_path).is_file(), relative_path)
        self.assertFalse((output / "app.deb").exists())
        self.assertFalse(any(output.rglob("unlisted-output.deb")))
        self.assertEqual({path.name for path in output.iterdir()}, {"resident", "deb"})
        self.assertIn("resident-packages", {path.name for path in (output / "resident").iterdir()})
        self.assertIn("deb-packages", {path.name for path in (output / "deb").iterdir()})
        self.assertFalse((source / "build-all-invoked").exists())

        failed_resident = invoke(
            "resident",
            source_argument,
            output_argument,
            fail_after_output=True,
        )
        self.assertEqual(failed_resident.returncode, 42, failed_resident.stderr)
        self.assertIn("preserved available diagnostics", failed_resident.stderr)
        self.assertTrue((output / "resident" / "package-registry-result.json").is_file())
        self.assertTrue(
            (output / "resident" / "resident-packages" / "360" / "nested" / "resident.tar.gz").is_file()
        )

        failed_deb = invoke(
            "deb",
            source_argument,
            output_argument,
            fail_after_output=True,
        )
        self.assertEqual(failed_deb.returncode, 43, failed_deb.stderr)
        self.assertIn("preserved available diagnostics", failed_deb.stderr)
        self.assertTrue((output / "deb" / "deb-package-registry-result.json").is_file())
        self.assertTrue(
            (output / "deb" / "deb-packages" / "360" / "nested" / "app.deb").is_file()
        )

        (source / "resident-invocation.json").unlink()
        (source / "deb-invocation.json").unlink()
        mismatch = invoke("resident", source_argument, output_argument, config_ref="SIMBOT_R6_A", config_label="360s")
        self.assertEqual(mismatch.returncode, 0, mismatch.stderr)
        inherited = json.loads((source / "resident-invocation.json").read_text())
        self.assertIsNone(inherited["SIMOS_MATRIX_CONFIG_REF"])
        self.assertIsNone(inherited["SIMOS_MATRIX_CONFIG_LABEL"])
        self.assertFalse((source / "deb-invocation.json").exists())
        self.assertFalse((source / "build-all-invoked").exists())

        (source / "package-registry-result.json").unlink()
        missing_manifest = invoke("resident", source_argument, output_argument, omit_manifest=True)
        self.assertNotEqual(missing_manifest.returncode, 0, missing_manifest)
        self.assertIn("formal resident manifest is missing", missing_manifest.stderr)

    def test_feature_registry_publisher_validates_formal_manifests(self):
        script = server.ROOT.parent / ".gitlab/scripts/feature-package-publish-registry.sh"
        root = Path(self.tmpdir.name)
        binary = root / "bin"
        binary.mkdir()
        curl = binary / "curl"
        curl.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" >> \"$UPLOAD_LOG\"\n")
        curl.chmod(0o755)
        context = {"schema": 3, "build_id": "T20260908143140_r3", "registry": {"project": "software_hmi_app/gitops-control"}}
        context_path = root / "context.json"
        context_path.write_text(json.dumps(context))
        for kind in ("resident", "deb"):
            with self.subTest(kind=kind):
                output = root / kind / "output"
                directory = output / kind
                package_dir = directory / ("resident-packages" if kind == "resident" else "deb_packages")
                package_dir.mkdir(parents=True)
                name = "resident.tar.gz" if kind == "resident" else "business_3.1.3.0_arm64.deb"
                package = package_dir / name
                package.write_bytes(b"payload")
                metadata = directory / (kind + "-package-info") / "build-info.json"
                metadata.parent.mkdir()
                metadata.write_text("{}")
                manifest_path = directory / ("package-registry-result.json" if kind == "resident" else "deb-package-registry-result.json")
                entry = {"file": name, "size": 7, "md5": hashlib.md5(b"payload").hexdigest(),
                         "sha256": hashlib.sha256(b"payload").hexdigest()}
                manifest = {"status": "skipped", "tag": "", "files": [entry]}
                manifest_path.write_text(json.dumps(manifest))
                publish = root / kind / "publish"
                log = root / kind / "uploads.log"

                def invoke():
                    log.unlink(missing_ok=True)
                    shutil.rmtree(publish, ignore_errors=True)
                    return subprocess.run(
                        ["bash", str(script), kind, str(context_path), str(output), str(publish)],
                        env={**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"],
                             "UPLOAD_LOG": str(log), "CI_API_V4_URL": "https://gitlab.test/api/v4",
                             "CI_PROJECT_ID": "30", "CI_PROJECT_PATH": "software_hmi_app/gitops-control", "CI_JOB_TOKEN": "test-token"},
                        capture_output=True, text=True)

                result = invoke()
                self.assertEqual(result.returncode, 0, result.stderr)
                plan = json.loads((publish / "registry-result.json").read_text())
                self.assertEqual(plan["kind"], kind)
                self.assertIn(f"{kind}/{package_dir.name}/{name}", {item["local_path"] for item in plan["files"]})
                self.assertIn(f"{kind}/{kind}-package-info/build-info.json", {item["local_path"] for item in plan["files"]})
                self.assertTrue(log.is_file())

                for field, value in (("size", 8), ("md5", "0" * 32), ("sha256", "0" * 64),
                                     ("file", "../outside.deb"), ("registry_file", "../escape")):
                    with self.subTest(kind=kind, field=field):
                        manifest_path.write_text(json.dumps({**manifest, "files": [{**entry, field: value}]}))
                        result = invoke()
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(log.exists(), result.stderr)
                manifest_path.write_text(json.dumps({**manifest, "tag": "release_V3.1.3.0"}))
                self.assertNotEqual(invoke().returncode, 0)
                self.assertFalse(log.exists())
                manifest_path.write_text(json.dumps(manifest))
                unlisted = package_dir / "extra.deb"
                unlisted.write_bytes(b"unlisted")
                self.assertNotEqual(invoke().returncode, 0)
                self.assertFalse(log.exists())
                unlisted.unlink()
                package.unlink()
                self.assertNotEqual(invoke().returncode, 0)
                self.assertFalse(log.exists())
                outside = root / (kind + "-outside")
                outside.write_bytes(b"payload")
                package.symlink_to(outside)
                self.assertNotEqual(invoke().returncode, 0)
                self.assertFalse(log.exists())

    def test_feature_nextcloud_publisher_preserves_variant_layout(self):
        SCRIPT = str(server.ROOT.parent / ".gitlab/scripts/feature-package-publish-nextcloud.sh")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'bin'
            binary.mkdir()
            sudo = binary / 'sudo'
            sudo.write_text("""#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
assert args[:2] == ['-n', '/usr/local/bin/simos-ci-publish-resident']
with open(os.environ['PUBLISH_LOG'], 'a') as log:
    log.write(('capability' if '--capability' in args else 'publish') + '\\n')
if os.environ.get('SIMOS_FEATURE_TEST_SERVER_PUBLISHER'):
    os.execv(os.environ['SIMOS_FEATURE_TEST_SERVER_PUBLISHER'], [os.environ['SIMOS_FEATURE_TEST_SERVER_PUBLISHER'], *args[2:]])
if '--capability' in args:
    print(os.environ.get('TEST_CAPABILITY', 'feature-package-v1'))
else:
    if os.environ.get('PUBLISH_FAIL'): sys.exit(2)
    def value(flag): return args[args.index(flag) + 1]
    bid = value('--feature-build-id')
    assert value('--tag') == value('--version') == bid
    kind = 'deb' if '--deb-only' in args else 'resident'
    incoming = Path(value('--incoming')) / (kind + '-packages')
    assert incoming.is_dir()
    target = Path(os.environ['SIMOS_PUBLISH_NEXTCLOUD_VERSIONS_ROOT']) / value('--category') / bid / kind
    shutil.copytree(incoming, target, dirs_exist_ok=True)
""")
            sudo.chmod(0o755)
            curl = binary / 'curl'
            curl.write_text('''#!/usr/bin/env python3
import os, shlex, sys
from pathlib import Path
assert sys.argv[1:3] == ['-q', '--config']
settings = {}
for line in Path(sys.argv[-1]).read_text().splitlines():
    tokens = shlex.split(line)
    if len(tokens) == 3: settings[tokens[0]] = tokens[2]
assert settings['header'] == 'JOB-TOKEN: test-token'
assert 'location' not in settings
with open(os.environ['DOWNLOAD_LOG'], 'a') as log:
    log.write(settings['url'] + '\\n')
if os.environ.get('HTTP_STATUS'):
    print(os.environ['HTTP_STATUS'], end=''); sys.exit(0)
if settings['url'].endswith('/artifacts'):
    Path(settings['output']).write_bytes(Path(os.environ['SOURCE_ARCHIVE']).read_bytes())
    print('200', end=''); sys.exit(0)
Path(settings['output']).write_bytes(b'corrupt' if os.environ.get('CORRUPT') else b'payload')
print('200', end='')
''')
            curl.chmod(0o755)
            env = {**os.environ, 'PATH': str(binary) + ':' + os.environ['PATH'],
                   'PUBLISH_LOG': str(root / 'publisher.log'), 'DOWNLOAD_LOG': str(root / 'download.log'),
                   'CI_JOB_TOKEN': 'test-token',
                   'CI_API_V4_URL': 'https://gitlab.test/api/v4', 'CI_PROJECT_ID': '30', 'CI_PROJECT_PATH': 'software_hmi_app/gitops-control',
                   'SIMOS_PUBLISH_ARTIFACT_ROOT': str(root / 'artifacts'),
                   'SIMOS_PUBLISH_NEXTCLOUD_VERSIONS_ROOT': str(root / 'cloud'), 'SIMOS_PUBLISH_SKIP_OCC': 'true'}
            bid = 'T20260908143140_r3'
            context = {'schema': 3, 'build_id': bid, 'cloud_category': 'Feature/packages',
                       'registry': {'project': 'software_hmi_app/gitops-control'}, 'components': []}
            (root / 'context.json').write_text(json.dumps(context))
            publish = root / 'publish'
            publish.mkdir()
            for kind in ('resident', 'deb'):
                package = 'feature-resident' if kind == 'resident' else 'feature-debs'
                names = ['resident.tar.gz', 'simos.config', 'deploy.sh', 'remote_run.sh', 'checksum.md5'] if kind == 'resident' else ['simos_1_arm64.deb']
                directory = 'resident-packages' if kind == 'resident' else 'deb_packages'
                files = [{'package_name': package, 'registry_file': name,
                          'registry_url': f'https://gitlab.test/api/v4/projects/30/packages/generic/{package}/{bid}/{name}',
                          'local_path': f'{kind}/{directory}/{name}', 'nextcloud_path': f'{kind}/{directory}/{name}',
                          'kind': kind, 'size': 7, 'md5': hashlib.md5(b'payload').hexdigest(),
                          'sha256': hashlib.sha256(b'payload').hexdigest()} for name in names]
                plan = {'build_id': bid, 'project': 'software_hmi_app/gitops-control', 'kind': kind,
                        kind: {'package_name': package, 'package_version': bid}, 'files': files}
                (publish / 'registry-result.json').write_text(json.dumps(plan))
                command = ['bash', SCRIPT, kind, 'context.json', 'publish', 'result.json']
                result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((root / 'cloud' / 'Feature' / 'packages' / bid / kind / names[0]).is_file())
                self.assertEqual(json.loads((root / 'result.json').read_text())['nextcloud']['cloud_dir'], f'/public/Versions/Feature/packages/{bid}/{kind}')
                published = json.loads((root / 'result.json').read_text())['nextcloud']['files']
                self.assertEqual({item['nextcloud_path'] for item in published}, {f'{kind}/{name}' for name in names})
                for item in published:
                    self.assertTrue((root / 'cloud' / 'Feature' / 'packages' / bid / item['nextcloud_path']).is_file())
                (root / 'result.json').unlink()
                calls_before = (root / 'publisher.log').read_text().count('publish\n')
                result = subprocess.run(command, cwd=root, env={**env, 'CORRUPT': '1'}, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((root / 'publisher.log').read_text().count('publish\n'), calls_before)
                self.assertFalse((root / 'result.json').exists())
                self.assertFalse(list(root.glob('.feature-package-nextcloud-*')))

                for mutation in (
                    {**plan, 'kind': 'deb' if kind == 'resident' else 'resident'},
                    {**plan, 'files': []},
                    {**plan, 'files': [*files, files[0]]},
                    {**plan, 'files': [{**files[0], 'registry_url': 'https://other.test/package'}]},
                    {**plan, 'files': [{**files[0], 'local_path': f'{kind}/../escape'}]},
                ):
                    (root / 'download.log').unlink(missing_ok=True)
                    (publish / 'registry-result.json').write_text(json.dumps(mutation))
                    rejected = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertFalse((root / 'download.log').exists(), rejected.stderr)
                (publish / 'registry-result.json').write_text(json.dumps(plan))
                for status in ('302', '403'):
                    rejected = subprocess.run(command, cwd=root, env={**env, 'HTTP_STATUS': status}, capture_output=True, text=True)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertEqual((root / 'publisher.log').read_text().count('publish\n'), calls_before)

                archive = root / 'source.zip'
                with zipfile.ZipFile(archive, 'w') as bundle:
                    bundle.writestr('feature-context.json', json.dumps(context))
                    bundle.writestr('feature-publish/registry-result.json', json.dumps(plan))
                    bundle.writestr('../unwanted', 'must not extract')
                scripts = root / '.gitlab/scripts'
                scripts.mkdir(parents=True, exist_ok=True)
                shutil.copy2(SCRIPT, scripts / 'feature-package-publish-nextcloud.sh')
                republish = server.ROOT.parent / '.gitlab/scripts/feature-package-republish.sh'
                republish_env = {**env, 'CI_PROJECT_ID': '30', 'SOURCE_ARCHIVE': str(archive),
                                 'GITOPS_FEATURE_REPUBLISH_JOB_ID': '21562', 'GITOPS_FEATURE_REPUBLISH_KIND': kind}
                retried = subprocess.run(['bash', str(republish)], cwd=root, env=republish_env, capture_output=True, text=True)
                self.assertEqual(retried.returncode, 0, retried.stderr)
                self.assertEqual(json.loads((root / 'feature-package-result.json').read_text())['status'], 'success')
                self.assertFalse(list(root.glob('.feature-republish-*')))
                self.assertFalse((root.parent / 'unwanted').exists())
                rejected = subprocess.run(['bash', str(republish)], cwd=root,
                    env={**republish_env, 'GITOPS_FEATURE_REPUBLISH_KIND': 'deb' if kind == 'resident' else 'resident'},
                    capture_output=True, text=True)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn('source build kind', rejected.stderr)

    def test_static_contract_for_trusted_feature_pipeline(self):
        now = server.datetime(2026, 8, 27, 15, 30, 45, tzinfo=server.ZoneInfo("Asia/Shanghai"))
        first = server.allocate_feature_package_build_id("feature/release_login", now, [])
        self.assertEqual(first, "T20260827153045_login")
        self.assertEqual(server.allocate_feature_package_build_id("feature/release_login", now, [{"version": first}]), "T20260827153046_login")
        index = (server.STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        app_js = (server.STATIC_ROOT / "app.js").read_text(encoding="utf-8")
        ci = (server.ROOT.parent / ".gitlab" / "ci" / "feature-package.yml").read_text(encoding="utf-8")
        root_ci = (server.ROOT.parent / ".gitlab-ci.yml").read_text(encoding="utf-8")
        verifier = (server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-validate.py").read_text(encoding="utf-8")
        self.assertIn('name="baseline_ref"', index); self.assertIn('id="featurePackageCloudCategory"', index)
        self.assertIn('id="featurePackageRepositories"', index); self.assertIn("feature-operations-grid", index)
        self.assertNotIn("featurePackageForceWeek", index); self.assertIn("/api/feature-package/runs", app_js)
        self.assertIn('["#featurePackageForm", "#featurePackageRef", "feature_branches", ""]', app_js)
        self.assertNotIn('fillSelect("#featurePackageRef", (state.commonRefs', app_js)
        self.assertIn("error: result.error", app_js)
        self.assertIn("GITOPS_FEATURE_CONTEXT_HMAC", verifier)
        prepare = (server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-prepare.sh").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("CI_SERVER_URL", "").rstrip("/")', prepare)
        self.assertIn('f"ssh://git@{server_host}:22222/"', prepare)
        self.assertIn('f"git@{server_host}:"', prepare)
        self.assertNotIn('CI_SERVER_PROTOCOL", "https"', prepare)
        self.assertIn("The button_* jobs are legacy GitLab Web-operation entry points.", root_ci)
        self.assertIn("if: '$GITOPS_FEATURE_PACKAGE == \"1\"'", root_ci)
        self.assertIn("when: never", root_ci)
        self.assertIn("- when: manual", root_ci)
        self.assertIn("stages:\n  - operate\n  - package\n  - publish", root_ci)
        self.assertIn(".gitops_base:\n  stage: operate", root_ci)
        self.assertIn("feature_build_resident:", ci)
        self.assertIn("feature_build_deb:", ci)
        for build_job in ("feature_build_resident:", "feature_build_deb:"):
            build_section = ci.split(build_job, 1)[1]
            self.assertIn("artifacts:\n    when: always", build_section)
        self.assertNotIn("feature_publish_registry:", ci)
        self.assertNotIn("SIMOS_MATRIX_CONFIG_REF", ci)
        self.assertNotIn("SIMOS_MATRIX_CONFIG_LABEL", ci)
        self.assertNotIn("feature_prepare:\n", ci)
        for job, stage in (
            ("feature_context_validate", "package"),
            ("feature_build_resident", "package"),
            ("feature_build_deb", "package"),
            ("feature_publish_nextcloud_resident", "publish"),
            ("feature_publish_nextcloud_deb", "publish"),
        ):
            self.assertIn(f"{job}:\n  extends: .feature_package_rules\n  stage: {stage}", ci)
        publisher_jobs = ("feature_publish_nextcloud_resident:", "feature_publish_nextcloud_deb:")
        for index, publisher_job in enumerate(publisher_jobs):
            publisher_section = ci.split(publisher_job, 1)[1]
            if index + 1 < len(publisher_jobs):
                publisher_section = publisher_section.split(publisher_jobs[index + 1], 1)[0]
            self.assertIn("    name: feature-package-nextcloud\n", publisher_section)
            self.assertIn("  after_script:\n", publisher_section)
            self.assertIn("feature-package publisher did not produce a success result", publisher_section)
        self.assertNotIn("button_", ci)
        self.assertNotIn("upload-ota", ci)
        self.assertNotIn("release-note", ci)
        self.assertNotIn("OTA", ci)
        self.assertNotIn("stage: upload", ci)
        self.assertNotIn('simos-cloud-publisher-222', ci)
        self.assertEqual(ci.count('tags: ["simos-feature-build"]'), 3)
        self.assertEqual(ci.count('tags: ["gitops-feature-publisher"]'), 2)
        self.assertNotIn("GIT_STRATEGY: none", ci)
        self.assertEqual(ci.count("GIT_STRATEGY: fetch"), 5)
        self.assertEqual(ci.count('image: "$GITOPS_FEATURE_BUILD_IMAGE"'), 4)
        self.assertNotIn("apk add", ci)
        self.assertNotIn("stage: upload", ci)
        self.assertNotIn("SIMOS_OTA", ci)
        prepare = (server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-prepare.sh").read_text(encoding="utf-8")
        self.assertIn('"submodule", "update", "--init", "--recursive"', prepare)

    def test_registry_publisher_resolves_relative_output_once(self):
        script = (server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-publish-registry.sh").read_text(encoding="utf-8")
        self.assertIn("root=root_path.resolve()", script)
        self.assertIn("p=(root/p).resolve()", script)
        self.assertNotIn("root_path/p).resolve()", script)

    def test_prepare_allows_config_without_simos_gitlink(self):
        prepare = (server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-prepare.sh").read_text(encoding="utf-8")
        self.assertIn('repo_id == "config"', prepare)
        self.assertIn('git", "clone"', prepare)
        self.assertIn('component.get("project")', prepare)
        self.assertIn('update-index", "--cacheinfo"', prepare)
        self.assertIn('path != "src/config"', prepare)

    def test_pipeline_result_is_persisted_only_after_trusted_publish_artifact(self):
        app, clients = make_feature_app()
        started = app.create_feature_package(self.package_payload())
        pipeline = clients["gitops-workbench"].created_pipelines[0]
        pipeline["status"] = "success"
        clients["gitops-workbench"].jobs_by_pipeline[pipeline["id"]] = [
            {"id": 41, "name": "feature_publish_nextcloud_resident"},
            {"id": 42, "name": "feature_publish_nextcloud_deb"},
        ]
        for job_id, kind in ((41, "resident"), (42, "deb")):
            clients["gitops-workbench"].artifacts[(job_id, "feature-package-result.json")] = json.dumps({
                "status": "success", "build_id": started["version"],
                "registry": {"package_version": started["version"]},
                "nextcloud": {"cloud_dir": f"Feature/x/{kind}"},
            })

        run = app.feature_package_runs()["runs"][0]

        self.assertEqual(run["status"], "success")
        for kind in ("resident", "deb"):
            self.assertEqual(run["registry"][kind]["package_version"], started["version"])
            self.assertEqual(run["nextcloud"][kind]["cloud_dir"], f"Feature/x/{kind}")

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
        script = server.ROOT.parent / ".gitlab/scripts/feature-package-validate.py"
        context = {
            "schema": 3, "run_id": "feature-test",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "build_id": "T20260827153045_login",
            "source": {"repository_id": "simos", "project": "OS/simos",
                       "ref": "feature/release_login", "sha": "a" * 40},
            "cloud_category": "Feature/packages",
            "components": [{"repository_id": "simos", "project": "OS/simos", "submodule_path": "",
                            "requested_ref": "feature/release_login", "resolved_ref": "feature/release_login",
                            "commit_id": "a" * 40, "resolution": "simos_source"}],
            "metadata": {"version_info": "Version:T20260827153045_login", "software_yaml": "version: T20260827153045_login"},
        }
        output = Path(self.tmpdir.name) / "feature-context.json"

        def invoke(candidate, signature_override=None):
            output.unlink(missing_ok=True)
            encoded = base64.urlsafe_b64encode(json.dumps(candidate).encode()).decode()
            signature = hmac.new(b"test-feature-key", encoded.encode(), hashlib.sha256).hexdigest()
            return subprocess.run(
                [sys.executable, str(script)], cwd=self.tmpdir.name,
                env={**os.environ, "GITOPS_FEATURE_PACKAGE": "1", "CI_PIPELINE_SOURCE": "api",
                     "CI_COMMIT_REF_NAME": "ci/feature-package",
                     "GITOPS_FEATURE_CLOUD_CATEGORIES": "Feature/packages",
                     "GITOPS_FEATURE_CONTEXT_B64": encoded,
                     "GITOPS_FEATURE_CONTEXT_HMAC": signature if signature_override is None else signature_override},
                capture_output=True, text=True)

        valid = invoke(context)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(json.loads(output.read_text()), context)
        for signature, message in (("", "signature context"), ("0" * 64, "HMAC mismatch")):
            result = invoke(context, signature)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)
            self.assertFalse(output.exists())
        cases = [
            ({"expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}, "context expired"),
            ({"schema": 1}, "unsupported schema"), ({"schema": 2}, "unsupported schema"),
            ({"schema": 4}, "unsupported schema"), ({"config_source": {}}, "config_source"),
            ({"cloud_category": "../other"}, "allow-list"), ({"components": []}, "snapshot missing"),
            ({"components": context["components"] * 2}, "duplicate repository"),
            ({"components": [{**context["components"][0], "commit_id": "b" * 40}]}, "source SHA"),
            ({"components": [{**context["components"][0], "submodule_path": "src/../bad"}]}, "submodule path"),
            ({"source": {**context["source"], "sha": "invalid"}}, "source snapshot"),
        ]
        for changes, message in cases:
            with self.subTest(changes=changes):
                result = invoke({**context, **changes})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(output.exists())

    def test_user_route_is_allowed_and_generic_tag_route_is_not(self):
        app, _ = make_feature_app(); app.create_feature_package = lambda payload: {"ok": True}  # type: ignore[method-assign]
        token = app.auth.login("user", "user123")["token"]
        handler = object.__new__(server.make_handler(app)); handler.path = "/api/feature-package/create"
        handler.headers = {"Cookie": server.login_cookie(token), "Content-Length": "2"}; handler.rfile = io.BytesIO(b"{}"); handler.wfile = io.BytesIO(); handler.extra_headers = {}
        statuses = []; handler.send_response = lambda status, *args: statuses.append(status); handler.send_header = lambda *args: None; handler.end_headers = lambda: None
        handler.do_POST(); self.assertEqual(statuses, [200])
        handler.path = "/api/tags/create"; handler.rfile = io.BytesIO(b"{}"); statuses.clear(); handler.do_POST(); self.assertEqual(statuses, [403])


if __name__ == "__main__": unittest.main()
