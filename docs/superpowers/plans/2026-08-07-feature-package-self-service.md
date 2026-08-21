# Feature Package Self-Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `user` accounts to create isolated `T`-prefixed packages from any `feature/*` branch while preserving the existing formal release/tag permissions and providing a local GitLab/CI simulation mode.

**Architecture:** Add a Feature-only application operation and permission instead of widening `create_tag`. The operation resolves the existing four-part version policy, optionally increments the third part without resetting the fourth, creates an isolated temporary build ref with package metadata and component pins, and tags that ref. A persistent simulation client will implement the same GitLab client surface against local JSON state, so local UI tests never contact GitLab or trigger CI.

**Tech Stack:** Python 3 standard library HTTP server, existing `GitLabClient` abstraction, `unittest`, vanilla HTML/CSS/JavaScript, Docker Compose.

---

### Task 1: Lock down version and permission behavior with failing tests

**Files:**
- Modify: `webapp/test_tag_version_update.py`
- Modify: `webapp/test_schedule_automation.py`
- Create: `webapp/test_feature_package.py`
- Modify: `webapp/auth.py`
- Modify: `webapp/server.py`

- [ ] **Step 1: Write failing pure version tests**

Add tests for a Feature package version calculator using the current four-part policy:

```python
def test_feature_package_keeps_existing_fourth_part_when_forcing_week_bump(self):
    self.assertEqual(
        server.feature_package_version("3.1.24.020", [], "2026-07-03T16:00:00+08:00", force_week_bump=True),
        "3.1.25.020",
    )

def test_feature_package_increments_existing_week_fourth_part_without_reset(self):
    tags = ["feature-release_login_T3.1.24.020_202607021000"]
    self.assertEqual(
        server.feature_package_version("3.1.24.020", tags, "2026-07-03T16:00:00+08:00", force_week_bump=False),
        "3.1.24.021",
    )

def test_feature_package_rejects_non_four_part_source_version(self):
    with self.assertRaisesRegex(ValueError, "四段"):
        server.feature_package_version("3.1.24", [], "2026-07-03T16:00:00+08:00", force_week_bump=False)
```

- [ ] **Step 2: Write failing permission and route tests**

Verify that `user` has only the new Feature package permission in addition to current permissions, and that the existing generic Tag route remains admin-only:

```python
def test_user_can_create_feature_package_but_not_generic_tag(self):
    self.assertIn("create_feature_package", auth.ROLE_PERMISSIONS["user"])
    self.assertNotIn("create_tag", auth.ROLE_PERMISSIONS["user"])
```

Exercise the handler route table with a user session and assert `/api/feature-package/create` is allowed while `/api/tags/create` returns 403.

- [ ] **Step 3: Write failing operation tests**

Using the existing `FakeClient` style, cover:

- a valid `feature/*` source creates a `T` Tag;
- `release`, `fix`, `bugfix/*`, arbitrary refs, and user-supplied Tag names are rejected;
- missing `version.info`, malformed version, and missing source branch fail before any write;
- the computed Tag name is deterministic apart from its timestamp;
- duplicate Tag names and concurrent duplicate allocation are rejected or advanced safely;
- the operation creates an `automation/feature-package/...` build ref and tags that ref, without writing the original Feature branch;
- all component prechecks complete before the first write;
- a GitLab failure leaves the original Feature branch unchanged and returns the failed phase.

- [ ] **Step 4: Run only the new tests to verify RED**

Run:

```bash
python3 -m unittest webapp.test_feature_package -v
```

Expected: failures for missing version calculator, permission, route, and Feature operation. Do not implement production code until these failures are observed.

### Task 2: Implement isolated Feature package backend

**Files:**
- Modify: `webapp/auth.py`
- Modify: `webapp/server.py`
- Modify: `webapp/branch_policy.py`
- Modify: `webapp/test_feature_package.py`

- [ ] **Step 1: Implement the pure Feature version helper**

Add a helper that requires exactly four numeric components, reuses `versions_from_tags_in_week` and the existing component-change calculation, keeps the existing fourth part on a forced third-part increment, and never emits a hardcoded `.001` reset. Keep formal release callers unchanged.

- [ ] **Step 2: Add the Feature permission and public capability advertisement**

Add `create_feature_package` to `ROLE_PERMISSIONS` for `user` and `admin`, expose it in `GitOpsApp.public_config()`, and leave `create_tag` admin-only.

- [ ] **Step 3: Implement Feature source and Tag validation**

Require `classify_branch(ref) == "feature"`, disallow custom Tag names, force `T` prefix, require SimOS and all enabled release repositories, and use the existing `feature_fallback_ref` default of `release` only for component resolution. Reject malformed source versions before any write.

- [ ] **Step 4: Implement two-phase precheck and isolated build ref execution**

Resolve every target and commit id first. Build an immutable execution context containing source ref, source commit, calculated version, Tag name, component resolutions, build branch name, and message. Only after all targets pass precheck create the temporary branch/commit and Tag. The original `feature/*` ref is never updated.

- [ ] **Step 5: Add operation locking and idempotency**

Protect Feature version allocation with a dedicated re-entrant lock. Re-read source Tags inside the lock, reject an existing final Tag, and ensure retrying after a partially completed operation reuses the same build ref or returns the recorded result rather than creating a second build.

- [ ] **Step 6: Run the focused tests and existing Tag tests**

Run:

```bash
python3 -m unittest webapp.test_feature_package webapp.test_tag_version_update -v
```

Expected: all focused tests pass and existing generic Tag behavior remains unchanged.

### Task 3: Add persistent local GitLab/CI simulation

**Files:**
- Create: `webapp/simulated_gitlab.py`
- Create: `webapp/data-simulation/repositories.json`
- Create: `webapp/data-simulation/simulation-state.json`
- Modify: `webapp/server.py`
- Modify: `webapp/repository_store.py`
- Create: `docker-compose.simulation.yml`
- Modify: `webapp/.env.example`
- Create: `webapp/test_simulated_gitlab.py`

- [ ] **Step 1: Write failing simulation client tests**

Test that the simulator can read branches, Tags, `version.info`, create a branch, create a Tag, and create a deterministic fake Pipeline without any network call. Test state persistence and reset behavior.

- [ ] **Step 2: Implement the simulator against JSON state**

Implement the existing client surface used by Feature packaging. Store branches, files, Tags, commits, and pipeline records in a caller-provided directory. Return GitLab-shaped dictionaries and raise `GitLabError` with matching status codes for missing refs and duplicate Tags.

- [ ] **Step 3: Add mode-gated client selection**

When `GITOPS_MODE=simulation`, construct only simulation clients, reject non-local GitLab configuration, disable the scheduler, and avoid reading production Tokens or artifact volumes. Keep the default mode unchanged.

- [ ] **Step 4: Add simulation Compose and fixture data**

Bind the simulator to `127.0.0.1`, use a separate port/data directory, provide `user/user123` and `admin/admin123`, and include a Feature branch with a four-part `version.info` plus a prior `T` Tag. No production GitLab host, Token, TLS certificate, or `/data/simos-ci` volume may appear in the simulation Compose file.

- [ ] **Step 5: Run simulation tests**

Run:

```bash
python3 -m unittest webapp.test_simulated_gitlab webapp.test_feature_package -v
docker compose -f docker-compose.simulation.yml config
```

Expected: tests pass; Compose config contains only localhost bindings and simulation data paths.

### Task 4: Add the minimal Feature UI and documentation

**Files:**
- Modify: `webapp/static/index.html`
- Modify: `webapp/static/app.js`
- Modify: `webapp/static/styles.css`
- Modify: `webapp/README.md`
- Modify: `README.md`
- Modify: `webapp/test_schedule_automation.py`
- Modify: `webapp/test_feature_package.py`

- [ ] **Step 1: Write failing static/UI contract tests**

Assert that Feature UI contains the source selector, the unchecked weekly-bump checkbox with the agreed text, a computed `T` version preview, and no generic Tag management controls for user sessions.

- [ ] **Step 2: Implement the Feature package panel**

Use the existing branch data and operation-log helpers. Recompute preview on source/checkbox changes, disable submission when version data is invalid, and display GitLab/Simulation Pipeline status without exposing editable Tag names.

- [ ] **Step 3: Implement capability-aware visibility**

Use advertised permissions rather than only `role === "admin"` for the new Feature package control. Keep existing admin-only navigation and forms unchanged.

- [ ] **Step 4: Document production and simulation workflows**

Document the T prefix, four-part calculation, temporary build ref behavior, real CI side effect, simulation mode, staging GitLab guidance, reset procedure, and the fact that simulation never uses production Tokens or data volumes.

- [ ] **Step 5: Run UI/static tests**

Run:

```bash
python3 -m unittest webapp.test_feature_package webapp.test_schedule_automation -v
```

Expected: all UI contracts pass and existing schedule UI tests remain green.

### Task 5: Full verification and handoff

**Files:**
- Modify only files required by failing verification.

- [ ] **Step 1: Run the complete test suite**

Run:

```bash
python3 -m unittest discover -s webapp -p 'test_*.py' -v
```

Expected: exit code 0 with no failures or errors.

- [ ] **Step 2: Run syntax and Compose checks**

Run:

```bash
python3 -m compileall -q webapp
docker compose -f docker-compose.simulation.yml config
```

Expected: both commands exit 0.

- [ ] **Step 3: Exercise the local simulation server**

Start the simulation Compose stack, log in as `user`, create one normal Feature package and one forced-week package, verify the fake Pipeline records, and confirm the simulation state contains no production URL or Token.

- [ ] **Step 4: Review the diff for scope and secrets**

Run `git diff --check` and inspect changed files for accidental production endpoints, real Tokens, broad permission changes, or writes to the original Feature branch.

- [ ] **Step 5: Update the plan status and report evidence**

Only report completion after all commands above provide fresh successful output; report any unavailable Docker/network check explicitly.
