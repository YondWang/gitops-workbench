# Configurable Feature Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow automatic and manual full releases to select the branch used when a non-SimOS repository lacks the requested `feature/*` branch.

**Architecture:** Store one normalized `feature_fallback_ref` on release tasks, defaulting to `release` for backward compatibility. Pass it through manual payload construction and release-plan resolution to the existing per-component resolver, which snapshots the configured fallback branch only for missing Feature branches.

**Tech Stack:** Python 3 standard-library HTTP application and `unittest`; static HTML and browser JavaScript.

---

## File structure

- `webapp/server.py`: task schema normalization, manual payload mapping, release-plan propagation, component fallback resolution.
- `webapp/static/index.html`: automatic-task and manual-release input controls and corrected help text.
- `webapp/static/app.js`: default form value and resolution status label.
- `webapp/test_tag_version_update.py`: resolver-level fallback behavior tests.
- `webapp/test_schedule_automation.py`: automatic/manual persistence and static-page integration tests.

### Task 1: Specify backend fallback behavior

**Files:**

- Modify: `webapp/test_tag_version_update.py:750-772`
- Modify: `webapp/test_schedule_automation.py:180-240`

- [ ] **Step 1: Write failing resolver tests**

```python
resolutions = self.app.resolve_full_release_components("feature/ABC", "bugfix/V1.2.3")
self.assertEqual(by_repository["gitops-workbench"]["resolved_ref"], "bugfix/V1.2.3")
self.assertEqual(by_repository["gitops-workbench"]["resolution"], "fallback_ref")
```

```python
with self.assertRaisesRegex(ValueError, "gitops-workbench.*bugfix/V1.2.3"):
    self.app.resolve_full_release_components("feature/ABC", "bugfix/V1.2.3")
```

- [ ] **Step 2: Run the targeted tests to verify failure**

Run: `python3 -m unittest webapp.test_tag_version_update.TagVersionUpdateTest.test_feature_release_resolves_each_component_or_falls_back_to_selected_ref`

Expected: FAIL because `resolve_full_release_components` has no fallback argument and current code only selects `release`.

- [ ] **Step 3: Write failing task-entry tests**

```python
saved = self.app.save_schedule({"id": "daily-simos-resident-release", "feature_fallback_ref": "bugfix/V1.2.3"})
self.assertEqual(saved["task"]["feature_fallback_ref"], "bugfix/V1.2.3")
```

```python
result = self.app.manual_release_run({"source_ref": "feature/ABC", "feature_fallback_ref": "bugfix/V1.2.3"})
self.assertEqual(result["task"]["feature_fallback_ref"], "bugfix/V1.2.3")
```

- [ ] **Step 4: Run the task-entry tests to verify failure**

Run: `python3 -m unittest webapp.test_schedule_automation.ScheduleAutomationTest.test_schedule_persists_feature_fallback_ref webapp.test_schedule_automation.ScheduleAutomationTest.test_manual_release_uses_feature_fallback_ref`

Expected: FAIL because the schema does not retain or use `feature_fallback_ref`.

### Task 2: Implement the minimal backend propagation

**Files:**

- Modify: `webapp/server.py:381-402`
- Modify: `webapp/server.py:596-639`
- Modify: `webapp/server.py:641-699`
- Modify: `webapp/server.py:2593-2632`

- [ ] **Step 1: Add defaulting and validation in task normalization**

```python
fallback_ref = str(task.get("feature_fallback_ref") or "release").strip()
task["feature_fallback_ref"] = require_ref_name(fallback_ref, "Feature 缺失时回退分支")
```

- [ ] **Step 2: Propagate the field from manual payload and release plan**

```python
"feature_fallback_ref": str(payload.get("feature_fallback_ref") or "release"),
...
component_resolutions = self.resolve_full_release_components(ref, str(schedule.get("feature_fallback_ref") or "release"))
```

- [ ] **Step 3: Resolve the selected branch for missing Feature components**

```python
elif self.is_feature_release_ref(requested_ref) and requested_ref not in branches:
    if fallback_ref not in branches:
        raise ValueError(f"{repository.id} 不存在 Feature 分支 {requested_ref}，且回退分支不存在：{fallback_ref}")
    resolved_ref = fallback_ref
    resolution = "fallback_ref"
```

- [ ] **Step 4: Run targeted backend tests to verify success**

Run: `python3 -m unittest webapp.test_tag_version_update webapp.test_schedule_automation`

Expected: PASS.

### Task 3: Expose the setting in both user flows

**Files:**

- Modify: `webapp/static/index.html:304-350`
- Modify: `webapp/static/index.html:384-430`
- Modify: `webapp/static/app.js:606-626`
- Modify: `webapp/static/app.js:470-500`
- Modify: `webapp/test_schedule_automation.py:235-244`

- [ ] **Step 1: Write failing static-page assertions**

```python
self.assertEqual(index.count('name="feature_fallback_ref"'), 2)
self.assertIn('item.resolution === "fallback_ref"', app_js)
```

- [ ] **Step 2: Run the static-page test to verify failure**

Run: `python3 -m unittest webapp.test_schedule_automation.ScheduleAutomationTest.test_manual_release_page_exposes_feature_fallback_ref`

Expected: FAIL because neither form contains the field and the renderer recognizes only `fallback_release`.

- [ ] **Step 3: Add the two fields and rendering support**

```html
<label>
  Feature 缺失时回退分支
  <input name="feature_fallback_ref" value="release" required />
</label>
```

```javascript
feature_fallback_ref: "release",
...
item.resolution === "fallback_ref" ? `回退 ${item.resolved_ref || "-"}` : "请求分支"
```

- [ ] **Step 4: Run the static-page test to verify success**

Run: `python3 -m unittest webapp.test_schedule_automation.ScheduleAutomationTest.test_manual_release_page_exposes_feature_fallback_ref`

Expected: PASS.

### Task 4: Full regression verification

**Files:**

- Test: `webapp/test_*.py`

- [ ] **Step 1: Run the complete backend test suite**

Run: `python3 -m unittest discover -s webapp -p 'test_*.py'`

Expected: PASS with no test failures.

- [ ] **Step 2: Inspect the final diff**

Run: `git diff --check && git diff -- webapp/server.py webapp/static/index.html webapp/static/app.js webapp/test_tag_version_update.py webapp/test_schedule_automation.py`

Expected: no whitespace errors; only planned fallback-field changes.
