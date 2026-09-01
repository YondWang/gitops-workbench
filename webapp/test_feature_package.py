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
        if self.repo_id == "simos" and file_path == ".gitmodules" and ref == "feature/release_login": return GITMODULES
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
        expected_config_source = {
            "mode": "formal_matrix",
            "project": "OS/config",
            "variants": [
                {"ref": "SIMBOT_R6_A", "label": "360"},
                {"ref": "SIMBOT_R6_B", "label": "360s"},
            ],
        }
        self.assertEqual(context["schema"], 2)
        self.assertEqual(context["config_source"], expected_config_source)
        self.assertEqual(context["operator"], "user")
        self.assertEqual(context["source"]["sha"], "simos-feature-abcdef")
        self.assertEqual({item["repo"] for item in context["components"]}, {"simos", "business"})
        self.assertIn(result["version"], context["metadata"]["version_info"])
        self.assertIn(result["version"], context["metadata"]["software_yaml"])
        for repo_id in ("simos", "business"):
                self.assertFalse(any(call[0] in {"create_branch", "create_commit", "create_tag", "create_pipeline"} for call in clients[repo_id].calls))
        persisted_run = app.feature_package_runs()["runs"][0]
        self.assertEqual(persisted_run["operator"], "user")
        self.assertEqual(persisted_run["config_source"], expected_config_source)
        self.assertEqual(result["run"]["config_source"], expected_config_source)

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
            "schema": 2,
            "build_id": "T20260831183045_login",
            "config_source": {
                "mode": "formal_matrix",
                "project": "OS/config",
                "variants": [
                    {"ref": "SIMBOT_R6_A", "label": "360"},
                    {"ref": "SIMBOT_R6_B", "label": "360s"},
                ],
            },
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
        manifest = json.loads((output / "resident" / "360" / "package-registry-result.json").read_text(encoding="utf-8"))
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
            "schema": 2,
            "build_id": "T20260831183045_login",
            "config_source": {
                "mode": "formal_matrix",
                "project": "OS/config",
                "variants": [
                    {"ref": "SIMBOT_R6_A", "label": "360"},
                    {"ref": "SIMBOT_R6_B", "label": "360s"},
                ],
            },
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
  printf '{"status":"skipped","tag":"%s","config_ref":"%s","files":[]}\n' "$CI_COMMIT_TAG" "$SIMOS_MATRIX_CONFIG_REF" > "$CI_PROJECT_DIR/package-registry-result.json"
fi
printf '{}' > "$CI_PROJECT_DIR/resident-package-info/build-info.json"
printf '{}' > "$CI_PROJECT_DIR/build-info.json"
printf 'sha256  resident-packages/360/nested/resident.tar.gz\n' > "$CI_PROJECT_DIR/checksums.txt"
printf 'md5  resident-packages/360/nested/resident.tar.gz\n' > "$CI_PROJECT_DIR/checksum.md5"
printf 'SIMOS_CONFIG_REF=%s\n' "$SIMOS_MATRIX_CONFIG_REF" > "$CI_PROJECT_DIR/config-build-info.env"
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
if [[ "${FAKE_SKIP_MANIFEST:-false}" != true ]]; then
  printf '{"status":"skipped","tag":"%s","config_ref":"%s","files":[]}\n' "$CI_COMMIT_TAG" "$SIMOS_MATRIX_CONFIG_REF" > "$CI_PROJECT_DIR/deb-package-registry-result.json"
fi
printf '{}' > "$CI_PROJECT_DIR/deb-package-info/build-info.json"
printf 'SIMOS_CONFIG_REF=%s\n' "$SIMOS_MATRIX_CONFIG_REF" > "$CI_PROJECT_DIR/config-build-info.env"
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
            "SIMOS_MATRIX_CONFIG_REF": "SIMBOT_R6_A",
            "SIMOS_MATRIX_CONFIG_LABEL": "360",
            "SIMOS_PACKAGE_REGISTRY_NAME": "simos-resident",
            "SIMOS_PACKAGE_REGISTRY_UPLOAD_ENABLED": "false",
            "SIMOS_PACKAGE_REGISTRY_UPLOAD_REQUIRED": "false",
            "SIMOS_BUILD_IMAGE": "resident-image:test",
        })
        self.assertTrue((output / "resident" / "360" / "resident-packages" / "360" / "nested" / "resident.tar.gz").is_file())
        self.assertTrue((output / "resident" / "360" / "package-registry-result.json").is_file())
        for relative_path in (
            "resident-package-info/build-info.json",
            "build-info.json",
            "checksums.txt",
            "checksum.md5",
            "config-build-info.env",
        ):
            self.assertTrue((output / "resident" / "360" / relative_path).is_file(), relative_path)
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
            "SIMOS_MATRIX_CONFIG_REF": "SIMBOT_R6_A",
            "SIMOS_MATRIX_CONFIG_LABEL": "360",
            "SIMOS_DEB_PACKAGE_REGISTRY_NAME": "simos-debs",
            "SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_ENABLED": "false",
            "SIMOS_DEB_PACKAGE_REGISTRY_UPLOAD_REQUIRED": "false",
            "SIMOS_DEB_BUILD_IMAGE": "deb-image:test",
            "SIMOS_DEB_BUILD_MODE": "all",
            "SIMOS_DEB_BUILD_JOBS": "16",
        })
        self.assertTrue((output / "deb" / "360" / "deb-packages" / "360" / "nested" / "app.deb").is_file())
        deb_manifest_path = output / "deb" / "360" / "deb-package-registry-result.json"
        self.assertTrue(deb_manifest_path.is_file())
        deb_manifest = json.loads(deb_manifest_path.read_text(encoding="utf-8"))
        self.assertIn("tag", deb_manifest)
        self.assertEqual(deb_manifest["tag"], "")
        for relative_path in (
            "deb-package-info/build-info.json",
            "config-build-info.env",
            "vehicle.info",
        ):
            self.assertTrue((output / "deb" / "360" / relative_path).is_file(), relative_path)
        self.assertFalse((output / "app.deb").exists())
        self.assertFalse(any(output.rglob("unlisted-output.deb")))
        self.assertEqual({path.name for path in output.iterdir()}, {"resident", "deb"})
        self.assertEqual([path.name for path in (output / "resident").iterdir()], ["360"])
        self.assertEqual([path.name for path in (output / "deb").iterdir()], ["360"])
        self.assertFalse((source / "build-all-invoked").exists())

        failed_resident = invoke(
            "resident",
            source_argument,
            output_argument,
            fail_after_output=True,
        )
        self.assertEqual(failed_resident.returncode, 42, failed_resident.stderr)
        self.assertIn("preserved available diagnostics", failed_resident.stderr)
        self.assertTrue((output / "resident" / "360" / "package-registry-result.json").is_file())
        self.assertTrue(
            (output / "resident" / "360" / "resident-packages" / "360" / "nested" / "resident.tar.gz").is_file()
        )

        failed_deb = invoke(
            "deb",
            source_argument,
            output_argument,
            fail_after_output=True,
        )
        self.assertEqual(failed_deb.returncode, 43, failed_deb.stderr)
        self.assertIn("preserved available diagnostics", failed_deb.stderr)
        self.assertTrue((output / "deb" / "360" / "deb-package-registry-result.json").is_file())
        self.assertTrue(
            (output / "deb" / "360" / "deb-packages" / "360" / "nested" / "app.deb").is_file()
        )

        (source / "resident-invocation.json").unlink()
        (source / "deb-invocation.json").unlink()
        mismatch = invoke("resident", source_argument, output_argument, config_ref="SIMBOT_R6_A", config_label="360s")
        self.assertNotEqual(mismatch.returncode, 0, mismatch)
        self.assertIn("matrix pair", mismatch.stderr)
        self.assertFalse((source / "resident-invocation.json").exists())
        self.assertFalse((source / "deb-invocation.json").exists())
        self.assertFalse((source / "build-all-invoked").exists())

        (source / "package-registry-result.json").unlink()
        missing_manifest = invoke("resident", source_argument, output_argument, omit_manifest=True)
        self.assertNotEqual(missing_manifest.returncode, 0, missing_manifest)
        self.assertIn("formal resident manifest is missing", missing_manifest.stderr)

    def test_feature_registry_publisher_validates_formal_manifests(self):
        """The publisher must complete all manifest checks before its first curl."""
        script = server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-publish-registry.sh"
        workspace = Path(self.tmpdir.name) / "registry-publisher"
        fake_bin = workspace / "bin"
        fake_curl = fake_bin / "curl"
        fake_bin.mkdir(parents=True)
        fake_curl.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' \"$*\" >> \"$FAKE_CURL_LOG\"\n",
            encoding="utf-8",
        )
        fake_curl.chmod(0o755)
        context = {
            "schema": 2,
            "build_id": "T20260831183045_login",
            "registry": {"project": "OS/simos"},
            "config_source": {
                "mode": "formal_matrix",
                "project": "OS/config",
                "variants": [
                    {"ref": "SIMBOT_R6_A", "label": "360"},
                    {"ref": "SIMBOT_R6_B", "label": "360s"},
                ],
            },
        }

        def digest(path):
            data = path.read_bytes()
            return len(data), hashlib.md5(data).hexdigest(), hashlib.sha256(data).hexdigest()

        def write(path, contents):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
            return path

        def build_output(name):
            root = workspace / name
            for config_ref, label in (("SIMBOT_R6_A", "360"), ("SIMBOT_R6_B", "360s")):
                resident = root / "resident" / label
                resident_files = {
                    "resident": write(resident / "resident-packages" / label / "resident.tar.gz", f"resident-{label}".encode()),
                    "resident_md5": write(resident / "resident-packages" / label / "resident.md5", f"md5-{label}".encode()),
                    "simos_config": write(resident / "resident-packages" / label / "simos.config", f"config-{label}".encode()),
                    "deploy_sh": write(resident / "resident-packages" / label / "deploy.sh", b"#!/bin/sh\n"),
                    "remote_run_sh": write(resident / "resident-packages" / label / "remote_run.sh", b"#!/bin/sh\n"),
                    "checksum_md5": write(resident / "resident-packages" / label / "checksum.md5", f"checksum-{label}".encode()),
                }
                for relative in (
                    "build-info.json",
                    "checksums.txt",
                    "checksum.md5",
                    "config-build-info.env",
                    "resident-package-info/build-info.json",
                    "resident-package-info/checksums.txt",
                    "resident-package-info/artifact-path.txt",
                    "resident-package-info/package-registry-result.json",
                ):
                    write(resident / relative, f"metadata-{label}-{relative}".encode())
                size, md5, sha256 = digest(resident_files["resident"])
                resident_files["resident_md5"].write_text(f"{md5}  resident.tar.gz\n", encoding="utf-8")
                (resident / "resident-packages" / label / "build-info.json").write_text(
                    json.dumps({
                        "status": "success",
                        "label": label,
                        "config_ref": config_ref,
                        "artifact_path": f"resident-packages/{label}/resident.tar.gz",
                        "size": size,
                        "md5": md5,
                        "sha256": sha256,
                    }),
                    encoding="utf-8",
                )
                resident_manifest = {
                    "status": "skipped",
                    "config_ref": config_ref,
                    "config_variants": [{
                        "label": label,
                        "config_ref": config_ref,
                        "artifact_path": f"resident-packages/{label}/resident.tar.gz",
                        "size": size,
                        "md5": md5,
                        "sha256": sha256,
                        "registry_files": {key: f"{label}-{path.name}" for key, path in resident_files.items()},
                    }],
                }
                (resident / "package-registry-result.json").write_text(json.dumps(resident_manifest), encoding="utf-8")

                deb = root / "deb" / label
                deb_file = write(deb / "deb-packages" / label / "app.deb", f"deb-{label}".encode())
                size, md5, sha256 = digest(deb_file)
                deb_entry = {
                    "file": "app.deb",
                    "registry_file": f"{label}-app.deb",
                    "size": size,
                    "md5": md5,
                    "sha256": sha256,
                    "label": label,
                    "config_ref": config_ref,
                }
                deb_variant_entry = {key: value for key, value in deb_entry.items() if key not in {"label", "config_ref"}}
                for relative in (
                    "config-build-info.env",
                    "vehicle.info",
                    "deb-package-info/build-info.json",
                    "deb-package-info/deb-package-registry-result.json",
                ):
                    write(deb / relative, f"metadata-{label}-{relative}".encode())
                deb_manifest = {
                    "status": "skipped",
                    "tag": "",
                    "config_ref": config_ref,
                    "config_variants": [{"label": label, "config_ref": config_ref, "files": [deb_variant_entry]}],
                    "files": [deb_entry],
                }
                (deb / "deb-package-registry-result.json").write_text(json.dumps(deb_manifest), encoding="utf-8")
            return root

        context_path = workspace / "feature-context.json"
        context_path.write_text(json.dumps(context), encoding="utf-8")

        def invoke(output, name):
            publish = workspace / f"publish-{name}"
            curl_log = workspace / f"curl-{name}.log"
            curl_log.unlink(missing_ok=True)
            result = subprocess.run(
                ["bash", str(script), str(context_path), str(output), str(publish)],
                cwd=workspace,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "FAKE_CURL_LOG": str(curl_log),
                    "CI_API_V4_URL": "https://gitlab.test/api/v4",
                    "GITOPS_FEATURE_SIMOS_PROJECT_ID": "20",
                    "CI_JOB_TOKEN": "publisher-token",
                },
                capture_output=True,
                text=True,
            )
            return result, curl_log, publish

        output = build_output("valid-output")
        success, curl_log, publish = invoke(output, "success")
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertTrue(curl_log.exists(), "valid formal artifacts must be uploaded")
        uploads = curl_log.read_text(encoding="utf-8")
        self.assertIn("/simos-resident/T20260831183045_login/360-resident.tar.gz", uploads)
        self.assertIn("/simos-resident/T20260831183045_login/360s-resident.tar.gz", uploads)
        self.assertIn("/simos-debs/T20260831183045_login/360-app.deb", uploads)
        self.assertIn("/simos-debs/T20260831183045_login/360s-app.deb", uploads)
        self.assertIn("/simos-resident/T20260831183045_login/360-root-checksum.md5", uploads)
        result = json.loads((publish / "registry-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["build_id"], context["build_id"])
        self.assertEqual(result["project"], "OS/simos")
        self.assertEqual({item["package_name"] for item in result["files"]}, {"simos-resident", "simos-debs"})
        self.assertIn("resident/360/resident.tar.gz", {item["nextcloud_path"] for item in result["files"]})
        self.assertIn("resident/360/metadata/checksum.md5", {item["nextcloud_path"] for item in result["files"]})
        self.assertIn("deb/360/app.deb", {item["nextcloud_path"] for item in result["files"]})
        for item in result["files"]:
            self.assertEqual(set(item), {"package_name", "registry_file", "registry_url", "local_path", "label", "kind", "size", "md5", "sha256", "nextcloud_path"})

        def assert_rejected(name, mutate):
            invalid = build_output(f"invalid-{name}")
            mutate(invalid)
            rejected, invalid_log, invalid_publish = invoke(invalid, name)
            self.assertNotEqual(rejected.returncode, 0, rejected)
            self.assertFalse(invalid_log.exists() and invalid_log.read_text(encoding="utf-8").strip(), rejected.stderr)
            self.assertFalse((invalid_publish / "registry-result.json").exists())

        assert_rejected("missing-output", lambda root: shutil.rmtree(root / "resident" / "360"))

        def nonempty_tag(root):
            path = root / "resident" / "360" / "package-registry-result.json"
            data = json.loads(path.read_text(encoding="utf-8")); data["tag"] = "release_V3.2.1.001_202608311830"; path.write_text(json.dumps(data), encoding="utf-8")
        assert_rejected("nonempty-tag", nonempty_tag)

        def nonempty_deb_tag(root):
            path = root / "deb" / "360" / "deb-package-registry-result.json"
            data = json.loads(path.read_text(encoding="utf-8")); data["tag"] = "release_V3.2.1.001_202608311830"; path.write_text(json.dumps(data), encoding="utf-8")
        assert_rejected("nonempty-deb-tag", nonempty_deb_tag)

        def wrong_config_pair(root):
            path = root / "deb" / "360" / "deb-package-registry-result.json"
            data = json.loads(path.read_text(encoding="utf-8")); data["config_ref"] = "wrong"; data["config_variants"][0]["config_ref"] = "wrong"; data["files"][0]["config_ref"] = "wrong"; path.write_text(json.dumps(data), encoding="utf-8")
        assert_rejected("wrong-config-pair", wrong_config_pair)

        assert_rejected("missing-listed-file", lambda root: (root / "deb" / "360" / "deb-packages" / "360" / "app.deb").unlink())

        def wrong_checksum(root):
            path = root / "deb" / "360" / "deb-package-registry-result.json"
            data = json.loads(path.read_text(encoding="utf-8")); data["files"][0]["sha256"] = "0" * 64; path.write_text(json.dumps(data), encoding="utf-8")
        assert_rejected("wrong-checksum", wrong_checksum)

        def wrong_md5(root):
            path = root / "deb" / "360" / "deb-package-registry-result.json"
            data = json.loads(path.read_text(encoding="utf-8")); data["files"][0]["md5"] = "0" * 32; path.write_text(json.dumps(data), encoding="utf-8")
        assert_rejected("wrong-md5", wrong_md5)

        def tampered_resident_build_info(root):
            path = root / "resident" / "360" / "resident-packages" / "360" / "build-info.json"
            data = json.loads(path.read_text(encoding="utf-8")); data["sha256"] = "0" * 64; path.write_text(json.dumps(data), encoding="utf-8")
        assert_rejected("tampered-resident-build-info", tampered_resident_build_info)

        assert_rejected("unlisted-package", lambda root: write(root / "deb" / "360" / "deb-packages" / "360" / "unlisted.deb", b"unlisted"))

        def outside_symlink(root):
            package = root / "resident" / "360" / "resident-packages" / "360"
            outside = workspace / "outside-resident.tar.gz"
            outside.write_bytes((package / "resident.tar.gz").read_bytes())
            (package / "resident.tar.gz").unlink()
            (package / "resident.tar.gz").symlink_to(outside)
        assert_rejected("outside-listed-symlink", outside_symlink)

        def outside_parent_symlink(root):
            package = root / "resident" / "360" / "resident-packages" / "360"
            outside = workspace / "outside-resident-package"
            shutil.copytree(package, outside)
            shutil.rmtree(package)
            package.symlink_to(outside, target_is_directory=True)
        assert_rejected("outside-parent-symlink", outside_parent_symlink)

        def optional_outside_symlink(root):
            metadata = root / "resident" / "360" / "config-build-info.env"
            outside = workspace / "outside-config-build-info.env"
            outside.write_bytes(metadata.read_bytes())
            metadata.unlink()
            metadata.symlink_to(outside)
        assert_rejected("outside-optional-symlink", optional_outside_symlink)

        def missing_deb_tag(root):
            path = root / "deb" / "360" / "deb-package-registry-result.json"
            data = json.loads(path.read_text(encoding="utf-8")); data.pop("tag"); path.write_text(json.dumps(data), encoding="utf-8")
        assert_rejected("missing-deb-tag", missing_deb_tag)

    def test_feature_nextcloud_publisher_preserves_variant_layout(self):
        """Only the verified Registry plan may drive nested Nextcloud writes."""
        script = server.ROOT.parent / ".gitlab" / "scripts" / "feature-package-publish-nextcloud.sh"
        workspace = Path(self.tmpdir.name) / "nextcloud-publisher"
        fake_bin = workspace / "bin"
        fake_curl = fake_bin / "curl"
        fake_bin.mkdir(parents=True)
        fake_curl.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_CURL_LOG\"\n"
            "skip_default_curlrc=0\n"
            "if [[ \"${1:-}\" == \"-q\" ]]; then skip_default_curlrc=1; fi\n"
            "config=''\n"
            "for ((index = 1; index <= $#; index++)); do\n"
            "  if [[ \"${!index}\" == \"--config\" ]]; then next=$((index + 1)); config=\"${!next}\"; fi\n"
            "done\n"
            "if [[ -n \"$config\" ]]; then\n"
            "  contents=$(<\"$config\")\n"
            "  if grep -q '^request = \"MKCOL\"$' \"$config\"; then\n"
            "    printf 'mkcol\\n' >> \"$FAKE_CURL_ACTION_LOG\"\n"
            "    if [[ \"${FAKE_CURL_FAILURE:-}\" == \"mkcol\" ]]; then printf 'feature-publisher' >&2; printf '500'; exit 0; fi\n"
            "    printf '%s' \"${FAKE_MKCOL_STATUS:-201}\"; exit 0\n"
            "  fi\n"
            "  if [[ \"$contents\" == *'upload-file = '* ]]; then\n"
            "    printf 'put\\n' >> \"$FAKE_CURL_ACTION_LOG\"\n"
            "    if [[ \"${FAKE_CURL_FAILURE:-}\" == \"put\" ]]; then printf 'protected-password' >&2; exit 22; fi\n"
            "    printf '201'; exit 0\n"
            "  fi\n"
            "  printf 'download\\n' >> \"$FAKE_CURL_ACTION_LOG\"\n"
            "  if [[ \"${FAKE_CURL_FAILURE:-}\" == \"download\" ]]; then printf 'read-only-job-token' >&2; exit 22; fi\n"
            "  if [[ \"${FAKE_CURL_FAILURE:-}\" == \"default-curlrc-redirect\" ]]; then\n"
            "    curl_home=\"${CURL_HOME:-${HOME:-}}\"\n"
            "    if [[ \"$skip_default_curlrc\" != \"1\" ]] && [[ -f \"$curl_home/.curlrc\" ]] && grep -qx 'location' \"$curl_home/.curlrc\"; then\n"
            "      printf 'external-request:%s\\n' \"${FAKE_CURL_REDIRECT_TARGET:-https://attacker.test/steal}\" >> \"$FAKE_CURL_ACTION_LOG\"\n"
            "      sed -n 's/^header = \"\\(.*\\)\"$/external-token:\\1/p' \"$config\" >> \"$FAKE_CURL_ACTION_LOG\"\n"
            "    fi\n"
            "    printf '302'; exit 0\n"
            "  fi\n"
            "  if [[ \"${FAKE_CURL_FAILURE:-}\" == \"redirect\" ]]; then printf '302'; exit 0; fi\n"
            "  output=$(sed -n 's#^output = \\\"\\(.*\\)\\\"$#\\1#p' \"$config\")\n"
            "  printf '%s' \"${FAKE_DOWNLOAD_CONTENT:-downloaded}\" > \"$output\"\n"
            "  printf '200'; exit 0\n"
            "fi\n"
            "if [[ \" $* \" == *\" -X MKCOL \"* ]]; then printf '201'; exit 0; fi\n"
            "for ((index = 1; index <= $#; index++)); do\n"
            "  if [[ \"${!index}\" == \"--output\" ]]; then next=$((index + 1)); printf 'downloaded' > \"${!next}\"; fi\n"
            "done\n",
            encoding="utf-8",
        )
        fake_curl.chmod(0o755)
        context = {
            "schema": 2,
            "build_id": "T20260831183045_login",
            "cloud_category": "车机/Feature测试包",
            "registry": {"project": "OS/simos"},
            "config_source": {
                "mode": "formal_matrix",
                "project": "OS/config",
                "variants": [
                    {"ref": "SIMBOT_R6_A", "label": "360"},
                    {"ref": "SIMBOT_R6_B", "label": "360s"},
                ],
            },
        }
        context_path = workspace / "feature-context.json"
        context_path.write_text(json.dumps(context), encoding="utf-8")

        def entry(kind, label, name):
            contents = b"downloaded"
            package_name = "simos-resident" if kind == "resident" else "simos-debs"
            return {
                "package_name": package_name,
                "registry_file": f"{label}-{name}",
                "registry_url": f"https://gitlab.test/api/v4/projects/20/packages/generic/{package_name}/{context['build_id']}/{label}-{name}",
                "local_path": f"{kind}/{label}/{name}",
                "label": label,
                "kind": kind,
                "size": len(contents),
                "md5": hashlib.md5(contents).hexdigest(),
                "sha256": hashlib.sha256(contents).hexdigest(),
                "nextcloud_path": f"{kind}/{label}/{name}",
            }

        def registry_result(*, files):
            return {
                "build_id": context["build_id"],
                "project": "OS/simos",
                "resident": {"package_name": "simos-resident", "package_version": context["build_id"]},
                "deb": {"package_name": "simos-debs", "package_version": context["build_id"]},
                "files": files,
            }

        def invoke(name, result, **extra_environment):
            publish = workspace / f"publish-{name}"
            publish.mkdir(parents=True, exist_ok=True)
            (publish / "registry-result.json").write_text(json.dumps(result), encoding="utf-8")
            curl_log = workspace / f"curl-{name}.log"
            curl_log.unlink(missing_ok=True)
            action_log = workspace / f"curl-{name}.actions"
            action_log.unlink(missing_ok=True)
            output = workspace / f"feature-package-result-{name}.json"
            output.unlink(missing_ok=True)
            completed = subprocess.run(
                ["bash", str(script), str(context_path), str(publish), str(output)],
                cwd=workspace,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "FAKE_CURL_LOG": str(curl_log),
                    "FAKE_CURL_ACTION_LOG": str(action_log),
                    "CI_JOB_TOKEN": "read-only-job-token",
                    "CI_API_V4_URL": "https://gitlab.test/api/v4",
                    "GITOPS_FEATURE_SIMOS_PROJECT_ID": "20",
                    "GITOPS_FEATURE_NEXTCLOUD_URL": "https://nextcloud.test",
                    "GITOPS_FEATURE_NEXTCLOUD_USER": "feature-publisher",
                    "GITOPS_FEATURE_NEXTCLOUD_PASSWORD": "protected-password",
                    **extra_environment,
                },
                capture_output=True,
                text=True,
            )
            return completed, curl_log, action_log, output

        valid_files = [entry("resident", "360", "resident.tar.gz"), entry("deb", "360", "app.deb")]
        success, curl_log, action_log, output = invoke("valid", registry_result(files=valid_files))
        self.assertEqual(success.returncode, 0, success.stderr)
        calls = curl_log.read_text(encoding="utf-8")
        self.assertTrue(calls.strip())
        self.assertTrue(all(line.startswith("-q --config ") for line in calls.splitlines()))
        self.assertNotIn("--location", calls)
        self.assertNotIn("read-only-job-token", calls)
        self.assertNotIn("feature-publisher", calls)
        self.assertNotIn("protected-password", calls)
        actions = action_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(actions[:2], ["download", "download"])
        self.assertGreaterEqual(actions.count("mkcol"), 7)
        self.assertEqual(actions.count("put"), 2)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["build_id"], context["build_id"])
        self.assertEqual(result["config_source"], context["config_source"])
        self.assertEqual(result["nextcloud"]["cloud_dir"], "车机/Feature测试包/T20260831183045_login")
        self.assertEqual({item["nextcloud_path"] for item in result["nextcloud"]["files"]}, {
            "resident/360/resident.tar.gz", "deb/360/app.deb",
        })
        self.assertNotIn("protected-password", output.read_text(encoding="utf-8"))

        for name, invalid_path in (
            ("dotdot", "resident/../resident.tar.gz"),
            ("leading-slash", "/resident/360/resident.tar.gz"),
            ("empty-segment", "resident//resident.tar.gz"),
        ):
            invalid_files = [dict(valid_files[0], nextcloud_path=invalid_path), valid_files[1]]
            rejected, invalid_log, _invalid_actions, invalid_output = invoke(name, registry_result(files=invalid_files))
            self.assertNotEqual(rejected.returncode, 0, rejected)
            self.assertFalse(invalid_log.exists() and invalid_log.read_text(encoding="utf-8").strip(), rejected.stderr)
            self.assertFalse(invalid_output.exists())

        def assert_rejected_before_curl(name, mutate):
            invalid_files = [dict(item) for item in valid_files]
            mutate(invalid_files)
            rejected, invalid_log, _invalid_actions, invalid_output = invoke(name, registry_result(files=invalid_files))
            self.assertNotEqual(rejected.returncode, 0, rejected)
            self.assertFalse(invalid_log.exists() and invalid_log.read_text(encoding="utf-8").strip(), rejected.stderr)
            self.assertFalse(invalid_output.exists())

        assert_rejected_before_curl("wrong-registry-host", lambda items: items[0].update(registry_url=items[0]["registry_url"].replace("gitlab.test", "attacker.test")))
        assert_rejected_before_curl("wrong-registry-project", lambda items: items[0].update(registry_url=items[0]["registry_url"].replace("/projects/20/", "/projects/99/")))
        assert_rejected_before_curl("wrong-registry-package", lambda items: items[0].update(registry_url=items[0]["registry_url"].replace("simos-resident", "simos-debs")))
        assert_rejected_before_curl("wrong-registry-version", lambda items: items[0].update(registry_url=items[0]["registry_url"].replace(context["build_id"], "T20260831183046_login")))

        redirected, redirect_log, redirect_actions, redirect_output = invoke("redirect", registry_result(files=valid_files), FAKE_CURL_FAILURE="redirect")
        self.assertNotEqual(redirected.returncode, 0, redirected)
        self.assertEqual(redirect_actions.read_text(encoding="utf-8").splitlines(), ["download"])
        self.assertNotIn("mkcol", redirect_actions.read_text(encoding="utf-8"))
        self.assertFalse(redirect_output.exists())

        curl_home = workspace / "curl-home"
        curl_home.mkdir(exist_ok=True)
        (curl_home / ".curlrc").write_text("location\n", encoding="utf-8")
        default_curlrc_redirect, _default_curlrc_log, default_curlrc_actions, default_curlrc_output = invoke(
            "default-curlrc-redirect",
            registry_result(files=valid_files),
            FAKE_CURL_FAILURE="default-curlrc-redirect",
            FAKE_CURL_REDIRECT_TARGET="https://attacker.test/stolen-package",
            HOME=str(curl_home),
            CURL_HOME=str(curl_home),
        )
        self.assertNotEqual(default_curlrc_redirect.returncode, 0, default_curlrc_redirect)
        default_actions = default_curlrc_actions.read_text(encoding="utf-8").splitlines()
        self.assertEqual(default_actions, ["download"])
        self.assertFalse(any(action.startswith("external-request:") for action in default_actions))
        self.assertFalse(any("read-only-job-token" in action for action in default_actions))
        self.assertFalse(default_curlrc_output.exists())

        corrupt, _corrupt_log, corrupt_actions, corrupt_output = invoke("corrupt-download", registry_result(files=valid_files), FAKE_DOWNLOAD_CONTENT="corrupt")
        self.assertNotEqual(corrupt.returncode, 0, corrupt)
        self.assertEqual(corrupt_actions.read_text(encoding="utf-8").splitlines(), ["download"])
        self.assertFalse(corrupt_output.exists())

        existing, _existing_log, _existing_actions, existing_output = invoke("existing-directories", registry_result(files=valid_files), FAKE_MKCOL_STATUS="405")
        self.assertEqual(existing.returncode, 0, existing.stderr)
        self.assertTrue(existing_output.exists())

        for failure in ("download", "mkcol", "put"):
            failed, failed_log, _failed_actions, failed_output = invoke(f"failure-{failure}", registry_result(files=valid_files), FAKE_CURL_FAILURE=failure)
            combined = failed.stdout + failed.stderr + (failed_log.read_text(encoding="utf-8") if failed_log.exists() else "")
            self.assertNotEqual(failed.returncode, 0, combined)
            self.assertNotIn("read-only-job-token", combined)
            self.assertNotIn("feature-publisher", combined)
            self.assertNotIn("protected-password", combined)
            self.assertFalse(failed_output.exists())
            self.assertFalse(list(workspace.glob(".feature-package-nextcloud-*")))

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
        self.assertIn("stages:\n  - operate\n  - feature_validate\n  - feature_prepare\n  - feature_build\n  - feature_publish", root_ci)
        self.assertIn(".gitops_base:\n  stage: operate", root_ci)
        self.assertIn("feature_build_resident:", ci)
        self.assertIn("feature_build_deb:", ci)
        for build_job in ("feature_build_resident:", "feature_build_deb:"):
            build_section = ci.split(build_job, 1)[1].split("feature_publish_registry:", 1)[0]
            self.assertIn("artifacts:\n    when: always", build_section)
        self.assertNotIn("feature_build:\n", ci)
        for config_ref, config_label in (("SIMBOT_R6_A", "360"), ("SIMBOT_R6_B", "360s")):
            self.assertEqual(ci.count(f'SIMOS_MATRIX_CONFIG_REF: "{config_ref}"'), 2)
            self.assertEqual(ci.count(f'SIMOS_MATRIX_CONFIG_LABEL: "{config_label}"'), 2)
        registry_needs = ci.split("feature_publish_registry:", 1)[1].split("feature_publish_nextcloud:", 1)[0]
        self.assertIn("- job: feature_build_resident\n      artifacts: true", registry_needs)
        self.assertIn("- job: feature_build_deb\n      artifacts: true", registry_needs)
        for job, stage in (
            ("feature_context_validate", "feature_validate"),
            ("feature_prepare", "feature_prepare"),
            ("feature_build_resident", "feature_build"),
            ("feature_build_deb", "feature_build"),
            ("feature_publish_registry", "feature_publish"),
            ("feature_publish_nextcloud", "feature_publish"),
        ):
            self.assertIn(f"{job}:\n  extends: .feature_package_rules\n  stage: {stage}", ci)
        self.assertNotIn("button_", ci)
        self.assertNotIn("upload-ota", ci)
        self.assertNotIn("release-note", ci)
        self.assertNotIn("OTA", ci)
        self.assertNotIn("stage: upload", ci)
        self.assertNotIn('simos-cloud-publisher-222', ci)
        self.assertEqual(ci.count('tags: ["simos-feature-build"]'), 4)
        self.assertEqual(ci.count('tags: ["gitops-feature-publisher"]'), 2)
        self.assertNotIn("GIT_STRATEGY: none", ci)
        self.assertEqual(ci.count("GIT_STRATEGY: fetch"), 6)
        self.assertEqual(ci.count('image: "$GITOPS_FEATURE_BUILD_IMAGE"'), 5)
        self.assertNotIn("apk add", ci)
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
            "schema": 2,
            "run_id": "feature-test",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "build_id": "T20260827153045_login",
            "source": {"repository_id": "simos", "project": "OS/simos", "ref": "feature/release_login", "sha": "a" * 40},
            "cloud_category": "车机/Feature测试包",
            "components": [{"repo": "simos", "project": "OS/simos", "submodule_path": "", "ref": "feature/release_login", "sha": "a" * 40}],
            "metadata": {"version_info": "Version:T20260827153045_login\n", "software_yaml": "version: T20260827153045_login\n"},
            "config_source": {
                "mode": "formal_matrix",
                "project": "OS/config",
                "variants": [
                    {"ref": "SIMBOT_R6_A", "label": "360"},
                    {"ref": "SIMBOT_R6_B", "label": "360s"},
                ],
            },
        }
        encoded = base64.urlsafe_b64encode(json.dumps(context).encode()).decode()
        signature = hmac.new(b"test-feature-key", encoded.encode(), hashlib.sha256).hexdigest()
        environment = {**os.environ, "GITOPS_FEATURE_PACKAGE": "1", "CI_PIPELINE_SOURCE": "api", "CI_COMMIT_REF_NAME": "ci/feature-package", "GITOPS_FEATURE_CONTEXT_HMAC_KEY": "test-feature-key", "GITOPS_FEATURE_CLOUD_CATEGORIES": "车机/Feature测试包", "GITOPS_FEATURE_CONTEXT_B64": encoded}
        output = Path(self.tmpdir.name) / "feature-context.json"

        def invoke(candidate, signature_override=None):
            output.unlink(missing_ok=True)
            encoded = base64.urlsafe_b64encode(json.dumps(candidate).encode()).decode()
            signature = hmac.new(b"test-feature-key", encoded.encode(), hashlib.sha256).hexdigest()
            return subprocess.run(
                [sys.executable, str(script)],
                cwd=self.tmpdir.name,
                env={
                    **environment,
                    "GITOPS_FEATURE_CONTEXT_B64": encoded,
                    "GITOPS_FEATURE_CONTEXT_HMAC": signature if signature_override is None else signature_override(signature),
                },
                capture_output=True,
                text=True,
            )

        valid = invoke(context)
        output.unlink(missing_ok=True)
        missing = subprocess.run([sys.executable, str(script)], cwd=self.tmpdir.name, env=environment, capture_output=True, text=True)
        tampered = invoke(context, lambda value: "0" * len(value))
        expired_context = {**context, "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
        expired = invoke(expired_context)
        schema_1 = invoke({**context, "schema": 1})
        future_schema = invoke({**context, "schema": 3})
        invalid_mode = invoke({**context, "config_source": {**context["config_source"], "mode": "shared_branch_snapshot"}})
        unknown_mode = invoke({**context, "config_source": {**context["config_source"], "mode": "unrecognized"}})
        invalid_project = invoke({**context, "config_source": {**context["config_source"], "project": "OS/other"}})
        invalid_order = invoke({**context, "config_source": {**context["config_source"], "variants": list(reversed(context["config_source"]["variants"]))}})
        duplicate_variants = invoke({**context, "config_source": {**context["config_source"], "variants": [
            {"ref": "SIMBOT_R6_A", "label": "360"},
            {"ref": "SIMBOT_R6_A", "label": "360"},
        ]}})
        missing_variant = invoke({**context, "config_source": {**context["config_source"], "variants": [
            {"ref": "SIMBOT_R6_A", "label": "360"},
        ]}})
        invalid_label = invoke({**context, "config_source": {**context["config_source"], "variants": [
            {"ref": "SIMBOT_R6_A", "label": "bad"},
            {"ref": "SIMBOT_R6_B", "label": "360s"},
        ]}})
        extra_policy_key = invoke({**context, "config_source": {**context["config_source"], "ref": "SIMBOT_R6_A"}})

        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertNotEqual(missing.returncode, 0); self.assertIn("signature context", missing.stderr)
        self.assertNotEqual(tampered.returncode, 0); self.assertIn("HMAC mismatch", tampered.stderr)
        self.assertNotEqual(expired.returncode, 0); self.assertIn("context expired", expired.stderr)
        self.assertNotEqual(schema_1.returncode, 0)
        self.assertIn("unsupported schema", schema_1.stderr)
        self.assertNotEqual(future_schema.returncode, 0)
        self.assertIn("unsupported schema", future_schema.stderr)
        for result in (invalid_mode, unknown_mode, invalid_project, invalid_order, duplicate_variants, missing_variant, invalid_label, extra_policy_key):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("config_source", result.stderr)
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
