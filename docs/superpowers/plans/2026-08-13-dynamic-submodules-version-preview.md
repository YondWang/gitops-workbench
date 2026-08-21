# Dynamic Submodules And Version Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Make enabled repositories dynamically synchronize SimOS submodule pointers and provide software.yaml-based version calculation and admin release preview.

**Architecture:** Persist an explicit `submodule_path` on repository configs. The server derives release components and gitlink paths from enabled repository configs, while one shared version resolver serves preview and execution. The existing static admin form gains a weekly-bump control and debounced preview request.

**Tech Stack:** Python 3, `unittest`, stdlib HTTP server, vanilla HTML/CSS/JavaScript.

---

### Task 1: Dynamic Repository Model

**Files:** `webapp/repository_store.py`, `webapp/server.py`, `webapp/static/index.html`, `webapp/static/app.js`, related tests.

- [ ] Add `submodule_path: str = ""` to `RepositoryConfig`, normalize it, and expose it through `public_dict`.
- [ ] Add the field to repository add/edit form, load it when editing, and render it in the repository table.
- [ ] Add failing tests proving persistence and validation of enabled code repositories.
- [ ] Run the focused tests and verify they fail before implementation.
- [ ] Implement the model and UI changes, then run the focused tests.

### Task 2: Dynamic Release Snapshots And Gitlinks

**Files:** `webapp/server.py`, `webapp/test_tag_version_update.py`, `webapp/test_schedule_automation.py`.

- [ ] Add failing tests with two new enabled repositories and distinct paths such as `src/costmap_node` and `src/laser_filter`.
- [ ] Assert component resolution includes both repositories and `submodule_update_paths` returns both paths.
- [ ] Assert the git-based commit emits `update-index --add --cacheinfo 160000` for every configured path.
- [ ] Replace fixed component lists in release planning, rendering, and gitlink update with repository-derived metadata while preserving SimOS/config exclusions.
- [ ] Add precheck errors for missing, duplicate, or invalid paths.
- [ ] Run focused and existing release tests.

### Task 3: Remote software.yaml Version Rules

**Files:** `webapp/server.py`, release tests.

- [ ] Add failing tests for reading `version` from remote `software.yaml`, malformed versions, and weekly third-component increments.
- [ ] Implement a strict parser for the remote YAML version scalar and use it as the release baseline.
- [ ] Thread `force_week_bump` through schedule normalization, plan resolution, manual release payloads, and version rendering.
- [ ] Ensure preview and execution call the same calculation.
- [ ] Run focused version tests.

### Task 4: Admin Manual Release Preview

**Files:** `webapp/server.py`, `webapp/static/index.html`, `webapp/static/app.js`, `webapp/static/styles.css`, tests.

- [ ] Add a GET preview route for manual release inputs.
- [ ] Add the admin form checkbox and readonly preview area.
- [ ] Trigger preview after source ref, fallback ref, prefix, or weekly-bump changes and display current/next version, tag, changed components, and source commits.
- [ ] Include the same `force_week_bump` value in the actual manual release request.
- [ ] Run browser-adjacent static checks and server tests.

### Task 5: Verification

- [ ] Run `python3 -m unittest discover -s webapp -p 'test_*.py' -v`.
- [ ] Run `git diff --check`.
- [ ] Inspect the final diff for unrelated changes and confirm existing user changes remain intact.
