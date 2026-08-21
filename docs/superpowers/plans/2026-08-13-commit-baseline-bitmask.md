# Commit-Baseline Bitmask Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Calculate formal-release fourth version components from only repository commits that differ from the selected branch's `software.yaml` baseline, while keeping each repository's bit stable.

**Architecture:** `software.yaml.commits` is the immutable comparison baseline for a release preview. Repository configuration persists each code repository's power-of-two `revision_bit`; legacy components retain their current bits and new repositories receive the next unused bit when created. The release plan compares each resolved SHA with the baseline before invoking the existing four-part version calculation.

**Tech Stack:** Python 3, `unittest`, JSON-backed repository configuration, vanilla HTML.

---

### Task 1: Persist Stable Repository Bits

**Files:**
- Modify: `webapp/repository_store.py`
- Modify: `webapp/static/index.html`
- Modify: `webapp/static/app.js`
- Test: `webapp/test_tag_version_update.py`

- [ ] **Step 1: Write failing tests**

```python
repositories = [RepositoryConfig(id="costmap_node", ..., revision_bit=64)]
self.assertEqual(server.repository_revision_bits(repositories), {"costmap_node": 64})
```

- [ ] **Step 2: Run the focused test and observe its missing-field failure.**

```bash
cd webapp && python3 -m unittest test_tag_version_update.TagVersionUpdateTest.test_dynamic_component_bits_are_stable_repository_configuration -v
```

- [ ] **Step 3: Implement `revision_bit` validation and durable auto-assignment.** Legacy SimOS repositories use their existing values; new code repositories use the next unused power of two and retain it when disabled or edited.

- [ ] **Step 4: Show the assigned bit in repository management and preserve it on edit.**

- [ ] **Step 5: Re-run the focused test.**

### Task 2: Compare Against software.yaml Commit Baseline

**Files:**
- Modify: `webapp/server.py`
- Test: `webapp/test_tag_version_update.py`
- Test: `webapp/test_schedule_automation.py`

- [ ] **Step 1: Write the failing baseline comparison test.**

```python
baseline = server.software_yaml_component_commits('commits:\n  main: "simos-old"\n  business: "business-old"\n')
resolutions = [{"component": "simos", "commit_id": "simos-old"}, {"component": "business", "commit_id": "business-new"}]
self.assertEqual(server.changed_components_from_software_baseline(resolutions, baseline), ["business"])
```

- [ ] **Step 2: Run it and verify that parsing/comparison helpers are absent.**

- [ ] **Step 3: Parse the top-level `commits` map, mapping SimOS to the `main` key.** A missing component baseline is treated as changed so legacy manifests safely gain a first record.

- [ ] **Step 4: Make `resolve_release_version_number` pass only differing components and configuration-backed bits to `dynamic_version_from_changed_components`.** Preserve the established single-component addition and multi-component mask behavior; apply `force_week_bump` only after that calculation.

- [ ] **Step 5: Surface only actual changed components in release previews.**

- [ ] **Step 6: Run focused schedule and version tests.**

### Task 3: Regression Verification

**Files:** all modified files above.

- [ ] **Step 1: Run all tests.**

```bash
cd webapp && python3 -m unittest discover -p 'test_*.py' -v
```

- [ ] **Step 2: Verify syntax and diff whitespace.**

```bash
python3 -m py_compile server.py repository_store.py
git diff --check
```

- [ ] **Step 3: Inspect the diff to ensure it does not overwrite unrelated worktree changes.**
