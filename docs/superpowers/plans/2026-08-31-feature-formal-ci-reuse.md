# Feature Formal CI Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make trusted Feature packages execute frozen SimOS formal resident/deb entrypoints with the same four-way Config scheduling, then publish only manifest-verified output through protected Workbench jobs.

**Architecture:** The Workbench backend signs a schema-2 context that contains a fixed formal_matrix Config policy and the frozen SimOS/component snapshot. Feature prepare materializes that snapshot. Two double-entry GitLab matrices invoke the formal SimOS scripts with CI_PROJECT_DIR set to feature-source. Their source-owned Registry writes are disabled; a protected Workbench publisher verifies every manifest and publishes simos-resident and simos-debs under the T build ID.

**Tech Stack:** Python 3 standard library, Bash, GitLab CI YAML, existing SimOS CI scripts, unittest.

**Spec:** docs/superpowers/specs/2026-08-31-feature-formal-ci-reuse-and-migration-design.md

## Global Constraints

- Do not modify the SimOS .gitlab-ci.yml, SimOS formal scripts, formal Runner tags, OTA configuration, formal Tag creation, or formal release behavior.
- Feature Pipeline contains validation, preparation, resident matrix, deb matrix, Registry publish, and Nextcloud publish only. It contains no button jobs, OTA, release-note, Tag, branch, commit, or MR logic.
- simos-feature-build remains non-privileged and credential-free. Only gitops-feature-publisher gets protected Registry/Nextcloud access.
- The wrappers call frozen feature-source/ci/resident/ci-build-resident.sh and feature-source/ci/deb/ci-build-debs.sh. They do not call build_all_debs.sh or reproduce formal build code.
- Current signed Config mode is exactly formal_matrix with SIMBOT_R6_A/360 and SIMBOT_R6_B/360s. shared_branch_snapshot is reserved and cannot run yet.
- Generic package versions use the Feature T build ID and package names simos-resident plus simos-debs.

---

## File Structure

- webapp/server.py: schema-2 context and Config policy persisted with Feature runs.
- webapp/test_feature_package.py: backend, validator, CI YAML, adapter, artifact, and publisher regression tests.
- webapp/simulated_gitlab.py: keeps simulated Feature result shape aligned with schema 2.
- .gitlab-ci.yml: adds Feature-only ordered stages without changing legacy manual job stages.
- .gitlab/ci/feature-package.yml: declares formal resident/deb matrices and protected publish dependencies.
- .gitlab/scripts/feature-package-validate.py: validates signed Config policy.
- .gitlab/scripts/feature-package-build.sh: safe adapter from a matrix instance to a frozen formal SimOS entrypoint.
- .gitlab/scripts/feature-package-publish-registry.sh: validates formal output and publishes both package families.
- .gitlab/scripts/feature-package-publish-nextcloud.sh: publishes only verified entries while preserving variant directories.
- webapp/README.md: current formal-matrix behavior, protected variables, and Config migration boundary.

### Task 1: Sign the fixed Config policy

**Files:**

- Modify: webapp/test_feature_package.py
- Modify: webapp/server.py
- Modify: webapp/simulated_gitlab.py

**Interfaces:**

- Signed context schema is 2.
- context.config_source is exactly:

~~~json
{
  "mode": "formal_matrix",
  "project": "OS/config",
  "variants": [
    {"ref": "SIMBOT_R6_A", "label": "360"},
    {"ref": "SIMBOT_R6_B", "label": "360s"}
  ]
}
~~~

- Feature run records and simulator result retain the same config_source object.

- [ ] **Step 1: Write failing context and input-validation tests**

Extend the existing trusted-pipeline test to assert schema 2, the exact config_source object, and run.config_source equality. Add a payload rejection test for config_source, config_ref, config_sha, SIMOS_CONFIG_REF, and pipeline_variables. It must assert no pipeline was created.

- [ ] **Step 2: Run the focused test to verify failure**

Run: python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_start_only_invokes_trusted_workbench_pipeline -v

Expected: FAIL because the old context is schema 1 and contains no Config policy.

- [ ] **Step 3: Implement a pure policy factory**

In server.py, add an immutable formal variant constant and a function that returns fresh dictionaries:

~~~python
FORMAL_FEATURE_CONFIG_VARIANTS = (
    {"ref": "SIMBOT_R6_A", "label": "360"},
    {"ref": "SIMBOT_R6_B", "label": "360s"},
)

def formal_feature_config_source() -> dict[str, Any]:
    return {
        "mode": "formal_matrix",
        "project": "OS/config",
        "variants": [dict(item) for item in FORMAL_FEATURE_CONFIG_VARIANTS],
    }
~~~

Add the client-controlled Config keys to validate_feature_package_payload. In _start_feature_package, set schema to 2, include the factory result in the signed context, run record, and response. The simulator copies it into its Feature result. Do not add a UI or API control for Config selection.

- [ ] **Step 4: Run focused backend tests to verify success**

Run: python3 -m unittest webapp.test_feature_package -v

Expected: PASS, including the current no-remote-write and no-OTA tests.

- [ ] **Step 5: Commit**

~~~bash
git add webapp/server.py webapp/simulated_gitlab.py webapp/test_feature_package.py
git commit -m "feat: sign formal config matrix for feature packages"
~~~

### Task 2: Validate Config policy before source checkout

**Files:**

- Modify: webapp/test_feature_package.py
- Modify: .gitlab/scripts/feature-package-validate.py

**Interfaces:**

- Only a signed schema-2 context with the exact formal Config policy writes feature-context.json.
- Schema 1, unknown mode, wrong project, reordered variants, duplicate variants, missing variants, and incorrect labels fail closed.

- [ ] **Step 1: Write failing validator cases**

Refactor the current validator fixture to produce a valid schema-2 context. Re-sign and invoke the validator for these modifications:

~~~python
invalid_mode = {**context, "config_source": {**context["config_source"], "mode": "shared_branch_snapshot"}}
invalid_order = {**context, "config_source": {**context["config_source"], "variants": list(reversed(context["config_source"]["variants"]))}}
invalid_label = {**context, "config_source": {**context["config_source"], "variants": [
    {"ref": "SIMBOT_R6_A", "label": "bad"},
    {"ref": "SIMBOT_R6_B", "label": "360s"},
]}}
~~~

Assert nonzero status and an error mentioning config_source. Assert the old schema 1 fixture fails separately.

- [ ] **Step 2: Run the validator test to verify failure**

Run: python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_validator_rejects_missing_tampered_and_expired_contexts -v

Expected: FAIL because the current validator accepts schema 1 and has no Config check.

- [ ] **Step 3: Implement exact structural validation**

Put the expected policy in a module-level FORMAL_CONFIG_SOURCE constant. Require context schema 2 and exact equality with the constant. Use the failure text: invalid config_source; only formal_matrix is supported. Do not accept a partial policy, unknown extra keys, or a future Config mode.

- [ ] **Step 4: Run the focused test to verify success**

Run: python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_validator_rejects_missing_tampered_and_expired_contexts -v

Expected: PASS for valid, missing-signature, tampered, expired, schema-1, and Config-policy cases.

- [ ] **Step 5: Commit**

~~~bash
git add .gitlab/scripts/feature-package-validate.py webapp/test_feature_package.py
git commit -m "feat: validate feature config matrix context"
~~~

### Task 3: Schedule the same four builds as formal SimOS CI

**Files:**

- Modify: webapp/test_feature_package.py
- Modify: .gitlab-ci.yml
- Modify: .gitlab/ci/feature-package.yml

**Interfaces:**

- Two definitions, feature_build_resident and feature_build_deb, each have a two-item matrix.
- feature_publish_registry needs artifacts from both parallel definitions.
- Root stages retain operate for existing manual jobs and append feature_validate, feature_prepare, feature_build, feature_publish.

- [ ] **Step 1: Write failing CI contract assertions**

Assert both new Job names occur, each Config ref and label occurs twice, Registry needs both new names, and the old single feature_build definition is absent. Assert no button, OTA, release-note, or upload stage appears in Feature YAML and .gitops_base remains stage operate.

- [ ] **Step 2: Run the contract test to verify failure**

Run: python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_static_contract_for_trusted_feature_pipeline -v

Expected: FAIL because the current pipeline has one feature_build Job and only operate stage.

- [ ] **Step 3: Implement Feature-specific ordered stages and matrices**

Append the four Feature stages after operate in root CI. Change trusted Job stages to the corresponding Feature stages. Replace feature_build with two definitions, both with simos-feature-build, GIT_STRATEGY fetch, the current protected build image, and a feature_prepare artifact need.

~~~yaml
feature_build_resident:
  stage: feature_build
  parallel:
    matrix:
      - SIMOS_MATRIX_CONFIG_REF: "SIMBOT_R6_A"
        SIMOS_MATRIX_CONFIG_LABEL: "360"
      - SIMOS_MATRIX_CONFIG_REF: "SIMBOT_R6_B"
        SIMOS_MATRIX_CONFIG_LABEL: "360s"
  script:
    - bash .gitlab/scripts/feature-package-build.sh resident feature-source feature-output

feature_build_deb:
  stage: feature_build
  parallel:
    matrix:
      - SIMOS_MATRIX_CONFIG_REF: "SIMBOT_R6_A"
        SIMOS_MATRIX_CONFIG_LABEL: "360"
      - SIMOS_MATRIX_CONFIG_REF: "SIMBOT_R6_B"
        SIMOS_MATRIX_CONFIG_LABEL: "360s"
  script:
    - bash .gitlab/scripts/feature-package-build.sh deb feature-source feature-output
~~~

Registry gets two artifact needs, one for each build definition. Build artifacts retain feature-context.json and feature-output. Keep assigned runner tags unchanged.

- [ ] **Step 4: Validate YAML and tests**

Run:

~~~bash
python3 - <<'PY'
from pathlib import Path
import yaml
for name in (".gitlab-ci.yml", ".gitlab/ci/feature-package.yml"):
    yaml.safe_load(Path(name).read_text(encoding="utf-8"))
PY
python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_static_contract_for_trusted_feature_pipeline -v
~~~

Expected: YAML parses and contract test passes. If PyYAML is unavailable, use Ruby YAML parsing without modifying project files.

- [ ] **Step 5: Commit**

~~~bash
git add .gitlab-ci.yml .gitlab/ci/feature-package.yml webapp/test_feature_package.py
git commit -m "feat: schedule feature builds like formal simos CI"
~~~

### Task 4: Adapt each matrix instance to the frozen formal entrypoint

**Files:**

- Modify: webapp/test_feature_package.py
- Modify: .gitlab/scripts/feature-package-build.sh

**Interfaces:**

- Invocation: feature-package-build.sh resident-or-deb source_dir output_dir.
- Requires SIMOS_MATRIX_CONFIG_REF, SIMOS_MATRIX_CONFIG_LABEL, and validated feature-context.json.
- Produces feature-output/kind/label with paths preserved, rather than flattened artifacts.

- [ ] **Step 1: Write failing adapter tests**

Add static assertions that the wrapper contains CI_PROJECT_DIR set to source_dir, CI_COMMIT_TAG set to build_id, both formal script paths, and both disabled source Registry-upload variables. Assert it contains neither build_all_debs.sh nor a whole-source find-and-copy command.

Add a subprocess test using a temporary fake source tree whose resident/deb entrypoints write their environment to files and generate minimal formal manifests. With a valid feature-context.json, assert resident/360 invokes only the resident path, receives source_dir as CI_PROJECT_DIR, receives the T build ID as CI_COMMIT_TAG, and creates only output/resident/360. With SIMBOT_R6_A plus 360s, assert nonzero status before a fake entrypoint runs.

- [ ] **Step 2: Run the adapter test to verify failure**

Run: python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_feature_build_wrapper_uses_formal_entrypoints -v

Expected: FAIL because the wrapper accepts two arguments and calls build_all_debs.sh.

- [ ] **Step 3: Implement a no-copy build adapter**

The wrapper first keeps the existing container, Docker socket, and blocked credential guards. It then parses feature-context.json with embedded Python, requiring schema 2, exact formal_matrix, and a matching matrix pair. It accepts resident or deb only.

For each Job, set variant_dir to output_dir/kind/SIMOS_MATRIX_CONFIG_LABEL. Remove only that exact directory. Run one frozen entrypoint with child-only environment variables: CI_PROJECT_DIR=source_dir, CI_COMMIT_TAG=build_id, current matrix ref/label, matching package name, upload-enabled false, and upload-required false. Inherit the remaining protected formal build environment.

After the formal script returns, copy exact named outputs with cp -a. Resident copies resident-packages, resident-package-info, package-registry-result.json, build-info.json, checksums.txt, checksum.md5, and config-build-info.env if present. Deb copies deb-packages, deb-package-info, deb-package-registry-result.json, config-build-info.env, and vehicle.info if present. Fail if the expected manifest is absent in variant_dir.

- [ ] **Step 4: Run syntax and adapter tests**

Run:

~~~bash
bash -n .gitlab/scripts/feature-package-build.sh
python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_feature_build_wrapper_uses_formal_entrypoints -v
~~~

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add .gitlab/scripts/feature-package-build.sh webapp/test_feature_package.py
git commit -m "feat: reuse frozen formal simos build entrypoints"
~~~

### Task 5: Validate manifests before publishing both Registry package families

**Files:**

- Modify: webapp/test_feature_package.py
- Modify: .gitlab/scripts/feature-package-publish-registry.sh

**Interfaces:**

- Consumes the four nested build artifacts.
- Produces feature-publish/registry-result.json containing build_id, project, resident, deb, and files.
- Every file has package_name, registry_file, registry_url, local_path, label, kind, size, md5, sha256, and nextcloud_path.

- [ ] **Step 1: Write failing publisher tests**

Build a temporary output tree for all four kind/label combinations. Put a valid formal manifest and test package into each. Use a fake curl executable on PATH that appends invocations to a log. Assert success uploads 360 and 360s names to both simos-resident/T and simos-debs/T endpoints, and records nextcloud paths such as resident/360/resident.tar.gz.

For missing kind/label output, wrong manifest tag, wrong Config pair, missing listed file, incorrect MD5/SHA-256, and an extra unlisted package file: assert nonzero exit and an empty curl log.

- [ ] **Step 2: Run publisher test to verify failure**

Run: python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_feature_registry_publisher_validates_formal_manifests -v

Expected: FAIL because the current script scans one flat directory and publishes only simos-debs.

- [ ] **Step 3: Implement the manifest-only publisher**

Replace the current directory scan with embedded Python that iterates exactly the signed Config variants and resident/deb kinds. Load package-registry-result.json or deb-package-registry-result.json from the exact variant directory. Require success or skipped status, Feature tag equality, matching Config metadata, existing files, and exact size/MD5/SHA-256.

Build an allow-list from formal manifest entries plus explicit formal metadata files copied by Task 4. Reject a package-pattern file that is present but unlisted. Only after all validation succeeds, upload via curl with CI_JOB_TOKEN to CI_API_V4_URL/projects/GITOPS_FEATURE_SIMOS_PROJECT_ID/packages/generic/package-name/build-id. Use manifest registry_file names and formal 360/360s prefixes. Write registry-result.json only after every upload completes.

- [ ] **Step 4: Run syntax and publisher tests**

Run:

~~~bash
bash -n .gitlab/scripts/feature-package-publish-registry.sh
python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_feature_registry_publisher_validates_formal_manifests -v
~~~

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add .gitlab/scripts/feature-package-publish-registry.sh webapp/test_feature_package.py
git commit -m "feat: publish verified formal feature manifests"
~~~

### Task 6: Publish verified nested layouts to Nextcloud

**Files:**

- Modify: webapp/test_feature_package.py
- Modify: .gitlab/scripts/feature-package-publish-nextcloud.sh
- Modify: webapp/README.md

**Interfaces:**

- Consumes feature-publish/registry-result.json only.
- Produces feature-package-result.json with status, build_id, config_source, registry, nextcloud.cloud_dir, and nextcloud.files.
- Places each item below cloud-category/build-id/item.nextcloud_path.

- [ ] **Step 1: Write failing nested-publication tests**

Use a trusted Registry result containing resident/360/resident.tar.gz and deb/360/app.deb. A fake curl must observe parent WebDAV directory creation and uploads to both nested paths. Add invalid nextcloud_path values containing dot-dot, a leading slash, or empty segments; they must fail before any upload. Assert the final result includes config_source but no protected password.

- [ ] **Step 2: Run Nextcloud test to verify failure**

Run: python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_feature_nextcloud_publisher_preserves_variant_layout -v

Expected: FAIL because the current script flattens uploads.

- [ ] **Step 3: Implement verified nested publication**

Validate every result entry has a safe relative nextcloud_path. Create base and parent directories idempotently through MKCOL. Download exactly registry_url with Job Token, upload the temporary file to the matching nested Nextcloud URL, and delete it in finally. Write the final JSON only after all entries publish:

~~~python
{
    "status": "success",
    "build_id": context["build_id"],
    "config_source": context["config_source"],
    "registry": registry_result,
    "nextcloud": {"cloud_dir": directory, "files": published_files},
}
~~~

Document the four formal builds, two Registry package names, protected variable requirements, and that shared_branch_snapshot is documented but unavailable without a separately approved change.

- [ ] **Step 4: Run syntax and Nextcloud tests**

Run:

~~~bash
bash -n .gitlab/scripts/feature-package-publish-nextcloud.sh
python3 -m unittest webapp.test_feature_package.FeaturePackageTest.test_feature_nextcloud_publisher_preserves_variant_layout -v
~~~

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add .gitlab/scripts/feature-package-publish-nextcloud.sh webapp/README.md webapp/test_feature_package.py
git commit -m "feat: preserve feature package variant layout"
~~~

### Task 7: Full regression and deployment handoff

**Files:**

- Modify only if verification reveals a concrete defect in previous tasks.

- [ ] **Step 1: Run complete Workbench tests**

Run: python3 -m unittest discover -s webapp -p 'test_*.py' -v

Expected: PASS. Fix only regressions caused by this plan.

- [ ] **Step 2: Run static checks**

Run:

~~~bash
bash -n .gitlab/scripts/feature-package-build.sh
bash -n .gitlab/scripts/feature-package-publish-registry.sh
bash -n .gitlab/scripts/feature-package-publish-nextcloud.sh
python3 - <<'PY'
from pathlib import Path
import yaml
for name in (".gitlab-ci.yml", ".gitlab/ci/feature-package.yml"):
    yaml.safe_load(Path(name).read_text(encoding="utf-8"))
PY
git diff --check
~~~

Expected: all commands succeed. If PyYAML is unavailable, use Ruby YAML parsing without adding dependencies.

- [ ] **Step 3: Audit scope and SimOS isolation**

Run:

~~~bash
rg -n 'upload-ota|ota/register|release-note|button_' .gitlab/ci/feature-package.yml .gitlab/scripts/feature-package-*.sh
git -C /home/simpleai/ubuntu2004/project/simos diff -- .gitlab-ci.yml ci/
git diff --check
~~~

Expected: Feature files have no OTA/release-note/button implementation. SimOS CI diff is empty; preserve any user-owned SimOS working-tree changes outside CI.

- [ ] **Step 4: Commit a verification correction only when necessary**

Run: git status --short and git log --oneline --max-count=8.

If verification required a correction, commit it with a focused fix message. Do not amend or force-push.

- [ ] **Step 5: Hand off infrastructure prerequisites without changing them**

The final delivery must name these required administrator checks:

~~~text
ci/feature-package is protected.
GITOPS_FEATURE_CONTEXT_HMAC_KEY is masked/protected and equals the Workbench service value.
GITOPS_FEATURE_BUILD_IMAGE is the maintained formal SimOS build image.
simos-feature-build is an unprivileged container runner with cross-rootfs and no publisher credentials.
gitops-feature-publisher is restricted to the protected Workbench project/environment and has Registry/Nextcloud credentials.
The build Job Token can read OS/simos, its selected submodules, and OS/config.
~~~

Do not change GitLab Runner or protected-branch settings from repository code.
