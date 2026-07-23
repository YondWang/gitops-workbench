# 发版任务运行记录清理与界面优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为发版任务页提供单条删除和清空运行记录能力，并让双栏布局和构建配置控件在桌面端更均衡、紧凑。

**Architecture:** 后端继续以 `release_runs.json` 为唯一历史存储源，在 `GitOpsApp` 增加两个最小的记录管理方法，并经现有 HTTP Handler 的 `admin` 鉴权暴露为 DELETE 接口。前端在保留既有发版流程的基础上，新增删除/清空动作、运行记录操作列和仅作用于发版任务页的 CSS 覆盖；数据刷新仍复用 `state.scheduleRuns` 与 `renderSchedules()`。

**Tech Stack:** Python 3 标准库 HTTP 服务与 `unittest`；原生 HTML、CSS、JavaScript。

---

## File structure

- `webapp/server.py` — 添加运行记录删除/清空领域方法和两个管理员 DELETE 路由。
- `webapp/test_schedule_automation.py` — 在已有 `ScheduleAutomationTest` 中测试记录删除、空清理和错误分支。
- `webapp/static/index.html` — 添加最近运行操作栏、清空按钮和表格操作列。
- `webapp/static/app.js` — 调用新的 DELETE 接口，确认操作并重渲染最新运行历史。
- `webapp/static/styles.css` — 仅为发版任务布局、运行记录操作栏及构建配置复选项添加作用域样式。

### Task 1: 运行历史删除后端与测试

**Files:**
- Modify: `webapp/test_schedule_automation.py:221`（`ScheduleAutomationTest` 的任务 CRUD 测试后）
- Modify: `webapp/server.py:293-302`（`delete_release_task` 后）
- Modify: `webapp/server.py:2435-2450`（`Handler.do_DELETE`）

- [ ] **Step 1: 写入会失败的领域层测试**

  在 `ScheduleAutomationTest` 中新增以下三个测试。它们直接构造运行记录并调用将要新增的 `GitOpsApp` 方法，因此既验证文件持久化又不依赖网络：

  ```python
  def test_delete_release_run_removes_only_the_requested_record(self) -> None:
      server.save_release_runs([
          {"id": "run-keep", "tag_name": "keep"},
          {"id": "run-delete", "tag_name": "delete"},
      ])

      result = self.app.delete_release_run("run-delete")

      self.assertTrue(result["ok"])
      self.assertEqual(result["deleted"], "run-delete")
      self.assertEqual([run["id"] for run in result["runs"]], ["run-keep"])
      self.assertEqual([run["id"] for run in server.load_release_runs()], ["run-keep"])

  def test_delete_release_run_rejects_an_unknown_record(self) -> None:
      server.save_release_runs([{"id": "run-keep"}])

      with self.assertRaisesRegex(ValueError, "发版运行不存在：run-missing"):
          self.app.delete_release_run("run-missing")

      self.assertEqual([run["id"] for run in server.load_release_runs()], ["run-keep"])

  def test_clear_release_runs_persists_an_empty_history(self) -> None:
      server.save_release_runs([{"id": "run-a"}, {"id": "run-b"}])

      result = self.app.clear_release_runs()

      self.assertTrue(result["ok"])
      self.assertEqual(result["deleted_count"], 2)
      self.assertEqual(result["runs"], [])
      self.assertEqual(server.load_release_runs(), [])
  ```

- [ ] **Step 2: 运行新测试，确认其因为方法不存在而失败**

  Run:

  ```bash
  python3 -m unittest webapp.test_schedule_automation.ScheduleAutomationTest.test_delete_release_run_removes_only_the_requested_record webapp.test_schedule_automation.ScheduleAutomationTest.test_delete_release_run_rejects_an_unknown_record webapp.test_schedule_automation.ScheduleAutomationTest.test_clear_release_runs_persists_an_empty_history -v
  ```

  Expected: FAIL，提示 `GitOpsApp` 没有 `delete_release_run` 或 `clear_release_runs` 属性。

- [ ] **Step 3: 添加最小的记录管理方法**

  在 `GitOpsApp.delete_release_task` 后添加下面的方法；不要修改 `save_release_runs` 的 300 条保留上限，也不要调用任何 GitLab 客户端：

  ```python
  def delete_release_run(self, run_id: str) -> dict[str, Any]:
      run_id = str(run_id or "").strip()
      if not run_id:
          raise ValueError("发版运行 ID 不能为空")
      runs = load_release_runs()
      next_runs = [item for item in runs if item.get("id") != run_id]
      if len(next_runs) == len(runs):
          raise ValueError(f"发版运行不存在：{run_id}")
      save_release_runs(next_runs)
      return {"ok": True, "deleted": run_id, "runs": load_release_runs()}

  def clear_release_runs(self) -> dict[str, Any]:
      deleted_count = len(load_release_runs())
      save_release_runs([])
      return {"ok": True, "deleted_count": deleted_count, "runs": []}
  ```

- [ ] **Step 4: 将两个操作接入管理员 DELETE 路由**

  在 `Handler.do_DELETE` 的开头、现有 `schedule_route` 判断之前插入以下逻辑。`/api/release-runs` 必须先精确匹配，随后才允许单条路径匹配：

  ```python
  if path == "/api/release-runs":
      self.handle_api("admin", app.clear_release_runs)
      return
  release_run_id = match_release_run_path(path)
  if release_run_id:
      self.handle_api("admin", lambda run_id=release_run_id: app.delete_release_run(run_id))
      return
  ```

  结果是：

  - `DELETE /api/release-runs/{run_id}` 删除一个记录；
  - `DELETE /api/release-runs` 清空记录；
  - 两条路径都使用既有 `admin` 鉴权和错误 JSON 格式。

- [ ] **Step 5: 运行测试，确认记录操作通过且既有任务删除语义未回归**

  Run:

  ```bash
  python3 -m unittest webapp.test_schedule_automation.ScheduleAutomationTest.test_delete_release_run_removes_only_the_requested_record webapp.test_schedule_automation.ScheduleAutomationTest.test_delete_release_run_rejects_an_unknown_record webapp.test_schedule_automation.ScheduleAutomationTest.test_clear_release_runs_persists_an_empty_history webapp.test_schedule_automation.ScheduleAutomationTest.test_schedule_crud_supports_multiple_tasks -v
  ```

  Expected: PASS，4 个测试全部通过。

- [ ] **Step 6: 提交后端和测试修改**

  ```bash
  git add webapp/server.py webapp/test_schedule_automation.py
  git commit -m "feat: manage release run history"
  ```

### Task 2: 最近运行的删除与清空交互

**Files:**
- Modify: `webapp/static/index.html:436-455`
- Modify: `webapp/static/app.js:346-375`
- Modify: `webapp/static/app.js:1004-1011`
- Modify: `webapp/static/app.js:1114-1119`

- [ ] **Step 1: 扩展“最近运行”标题栏和表头**

  将现有的刷新按钮换成一个操作区，并增加一列表头：

  ```html
  <div class="panel-title">
    <h2>最近运行</h2>
    <div class="title-actions">
      <button id="refreshSchedulesBtn" type="button">刷新</button>
      <button id="clearScheduleRunsBtn" class="danger small" type="button">清空全部</button>
    </div>
  </div>
  ```

  在同一张表的 `云盘目录` 表头之后添加：

  ```html
  <th>操作</th>
  ```

- [ ] **Step 2: 修改渲染函数，先得到会失败的静态断言**

  在 `renderScheduleRuns` 的每一行云盘目录 `<td>` 之后添加操作单元格；继续发版按钮仍留在云盘目录单元格内：

  ```javascript
  <td>
    <button class="danger small" type="button" data-delete-run="${escapeHtml(run.id)}">删除</button>
  </td>
  ```

  将空状态列跨度由 `4` 改为 `5`：

  ```javascript
  .join("") || `<tr><td colspan="5">暂无运行记录</td></tr>`;
  ```

  此时浏览器手工检查可看到按钮，但点击没有行为，作为下一步的失败基线。

- [ ] **Step 3: 实现前端删除与清空函数**

  在 `continueReleaseRun` 后添加以下函数。确认取消时不发请求；成功时直接采用接口返回的完整列表，不额外刷新不相关的数据：

  ```javascript
  async function deleteReleaseRun(runId) {
    if (!confirm("确认删除这条发版运行记录？此操作不会删除 Tag、流水线或构建产物。")) return;
    const result = await api(`/api/release-runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
    state.scheduleRuns = result.runs || [];
    appendLog("删除发版运行记录", result);
    renderSchedules();
  }

  async function clearReleaseRuns() {
    if (!confirm("确认清空全部发版运行记录？此操作不会删除 Tag、流水线或构建产物。")) return;
    const result = await api("/api/release-runs", { method: "DELETE" });
    state.scheduleRuns = result.runs || [];
    appendLog("清空发版运行记录", result);
    renderSchedules();
  }
  ```

- [ ] **Step 4: 绑定清空按钮与表格委托事件**

  在 `refreshSchedulesBtn` 的既有绑定之后加入：

  ```javascript
  $("#clearScheduleRunsBtn")?.addEventListener("click", () => clearReleaseRuns().catch((error) => appendLog("清空发版运行记录失败", error.message)));
  ```

  将既有 `scheduleRunsBody` 点击处理改为同时读取 `data-delete-run`。继续动作优先，其后才处理删除：

  ```javascript
  $("#scheduleRunsBody")?.addEventListener("click", (event) => {
    const runId = event.target.dataset.continueRun;
    const deleteRunId = event.target.dataset.deleteRun;
    if (runId) {
      continueReleaseRun(runId).catch((error) => appendLog("继续发版任务失败", error.message));
    }
    if (deleteRunId) {
      deleteReleaseRun(deleteRunId).catch((error) => appendLog("删除发版运行记录失败", error.message));
    }
  });
  ```

- [ ] **Step 5: 本地验证交互请求与状态更新**

  启动服务并以管理员登录：

  ```bash
  cd webapp && python3 server.py --host 127.0.0.1 --port 8765
  ```

  在“发版任务 -> 最近运行”中确认：单条删除显示确认提示，确认后该行消失；“清空全部”显示确认提示，确认后显示“暂无运行记录”；取消任意确认后列表和网络请求都不改变。检查服务日志没有 404、403 或 500。

- [ ] **Step 6: 提交 HTML 与 JavaScript 修改**

  ```bash
  git add webapp/static/index.html webapp/static/app.js
  git commit -m "feat: add release run cleanup controls"
  ```

### Task 3: 双栏填充与构建配置复选项美化

**Files:**
- Modify: `webapp/static/styles.css:156-160`（双栏网格规则后）
- Modify: `webapp/static/styles.css:164-172`（面板宽度规则后）
- Modify: `webapp/static/styles.css:238-256`（通用表单控件规则后）

- [ ] **Step 1: 添加仅作用于发版任务页的等宽双栏规则**

  在 `.grid.two` 规则之后添加：

  ```css
  #schedules .grid.two {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-items: stretch;
  }

  #schedules .form-panel {
    max-width: none;
  }
  ```

  该选择器只覆盖发版任务页；现有其它视图仍使用原有的 `1.4fr / 0.6fr` 网格。既有 `@media (max-width: 980px)` 中的 `.grid.two { grid-template-columns: 1fr; }` 保持不变，因此移动端自动单列。

- [ ] **Step 2: 添加标题操作区、运行记录操作列和紧凑构建配置样式**

  在通用表单输入样式之后添加：

  ```css
  .title-actions {
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }

  #scheduleRunsBody td:last-child {
    width: 1%;
    white-space: nowrap;
  }

  .inline-options {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0 0 13px;
    padding: 10px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--surface-soft);
  }

  .inline-options legend {
    padding: 0 5px;
    color: var(--muted);
    font-size: 13px;
  }

  .inline-options label {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    padding: 7px 10px;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
  }

  .inline-options label:has(input:checked) {
    border-color: #5eead4;
    background: #f0fdfa;
    color: #115e59;
  }

  .inline-options input[type="checkbox"] {
    width: 18px;
    min-height: 18px;
    height: 18px;
    margin: 0;
    accent-color: var(--primary);
  }

  .inline-options label:focus-within {
    outline: 2px solid #5eead4;
    outline-offset: 2px;
  }
  ```

  `:has()` 只用于视觉高亮，复选框自身仍是原生可访问控件；`focus-within` 保证键盘焦点可见。

- [ ] **Step 3: 在宽屏与窄屏做静态布局检查**

  使用浏览器开发工具或本地截图检查 `http://127.0.0.1:8765`：

  1. 在至少 1280px 宽度下，左、右发版任务面板为等宽并撑满工作区，右侧不再出现狭窄列。
  2. 两处构建配置的复选框显示为 18px 方块，标签为紧凑横向卡片，已选择项为浅青色。
  3. 在 980px 以下，页面成为单列，不出现横向滚动、表格重叠或按钮遮挡。

- [ ] **Step 4: 提交样式修改**

  ```bash
  git add webapp/static/styles.css
  git commit -m "style: polish release task layout"
  ```

### Task 4: 完整回归与交付检查

**Files:**
- Verify: `webapp/server.py`
- Verify: `webapp/test_schedule_automation.py`
- Verify: `webapp/static/index.html`
- Verify: `webapp/static/app.js`
- Verify: `webapp/static/styles.css`

- [ ] **Step 1: 运行完整 Python 回归测试**

  Run:

  ```bash
  cd webapp && python3 -m unittest discover -v
  ```

  Expected: PASS；现有发版、Tag、resident 包与自动任务测试不发生失败。

- [ ] **Step 2: 检查变更范围与空白错误**

  Run:

  ```bash
  git diff --check
  git status --short
  ```

  Expected: `git diff --check` 无输出；状态只包含本功能涉及的静态文件、后端文件、测试文件与已确认的设计/计划文档。

- [ ] **Step 3: 复核风险边界**

  在交付说明中明确：删除仅会改写 `release_runs.json`，不会删除 Tag、Pipeline、MR、云盘目录或构建产物；浏览器运行时确认框可以阻止误点，但历史删除不可恢复。

- [ ] **Step 4: 创建交付提交**

  ```bash
  git add webapp/server.py webapp/test_schedule_automation.py webapp/static/index.html webapp/static/app.js webapp/static/styles.css
  git commit -m "feat: improve release task operations"
  ```

  如果前面各任务已按步骤提交，此步骤只在当前项目要求压缩提交或仍有未提交的功能修改时执行；不要创建空提交。

## Plan self-review

- **Spec coverage:** Task 1 覆盖管理员删除/清空、错误处理与持久化；Task 2 覆盖确认、前端列表刷新与操作列；Task 3 覆盖等宽填充、窄屏降级和复选框视觉；Task 4 覆盖完整回归与删除范围说明。
- **Placeholder scan:** 已检查文档，没有未决占位、未定义函数名称或“按需处理”类步骤。
- **Type consistency:** 后端方法固定为 `delete_release_run(run_id: str)` 和 `clear_release_runs()`；接口和前端路径统一为 `/api/release-runs`；接口响应统一含 `runs`，前端统一写入 `state.scheduleRuns`。
