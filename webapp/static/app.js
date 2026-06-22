const state = {
  session: null,
  config: null,
  repositories: [],
  currentRepositoryId: "",
  branches: [],
  tags: [],
  commonRefs: null,
  log: [],
  pendingVersionTag: null,
  pendingVersionTimer: null,
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function postJson(path, body) {
  return api(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

function formValues(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  form.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    data[input.name] = input.checked;
  });
  return data;
}

function operationBody(form) {
  return {
    ...formValues(form),
    repository_id: state.currentRepositoryId,
  };
}

function currentRepository() {
  return state.repositories.find((repo) => repo.id === state.currentRepositoryId) || state.repositories[0] || null;
}

function appendLog(title, payload) {
  const stamp = new Date().toLocaleTimeString();
  const body = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  state.log.unshift(`[${stamp}] ${title}\n${body}`);
  $("#logOutput").textContent = state.log.join("\n\n");
}

function showLoginMessage(text, isError = false) {
  const message = $("#loginMessage");
  message.textContent = text || "";
  message.className = `message ${isError ? "error" : "ok"}`;
}

function switchView(viewId) {
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === viewId));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active-view", view.id === viewId));
}

function renderSession() {
  const loggedIn = Boolean(state.session);
  $("#loginView").classList.toggle("hidden", loggedIn);
  $("#appView").classList.toggle("hidden", !loggedIn);
  if (!loggedIn) return;

  $("#currentUser").textContent = state.session.username;
  $("#currentRole").textContent = state.session.role;
  $("#currentRole").className = `badge ${state.session.role === "admin" ? "ok" : "muted"}`;

  const isAdmin = state.session.role === "admin";
  document.querySelectorAll("[data-admin-only]").forEach((el) => el.classList.toggle("hidden", !isAdmin));
  document.querySelectorAll("[data-user-block]").forEach((el) => el.classList.toggle("hidden", isAdmin));
  if (!isAdmin && ["release", "bugfix", "tag", "repositories"].some((id) => $(`#${id}`).classList.contains("active-view"))) {
    switchView("overview");
  }
}

function renderConfig() {
  if (!state.config) return;
  const repo = currentRepository();
  $("#projectName").textContent = repo ? `${repo.name} / ${repo.project}` : "暂无仓库";
  $("#configOutput").textContent = JSON.stringify(state.config, null, 2);
}

function renderRepositorySelect() {
  const select = $("#repositorySelect");
  const previous = state.currentRepositoryId || select.value;
  select.innerHTML =
    state.repositories
      .map((repo) => `<option value="${escapeHtml(repo.id)}">${escapeHtml(repo.name)} · ${escapeHtml(repo.project)}</option>`)
      .join("") || `<option value="">暂无仓库</option>`;
  if (state.repositories.some((repo) => repo.id === previous)) {
    state.currentRepositoryId = previous;
  } else {
    state.currentRepositoryId = state.config?.default_repository_id || state.repositories[0]?.id || "";
  }
  select.value = state.currentRepositoryId;
}

function renderRepositories() {
  $("#repoCount").textContent = String(state.repositories.length);
  $("#repositoriesBody").innerHTML =
    state.repositories
      .map(
        (repo) => `
          <tr>
            <td><code>${escapeHtml(repo.id)}</code></td>
            <td>
              <strong>${escapeHtml(repo.name)}</strong>
              <div class="meta">${escapeHtml(repo.base_url)} / ${escapeHtml(repo.project)}</div>
            </td>
            <td>${repo.token_loaded ? "已加载" : "未加载"} <div class="meta">${escapeHtml(repo.token_env)}</div></td>
            <td>${repo.enabled ? "启用" : "停用"}</td>
            <td>
              <button class="secondary small" data-edit-repo="${escapeHtml(repo.id)}">编辑</button>
              <button class="danger small" data-delete-repo="${escapeHtml(repo.id)}">删除</button>
            </td>
          </tr>
        `,
      )
      .join("") || `<tr><td colspan="5">暂无仓库配置</td></tr>`;
}

function renderBranches() {
  const kind = $("#branchTypeFilter")?.value || "all";
  const branches = kind === "all" ? state.branches : state.branches.filter((branch) => branch.kind === kind);
  $("#branchCount").textContent = kind === "all" ? String(branches.length) : `${branches.length}/${state.branches.length}`;
  $("#branchesBody").innerHTML =
    branches
      .map(
        (branch) => `
          <tr>
            <td>${branch.web_url ? `<a href="${escapeHtml(branch.web_url)}" target="_blank"><code>${escapeHtml(branch.name)}</code></a>` : `<code>${escapeHtml(branch.name)}</code>`}</td>
            <td><span class="pill">${escapeHtml(branch.kind)}</span></td>
            <td>${branch.protected ? "是" : "否"}</td>
            <td><code>${escapeHtml(branch.commit_id || "")}</code> ${escapeHtml(branch.commit_title || "")}</td>
          </tr>
        `,
      )
      .join("") || `<tr><td colspan="4">暂无分支或未读取到数据</td></tr>`;
  renderSelectOptions();
}

function renderTags() {
  $("#tagCount").textContent = String(state.tags.length);
  $("#tagList").innerHTML =
    state.tags
      .slice(0, 30)
      .map(
        (tag) => `
          <li>
            <strong>${escapeHtml(tag.name)}</strong>
            <div class="meta"><code>${escapeHtml(tag.commit_id || tag.target || "")}</code> ${escapeHtml(tag.commit_title || "")}</div>
          </li>
        `,
      )
      .join("") || "<li>暂无 Tag</li>";
}

function renderSelectOptions() {
  const releaseRefs = sourceOptions(scopeValue("#releaseForm"));
  const featureRefs = sourceOptions(scopeValue("#featureForm"));
  const bugfixRefs = sourceOptions(scopeValue("#bugfixForm"));
  const tagRefs = sourceOptions(scopeValue("#tagForm"));

  fillSelect("#releaseRef", releaseRefs.refs);
  fillSelect("#featureRef", featureRefs.featureSources, "release");
  fillSelect("#bugfixRef", bugfixRefs.refs, "release");
  fillSelect("#tagRef", tagRefs.branches);
  syncTagUpdateVersionControl();
}

function scopeValue(formSelector) {
  const form = $(formSelector);
  return form?.elements.scope?.value || "single";
}

function sourceOptions(scope) {
  const source =
    scope === "all"
      ? {
          branches: state.commonRefs?.branches || [],
          tags: state.commonRefs?.tags || [],
          featureSources: state.commonRefs?.feature_sources || [],
        }
      : {
          branches: state.branches,
          tags: state.tags,
          featureSources: state.branches.filter((item) => item.kind === "release" || item.kind === "bugfix"),
        };
  const branches = source.branches.map((item) => item.name).sort((a, b) => a.localeCompare(b));
  const tags = source.tags.map((item) => item.name).sort((a, b) => a.localeCompare(b));
  const featureSources = source.featureSources.map((item) => item.name).sort((a, b) => a.localeCompare(b));
  return { branches, tags, refs: [...branches, ...tags], featureSources };
}

function syncTagUpdateVersionControl() {
  const form = $("#tagForm");
  if (!form?.elements.update_version) return;
  const checkbox = form.elements.update_version;
  const enabled = form.elements.scope?.value === "all";
  checkbox.disabled = !enabled;
  checkbox.title = enabled ? "" : "仅在全部启用仓库范围可用";
  checkbox.closest(".checkline")?.classList.toggle("disabled", !enabled);
  if (!enabled) {
    checkbox.checked = false;
  }
}

function fillSelect(selector, values, preferred = "") {
  const select = $(selector);
  if (!select) return;
  const previous = select.value;
  select.innerHTML =
    values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("") ||
    `<option value="">暂无可选项</option>`;
  if (values.includes(previous)) {
    select.value = previous;
  } else if (preferred && values.includes(preferred)) {
    select.value = preferred;
  }
}

async function refreshAll() {
  const config = await api("/api/config");
  state.config = config;
  state.repositories = config.repositories || [];
  renderRepositorySelect();
  renderRepositories();
  renderConfig();

  if (!state.currentRepositoryId) {
    state.branches = [];
    state.tags = [];
    state.commonRefs = null;
    renderBranches();
    renderTags();
    return;
  }

  const search = $("#branchSearch").value.trim();
  const params = new URLSearchParams({ repository_id: state.currentRepositoryId });
  if (search) params.set("search", search);
  const [branches, tags, commonRefs] = await Promise.all([
    api(`/api/branches?${params}`),
    api(`/api/tags?${params}`),
    api("/api/common-refs").catch((error) => {
      appendLog("刷新公共来源失败", error.message);
      return null;
    }),
  ]);
  state.branches = branches.branches || [];
  state.tags = tags.tags || [];
  state.commonRefs = commonRefs;
  renderBranches();
  renderTags();
}

async function loadSession() {
  const data = await api("/api/session");
  state.session = data.session;
  renderSession();
  if (state.session) {
    await refreshAll().catch((error) => appendLog("刷新失败", error.message));
  }
}

async function handleOperation(title, path, form) {
  const body = operationBody(form);
  const result = await postJson(path, body);
  appendLog(title, summarizeOperationResult(result));
  if (path === "/api/tags/create") {
    handleTagOperationResult(body, result);
  }
  await refreshAll();
}

function handleTagOperationResult(body, result) {
  if (result?.phase === "waiting_version_mr" && result.merge_request) {
    const nextBody = {
      ...body,
      tag_name: result.tag_name || body.tag_name,
      version_update_branch: result.version_update?.branch,
    };
    state.pendingVersionTag = {
      body: nextBody,
      mergeRequest: result.merge_request,
      startedAt: Date.now(),
    };
    renderPendingVersionTag();
    appendLog("等待版本号 MR 合并", {
      merge_request: result.merge_request.web_url || result.merge_request,
      source_branch: result.version_update?.branch,
      next: "MR 合并后将自动继续创建 Tag",
    });
    $("#logOutput").dataset.pendingTag = JSON.stringify(nextBody);
    scheduleVersionTagPoll();
    return;
  }
  if (result?.phase === "version_update_aborted" || result?.terminated) {
    const mergeRequest = result.merge_request || state.pendingVersionTag?.mergeRequest;
    clearVersionTagPoll();
    appendLog("已终止发版或打 Tag 流程", {
      reason: result.message || "版本号更新 MR 已关闭",
      merge_request: mergeRequest?.web_url || mergeRequest || "",
    });
    return;
  }
  if (result?.ok && state.pendingVersionTag) {
    clearVersionTagPoll();
    appendLog("版本号 MR 已合并", "已继续完成 Tag 创建");
  }
}

function renderPendingVersionTag() {
  const panel = $("#pendingVersionTag");
  if (!panel) return;
  const pending = state.pendingVersionTag;
  panel.classList.toggle("hidden", !pending);
  if (!pending) return;

  const mergeRequest = pending.mergeRequest || {};
  const webUrl = typeof mergeRequest === "object" ? mergeRequest.web_url || "" : "";
  const stateText = typeof mergeRequest === "object" ? mergeRequest.state || "opened" : "opened";
  $("#pendingVersionTagMeta").textContent = `${pending.body.tag_name || "待创建 Tag"} · ${pending.body.version_update_branch || "版本更新分支"} · MR ${stateText}`;
  const link = $("#pendingVersionTagLink");
  link.classList.toggle("hidden", !webUrl);
  link.href = webUrl || "#";
}

function abortPendingVersionTag() {
  const pending = state.pendingVersionTag;
  if (!pending) return;
  const mergeRequest = pending.mergeRequest || {};
  clearVersionTagPoll();
  appendLog("已终止发版或打 Tag 流程", {
    tag_name: pending.body.tag_name,
    merge_request: mergeRequest.web_url || mergeRequest,
    note: "已停止自动检查版本号 MR，不会继续自动创建 Tag",
  });
}

function scheduleVersionTagPoll() {
  if (state.pendingVersionTimer) {
    clearTimeout(state.pendingVersionTimer);
  }
  state.pendingVersionTimer = setTimeout(pollPendingVersionTag, 8000);
}

function clearVersionTagPoll() {
  if (state.pendingVersionTimer) {
    clearTimeout(state.pendingVersionTimer);
  }
  state.pendingVersionTimer = null;
  state.pendingVersionTag = null;
  delete $("#logOutput").dataset.pendingTag;
  renderPendingVersionTag();
}

async function pollPendingVersionTag() {
  const pending = state.pendingVersionTag;
  if (!pending) return;
  try {
    const result = await postJson("/api/tags/create", pending.body);
    appendLog("检查版本号 MR 状态", summarizeOperationResult(result));
    if (result?.phase === "waiting_version_mr") {
      state.pendingVersionTag.mergeRequest = result.merge_request || pending.mergeRequest;
      renderPendingVersionTag();
      scheduleVersionTagPoll();
      return;
    }
    handleTagOperationResult(pending.body, result);
    await refreshAll();
  } catch (error) {
    appendLog("检查版本号 MR 状态失败", error.message);
    scheduleVersionTagPoll();
  }
}

function summarizeOperationResult(result) {
  if (!result || !Array.isArray(result.results)) return result;
  return {
    ok: result.ok,
    operation: result.operation,
    phase: result.phase,
    precheck: result.precheck?.map((item) => ({
      repository: item.repository?.id,
      ok: item.ok,
      error: item.error,
      context: item.context,
    })),
    results: result.results.map((item) => ({
      repository: item.repository?.id,
      ok: item.ok,
      error: item.error,
      result: item.result,
    })),
    blocked: result.blocked,
    terminated: result.terminated,
    message: result.message,
    tag_name: result.tag_name,
    version_update: result.version_update,
    merge_request: result.merge_request,
  };
}

function fillRepositoryForm(repo) {
  const form = $("#repositoryForm");
  form.elements.id.value = repo?.id || "";
  form.elements.id.readOnly = Boolean(repo);
  form.elements.name.value = repo?.name || "";
  form.elements.base_url.value = repo?.base_url || "https://www.chancee-shanghai.cn:9900";
  form.elements.project.value = repo?.project || "";
  form.elements.default_ref.value = repo?.default_ref || "main";
  form.elements.token_env.value = repo?.token_env || "GITLAB_TOKEN";
  form.elements.enabled.checked = repo ? Boolean(repo.enabled) : true;
  form.elements.ssl_verify.checked = repo ? Boolean(repo.ssl_verify) : true;
}

async function saveRepository(form) {
  const body = formValues(form);
  const exists = state.repositories.some((repo) => repo.id === body.id);
  const path = exists ? `/api/repositories/${encodeURIComponent(body.id)}` : "/api/repositories";
  const method = exists ? "PUT" : "POST";
  const result = await api(path, { method, body: JSON.stringify(body) });
  appendLog(exists ? "编辑仓库" : "添加仓库", result);
  await refreshAll();
  fillRepositoryForm(null);
}

function bindEvents() {
  $("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    showLoginMessage("");
    try {
      const data = await postJson("/api/login", formValues(event.currentTarget));
      state.session = data.session;
      renderSession();
      await refreshAll().catch((error) => appendLog("刷新失败", error.message));
    } catch (error) {
      showLoginMessage(error.message, true);
    }
  });

  $("#logoutBtn").addEventListener("click", async () => {
    await postJson("/api/logout", {});
    state.session = null;
    renderSession();
  });

  $("#repositorySelect").addEventListener("change", async (event) => {
    state.currentRepositoryId = event.currentTarget.value;
    await refreshAll().catch((error) => appendLog("切换仓库失败", error.message));
  });

  $("#refreshBtn").addEventListener("click", () => refreshAll().catch((error) => appendLog("刷新失败", error.message)));
  $("#branchSearch").addEventListener("change", () => refreshAll().catch((error) => appendLog("搜索失败", error.message)));
  $("#branchTypeFilter").addEventListener("change", () => renderBranches());
  $("#clearLogBtn").addEventListener("click", () => {
    state.log = [];
    $("#logOutput").textContent = "暂无操作。";
  });

  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });

  document.querySelectorAll('form select[name="scope"]').forEach((select) => {
    select.addEventListener("change", renderSelectOptions);
  });
  syncTagUpdateVersionControl();

  $("#featureForm").addEventListener("submit", (event) => {
    event.preventDefault();
    handleOperation("创建 feature", "/api/feature/create", event.currentTarget).catch((error) => appendLog("创建 feature 失败", error.message));
  });

  $("#releaseForm").addEventListener("submit", (event) => {
    event.preventDefault();
    handleOperation("创建 release", "/api/release/create", event.currentTarget).catch((error) => appendLog("创建 release 失败", error.message));
  });

  $("#bugfixForm").addEventListener("submit", (event) => {
    event.preventDefault();
    handleOperation("创建 bugfix", "/api/bugfix/create", event.currentTarget).catch((error) => appendLog("创建 bugfix 失败", error.message));
  });

  $("#tagForm").addEventListener("submit", (event) => {
    event.preventDefault();
    handleOperation("创建 Tag", "/api/tags/create", event.currentTarget).catch((error) => appendLog("创建 Tag 失败", error.message));
  });
  $("#abortPendingVersionTagBtn").addEventListener("click", abortPendingVersionTag);

  $("#repositoryForm").addEventListener("submit", (event) => {
    event.preventDefault();
    saveRepository(event.currentTarget).catch((error) => appendLog("保存仓库失败", error.message));
  });

  $("#resetRepositoryFormBtn").addEventListener("click", () => fillRepositoryForm(null));

  $("#repositoriesBody").addEventListener("click", async (event) => {
    const editId = event.target.dataset.editRepo;
    const deleteId = event.target.dataset.deleteRepo;
    if (editId) {
      const repo = state.repositories.find((item) => item.id === editId);
      if (repo) fillRepositoryForm(repo);
    }
    if (deleteId) {
      if (!confirm(`确认删除仓库 ${deleteId}？`)) return;
      try {
        const result = await api(`/api/repositories/${encodeURIComponent(deleteId)}`, { method: "DELETE" });
        appendLog("删除仓库", result);
        await refreshAll();
      } catch (error) {
        appendLog("删除仓库失败", error.message);
      }
    }
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

bindEvents();
fillRepositoryForm(null);
loadSession().catch((error) => showLoginMessage(error.message, true));
