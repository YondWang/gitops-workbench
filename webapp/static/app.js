const state = {
  session: null,
  config: null,
  repositories: [],
  currentRepositoryId: "",
  branches: [],
  tags: [],
  simosTags: [],
  commonRefs: null,
  log: [],
  pendingVersionTag: null,
  pendingVersionTimer: null,
  residentPackage: null,
  residentPackageTimer: null,
  schedules: [],
  scheduleRuns: [],
  configBranches: [],
};

const RESIDENT_PACKAGE_TAG_RE = /^[A-Za-z0-9._-]+_[VvFfTt]?\d+(?:\.\d+)+_\d{12}$/;

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
    if (input.name === "config_matrix") return;
    data[input.name] = input.checked;
  });
  const matrixInputs = Array.from(form.querySelectorAll('input[name="config_matrix"]'));
  if (matrixInputs.length) {
    data.config_matrix_enabled = true;
    data.config_matrix = matrixInputs.map((input) => {
      const [config_ref, label] = String(input.value || "").split(":");
      return { config_ref, label, enabled: input.checked };
    });
  }
  form.querySelectorAll('select[multiple]').forEach((select) => {
    data[select.name] = Array.from(select.selectedOptions).map((option) => option.value).filter(Boolean);
  });
  return data;
}

function operationBody(form) {
  const body = formValues(form);
  return {
    ...body,
    repository_id: body.repository_id || state.currentRepositoryId,
  };
}

function currentRepository() {
  return state.repositories.find((repo) => repo.id === state.currentRepositoryId) || state.repositories[0] || null;
}

function simosRepository() {
  return state.repositories.find((repo) => repo.id === "simos") || null;
}

function configRepository() {
  return state.repositories.find((repo) => repo.id === "config" || String(repo.project || "").toLowerCase() === "os/config") || null;
}

function appendLog(title, payload) {
  const stamp = new Date().toLocaleTimeString();
  const body = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  state.log.unshift(`[${stamp}] ${title}\n${body}`);
  $("#logOutput").textContent = state.log.join("\n\n");
}

function isResidentPackageTag(tag) {
  return RESIDENT_PACKAGE_TAG_RE.test(String(tag || ""));
}

function tagNameFromOperationResult(body, result) {
  if (result?.tag_name) return result.tag_name;
  const precheckTag = result?.precheck?.find((item) => item?.context?.tag_name)?.context?.tag_name;
  if (precheckTag) return precheckTag;
  const resultTag = result?.results?.find((item) => item?.result?.tag_name || item?.result?.tag?.name)?.result;
  return resultTag?.tag_name || resultTag?.tag?.name || body?.tag_name || "";
}

function residentStatusText(status) {
  const value = String(status || "");
  if (value === "success" || value === "ready") return "成功";
  if (value === "failed" || value === "error") return "失败";
  if (value === "checking") return "查询中";
  if (value === "pending_or_missing") return "构建中/未找到";
  return value || "未知";
}

function residentStatusClass(status) {
  const value = String(status || "");
  if (value === "success" || value === "ready") return "badge ok";
  if (value === "failed" || value === "error") return "badge error";
  return "badge muted";
}

function residentPipelineUrl(packageInfo) {
  return (
    packageInfo?.pipeline_url ||
    packageInfo?.job_url ||
    packageInfo?.web_url ||
    packageInfo?.pipeline?.web_url ||
    packageInfo?.job?.web_url ||
    ""
  );
}

function residentArtifactUrl(packageInfo) {
  const value = packageInfo?.artifact_url || packageInfo?.download_url || packageInfo?.artifact_path || "";
  return String(value).startsWith("http") ? value : "";
}

function packageShaSummary(packageInfo) {
  if (packageInfo?.sha256) return packageInfo.sha256;
  const packages = packageInfo?.packages || {};
  return Object.entries(packages)
    .map(([name, item]) => `${name}:${item?.sha256 || item?.md5 || "-"}`)
    .join("  ");
}

function renderResidentPackage() {
  const panel = $("#residentPackagePanel");
  if (!panel) return;
  const packageInfo = state.residentPackage;
  panel.classList.toggle("hidden", !packageInfo);
  if (!packageInfo) return;

  const status = packageInfo.status || "checking";
  const badge = $("#residentPackageStatus");
  badge.textContent = residentStatusText(status);
  badge.className = residentStatusClass(status);
  $("#residentPackageTag").textContent = packageInfo.tag || "-";
  $("#residentPackagePath").textContent = packageInfo.cloud_dir || packageInfo.artifact_path || `/data/simos-ci/artifacts/${packageInfo.tag || "<tag>"}/resident.tar.gz`;
  $("#residentPackageSha").textContent = packageShaSummary(packageInfo) || "-";
  $("#residentPackageBuiltAt").textContent = packageInfo.published_at || packageInfo.built_at || packageInfo.finished_at || "-";
  $("#residentPackageMeta").textContent = packageInfo.message || packageInfo.error || "Tag Pipeline 会在 GitLab Runner 上自动构建 resident.tar.gz";

  const pipelineUrl = residentPipelineUrl(packageInfo);
  const link = $("#residentPackagePipelineLink");
  link.classList.toggle("hidden", !pipelineUrl);
  link.href = pipelineUrl || "#";

  const artifactUrl = residentArtifactUrl(packageInfo);
  const artifactLink = $("#residentPackageArtifactLink");
  artifactLink?.classList.toggle("hidden", !artifactUrl);
  if (artifactLink) {
    artifactLink.href = artifactUrl || "#";
  }
}

function clearResidentPackagePoll() {
  if (state.residentPackageTimer) {
    clearTimeout(state.residentPackageTimer);
  }
  state.residentPackageTimer = null;
}

function scheduleResidentPackagePoll(tag) {
  clearResidentPackagePoll();
  state.residentPackageTimer = setTimeout(() => fetchResidentPackage(tag), 15000);
}

async function fetchResidentPackage(tag) {
  if (!isResidentPackageTag(tag)) return;
  const previousStatus = state.residentPackage?.status || "";
  try {
    const data = await api(`/api/resident-packages?tag=${encodeURIComponent(tag)}`);
    if (state.residentPackage?.tag && state.residentPackage.tag !== tag) return;
    state.residentPackage = data;
    renderResidentPackage();
    if (data.status === "pending_or_missing") {
      scheduleResidentPackagePoll(tag);
      return;
    }
    clearResidentPackagePoll();
    if (previousStatus !== data.status) {
      appendLog("resident 包状态", data);
    }
  } catch (error) {
    state.residentPackage = {
      tag,
      status: "error",
      artifact_path: `/data/simos-ci/artifacts/${tag}/resident.tar.gz`,
      error: error.message,
    };
    renderResidentPackage();
    scheduleResidentPackagePoll(tag);
  }
}

function watchResidentPackage(tag) {
  if (!isResidentPackageTag(tag)) return;
  clearResidentPackagePoll();
  state.residentPackage = {
    tag,
    status: "checking",
    artifact_path: `/data/simos-ci/artifacts/${tag}/resident.tar.gz`,
    message: "正在等待 GitLab Tag Pipeline 生成 resident 包",
  };
  renderResidentPackage();
  appendLog("已触发 resident 包状态跟踪", {
    tag,
    artifact_path: state.residentPackage.artifact_path,
  });
  fetchResidentPackage(tag);
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
  if (!isAdmin && ["release", "bugfix", "tag", "schedules", "repositories"].some((id) => $(`#${id}`).classList.contains("active-view"))) {
    switchView("overview");
  }
}

function renderConfig() {
  if (!state.config) return;
  const repo = currentRepository();
  $("#projectName").textContent = repo ? `${repo.name} / ${repo.project}` : "暂无仓库";
  const baseInput = $("#tagForm")?.elements.base_version;
  if (baseInput && !baseInput.value) {
    baseInput.value = defaultVersionBase();
  }
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

function renderSchedules() {
  const currentId = $("#scheduleForm")?.elements.id?.value || "";
  const schedule = state.schedules.find((item) => item.id === currentId) || null;
  const status = $("#scheduleStatus");
  if (status) {
    status.textContent = schedule ? (schedule.enabled ? "已启用" : "已停用") : "编辑中";
    status.className = schedule?.enabled ? "badge ok" : "badge muted";
  }
  renderSchedulePreviewFromForm();
  renderScheduleList();
  renderScheduleRuns();
}

function renderScheduleList() {
  const body = $("#scheduleListBody");
  if (!body) return;
  $("#scheduleCount").textContent = String(state.schedules.length);
  body.innerHTML =
    state.schedules
      .map(
        (schedule) => `
          <tr>
            <td>
              <strong>${escapeHtml(schedule.name || schedule.id)}</strong>
              <div class="meta"><code>${escapeHtml(schedule.id)}</code> ${schedule.enabled ? "启用" : "停用"}</div>
            </td>
            <td><code>${escapeHtml(schedule.daily_time || timeFromCron(schedule.cron || "0 16 * * *"))}</code><div class="meta">${escapeHtml(schedule.timezone || "Asia/Shanghai")}</div></td>
            <td><code>${escapeHtml(schedule.default_ref || "-")}</code><div class="meta">config: ${escapeHtml(configMatrixLabel(schedule))} · ${escapeHtml(versionPrefixLabel(schedule))}</div></td>
            <td>
              <button class="secondary small" type="button" data-edit-schedule="${escapeHtml(schedule.id)}">编辑</button>
              <button class="danger small" type="button" data-delete-schedule="${escapeHtml(schedule.id)}">删除</button>
            </td>
          </tr>
        `,
      )
      .join("") || `<tr><td colspan="4">暂无自动任务</td></tr>`;
}

function renderScheduleRuns() {
  const body = $("#scheduleRunsBody");
  if (!body) return;
  body.innerHTML =
    state.scheduleRuns
      .slice(0, 20)
      .map(
        (run) => `
          <tr>
            <td><span class="${releaseRunStatusClass(run.status)}">${escapeHtml(releaseRunStatusText(run.status))}</span></td>
            <td><code>${escapeHtml(run.tag_name || "-")}</code><div class="meta">${escapeHtml(run.started_at || "")}</div></td>
            <td><code>${escapeHtml(run.source_ref || run.ref || "-")}</code><div class="meta">config: ${escapeHtml(configMatrixLabel(run))} · ${escapeHtml(run.release_version || run.version || "")}</div></td>
            <td>
              <code>${escapeHtml(run.cloud_dir || run.error || "-")}</code>
              ${run.status === "waiting_version_mr" ? `<div><button class="secondary small" type="button" data-continue-run="${escapeHtml(run.id)}">继续</button></div>` : ""}
            </td>
          </tr>
        `,
      )
      .join("") || `<tr><td colspan="4">暂无运行记录</td></tr>`;
  const newest = state.scheduleRuns.find((run) => run.tag_name);
  if (newest?.tag_name && (!state.residentPackage || state.residentPackage.tag !== newest.tag_name)) {
    state.residentPackage = {
      tag: newest.tag_name,
      status: newest.status === "published" ? "ready" : "checking",
      artifact_path: newest.cloud_dir || `/data/simos-ci/artifacts/${newest.tag_name}/resident.tar.gz`,
      message: newest.status === "waiting_version_mr" ? "等待版本号 MR 合并" : "正在等待 resident 包状态",
    };
    renderResidentPackage();
  }
}

function timeFromCron(cron) {
  const parts = String(cron || "0 16 * * *").split(/\s+/);
  const minute = String(parts[0] || "0").padStart(2, "0");
  const hour = String(parts[1] || "16").padStart(2, "0");
  return `${hour}:${minute}`;
}

function cronFromTime(value) {
  const [hour = "16", minute = "0"] = String(value || "16:00").split(":");
  return `${Number(minute)} ${Number(hour)} * * *`;
}

function fillScheduleForm(schedule = null) {
  const form = $("#scheduleForm");
  if (!form) return;
  const next = schedule || {
    id: `simos-resident-${Date.now()}`,
    enabled: true,
    name: "SimOS resident 自动构建",
    timezone: "Asia/Shanghai",
    daily_time: "16:00",
    source_ref_strategy: "fixed_ref",
    default_ref: "fix",
    config_ref: "",
    config_matrix_enabled: true,
    config_matrix: [
      { config_ref: "SIMBOT_R6_A", label: "360", enabled: true },
      { config_ref: "SIMBOT_R6_B", label: "360s", enabled: true },
    ],
    version_source: "simos_version_info",
    manual_version_number: "",
    version_prefix_mode: "auto",
    manual_version_prefix: "V",
    cloud_category: "车机/CI自动构建",
  };
  Object.entries(next).forEach(([key, value]) => {
    const field = form.elements[key];
    if (!field) return;
    if (field.type === "checkbox") {
      field.checked = Boolean(value);
    } else {
      field.value = value ?? "";
    }
  });
  const enabledMatrix = new Set(
    (next.config_matrix || [])
      .filter((item) => item.enabled !== false)
      .map((item) => `${item.config_ref}:${item.label}`),
  );
  form.querySelectorAll('input[name="config_matrix"]').forEach((input) => {
    input.checked = enabledMatrix.size ? enabledMatrix.has(input.value) : input.checked;
  });
  form.elements.daily_time.value = next.daily_time || timeFromCron(next.cron || "0 16 * * *");
  renderConfigBranchOptions();
  renderSchedulePreviewFromForm();
}

function scheduleFormBody() {
  const body = formValues($("#scheduleForm"));
  body.daily_time = body.daily_time || timeFromCron(body.cron || "0 16 * * *");
  delete body.dependency_ref;
  return body;
}

function renderConfigBranchOptions() {
  const options = ['<option value="">请选择 config 分支</option>'].concat(
    state.configBranches.map((branch) => `<option value="${escapeHtml(branch.name)}">${escapeHtml(branch.name)}</option>`),
  );
  document.querySelectorAll('select[data-config-ref-select]').forEach((select) => {
    const previous = select.value;
    select.innerHTML = options.join("");
    if (previous && state.configBranches.some((branch) => branch.name === previous)) {
      select.value = previous;
    }
  });
}

function versionPrefixLabel(schedule) {
  if (schedule.version_prefix_mode === "manual") return `手动 ${schedule.manual_version_prefix || "V"}`;
  return "自动 V/F";
}

function configMatrixLabel(value) {
  const matrix = value.config_matrix || [];
  if (Array.isArray(matrix) && matrix.length) {
    const enabled = matrix.filter((item) => item.enabled !== false);
    return enabled.map((item) => `${item.config_ref}/${item.label}`).join(", ") || "未选择";
  }
  return value.config_ref || "未选择";
}

function releaseRunStatusText(status) {
  const value = String(status || "");
  return {
    planned: "已计划",
    precheck_failed: "预检失败",
    waiting_version_mr: "等待 MR",
    tagging: "打 Tag",
    building: "构建中",
    published: "已发布",
    failed: "失败",
    canceled: "已取消",
  }[value] || value || "-";
}

function releaseRunStatusClass(status) {
  const value = String(status || "");
  if (["published", "building"].includes(value)) return "badge ok";
  if (["failed", "precheck_failed", "canceled"].includes(value)) return "badge error";
  return "badge muted";
}

function sourceRefSlug(ref) {
  return String(ref || "fix").replaceAll("/", "-");
}

function previewVersionPrefix(ref, mode, manualPrefix) {
  if (mode === "manual") return manualPrefix || "V";
  if (ref === "release") return "F";
  return "V";
}

function renderSchedulePreviewFromForm() {
  const form = $("#scheduleForm");
  const preview = $("#scheduleTagPreview");
  if (!form || !preview) return;
  const body = scheduleFormBody();
  const ref = body.default_ref || "fix";
  const prefix = previewVersionPrefix(ref, body.version_prefix_mode, body.manual_version_prefix);
  const versionNumber = body.manual_version_number || "版本号";
  const configRef = configMatrixLabel(body);
  preview.textContent = `${sourceRefSlug(ref)}_${prefix}${versionNumber}_${new Date().toISOString().slice(0, 16).replace(/[-T:]/g, "").slice(0, 12)} · config:${configRef}`;
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
  renderTagDeleteOptions();
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

function fillMultiSelect(selector, values) {
  const select = $(selector);
  if (!select) return;
  const previous = Array.from(select.selectedOptions).map((option) => option.value);
  if (!values.length) {
    select.innerHTML = "<option value=\"\" disabled>暂无可选 Tag</option>";
    select.disabled = true;
    return;
  }
  const open = "<option value=\"";
  const mid = '">';
  const close = "</option>";
  select.innerHTML = values.map((value) => open + escapeHtml(value) + mid + escapeHtml(value) + close).join("");
  const nextSelected = previous.filter((value) => values.includes(value));
  Array.from(select.options).forEach((option) => {
    option.selected = nextSelected.includes(option.value);
  });
  select.disabled = false;
}

function tagDeleteOptions(scope) {
  const tags = scope === "all" ? state.commonRefs?.tags || [] : state.tags;
  return tags.map((item) => item.name).sort((a, b) => a.localeCompare(b));
}

function renderTagDeleteOptions() {
  const deleteScope = $("#tagDeleteForm")?.elements.scope?.value || "single";
  const deleteOptions = tagDeleteOptions(deleteScope);
  fillMultiSelect("#tagDeleteSelect", deleteOptions);
  const deleteHint = $("#tagDeleteHint");
  if (deleteHint) {
    deleteHint.textContent = deleteScope === "all" ? `显示全部启用仓库共有 Tag，共 ${deleteOptions.length} 个` : `显示当前仓库 Tag，共 ${deleteOptions.length} 个`;
  }

  const simosRepo = simosRepository();
  const simosPanel = $("#simosTagPanel");
  simosPanel?.classList.toggle("hidden", !simosRepo);
  if (!simosRepo) {
    return;
  }
  const simosOptions = state.simosTags.map((item) => item.name).sort((a, b) => a.localeCompare(b));
  fillMultiSelect("#simosTagDeleteSelect", simosOptions);
  const simosHint = $("#simosTagHint");
  if (simosHint) {
    simosHint.textContent = `显示 simos 主库 Tag，共 ${simosOptions.length} 个`;
  }
}

function defaultVersionBase() {
  return state.config?.version_update?.base_version || "";
}

function syncTagUpdateVersionControl() {
  const form = $("#tagForm");
  if (!form?.elements.update_version) return;
  const checkbox = form.elements.update_version;
  const baseField = $("#tagBaseVersionField");
  const baseInput = form.elements.base_version;
  const enabled = form.elements.scope?.value === "all";
  checkbox.disabled = !enabled;
  checkbox.title = enabled ? "" : "仅在全部启用仓库范围可用";
  checkbox.closest(".checkline")?.classList.toggle("disabled", !enabled);
  if (!enabled) {
    checkbox.checked = false;
  }
  const showBaseVersion = enabled && checkbox.checked;
  baseField?.classList.toggle("hidden", !showBaseVersion);
  if (baseInput) {
    baseInput.disabled = !showBaseVersion;
    if (showBaseVersion && !baseInput.value) {
      baseInput.value = defaultVersionBase();
    }
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
    state.simosTags = [];
    state.commonRefs = null;
    renderBranches();
    renderTags();
    return;
  }

  const search = $("#branchSearch").value.trim();
  const params = new URLSearchParams({ repository_id: state.currentRepositoryId });
  if (search) params.set("search", search);
  const simosRepo = simosRepository();
  const configRepo = configRepository();
  const [branches, tags, commonRefs, simosTags, schedules, configBranches] = await Promise.all([
    api(`/api/branches?${params}`),
    api(`/api/tags?${params}`),
    api("/api/common-refs").catch((error) => {
      appendLog("刷新公共来源失败", error.message);
      return null;
    }),
    simosRepo
      ? api(`/api/repositories/${encodeURIComponent(simosRepo.id)}/tags`).catch((error) => {
          appendLog("刷新 simos Tag 失败", error.message);
          return null;
        })
      : Promise.resolve(null),
    api("/api/release-tasks").catch((error) => {
      appendLog("刷新自动任务失败", error.message);
      return null;
    }),
    configRepo
      ? api(`/api/branches?repository_id=${encodeURIComponent(configRepo.id)}`).catch((error) => {
          appendLog("刷新 config 分支失败", error.message);
          return null;
        })
      : Promise.resolve(null),
  ]);
  state.branches = branches.branches || [];
  state.tags = tags.tags || [];
  state.simosTags = simosTags?.tags || [];
  state.commonRefs = commonRefs;
  state.schedules = schedules?.tasks || schedules?.schedules || [];
  state.scheduleRuns = schedules?.runs || [];
  state.configBranches = configBranches?.branches || [];
  renderConfigBranchOptions();
  renderBranches();
  renderTags();
  renderSchedules();
}

async function refreshWorkspace() {
  const button = $("#refreshBtn");
  const previousText = button?.textContent || "刷新";
  if (button) {
    button.disabled = true;
    button.textContent = "刷新中";
  }
  try {
    await refreshAll();
    if (state.residentPackage?.tag && $("#schedules")?.classList.contains("active-view")) {
      await fetchResidentPackage(state.residentPackage.tag);
    }
    appendLog("刷新完成", {
      view: document.querySelector(".view.active-view")?.id || "",
      repository: state.currentRepositoryId,
      schedules: state.schedules.length,
      branches: state.branches.length,
      tags: state.tags.length,
    });
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = previousText;
    }
  }
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
      base_version: result.version_update?.base_version || body.base_version,
      version_update_branch: result.version_update?.branch,
    };
    if (result.version_update?.base_version) {
      state.config = state.config || {};
      state.config.version_update = { ...(state.config.version_update || {}), base_version: result.version_update.base_version };
    }
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

async function saveSchedule(form) {
  const body = scheduleFormBody();
  const path = `/api/release-tasks/${encodeURIComponent(body.id)}`;
  const result = await api(path, { method: "PUT", body: JSON.stringify(body) });
  appendLog("保存自动任务", result);
  state.schedules = result.tasks || result.schedules || state.schedules;
  state.scheduleRuns = result.runs || state.scheduleRuns;
  renderSchedules();
  return result;
}

async function refreshSchedules() {
  const data = await api("/api/release-tasks");
  state.schedules = data.tasks || data.schedules || [];
  state.scheduleRuns = data.runs || [];
  renderSchedules();
}

async function dryRunSchedule() {
  const schedule = scheduleFormBody();
  await saveSchedule($("#scheduleForm"));
  const result = await postJson(`/api/release-tasks/${encodeURIComponent(schedule.id)}/dry-run`, {});
  $("#schedulePreview").textContent = JSON.stringify(result.plan || result, null, 2);
  if (result?.plan?.tag_name) {
    $("#scheduleTagPreview").textContent = result.plan.tag_name;
  }
  appendLog("自动任务试运行", result);
}

async function deleteSchedule(scheduleId) {
  if (!confirm(`确认删除自动任务 ${scheduleId}？`)) return;
  const result = await api(`/api/release-tasks/${encodeURIComponent(scheduleId)}`, { method: "DELETE" });
  appendLog("删除自动任务", result);
  state.schedules = result.tasks || result.schedules || [];
  state.scheduleRuns = result.runs || [];
  const currentId = $("#scheduleForm")?.elements.id?.value || "";
  if (currentId === scheduleId) {
    fillScheduleForm(state.schedules[0] || null);
  }
  renderSchedules();
}

async function runManualRelease(form) {
  const body = formValues(form);
  delete body.dependency_ref;
  const result = await postJson("/api/release-runs/manual", body);
  $("#schedulePreview").textContent = JSON.stringify(result.run || result, null, 2);
  appendLog("手动完整发版构建", result);
  await refreshSchedules();
  if (result?.run?.tag_name) {
    watchResidentPackage(result.run.tag_name);
  }
}

async function rerunExistingTag(form) {
  const body = formValues(form);
  const result = await postJson("/api/release-runs/rerun-tag", body);
  $("#schedulePreview").textContent = JSON.stringify(result.run || result, null, 2);
  appendLog("重跑/刷新已有 Tag 构建", result);
  await refreshSchedules();
  if (result?.run?.tag_name) {
    watchResidentPackage(result.run.tag_name);
  }
}

async function continueReleaseRun(runId) {
  const result = await postJson(`/api/release-runs/${encodeURIComponent(runId)}/continue`, {});
  $("#schedulePreview").textContent = JSON.stringify(result.run || result, null, 2);
  appendLog("继续发版任务", result);
  await refreshSchedules();
  if (result?.run?.tag_name) {
    watchResidentPackage(result.run.tag_name);
  }
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

  $("#refreshBtn").addEventListener("click", () => refreshWorkspace().catch((error) => appendLog("刷新失败", error.message)));
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
  $("#tagDeleteForm")?.elements.scope?.addEventListener("change", renderTagDeleteOptions);
  $("#tagForm")?.elements.update_version?.addEventListener("change", syncTagUpdateVersionControl);
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
  $("#tagDeleteForm").addEventListener("submit", (event) => {
    event.preventDefault();
    handleOperation("批量删除 Tag", "/api/tags/delete", event.currentTarget).catch((error) => appendLog("批量删除 Tag 失败", error.message));
  });
  $("#simosTagDeleteForm").addEventListener("submit", (event) => {
    event.preventDefault();
    handleOperation("删除 simos Tag", "/api/tags/delete", event.currentTarget).catch((error) => appendLog("删除 simos Tag 失败", error.message));
  });
  $("#abortPendingVersionTagBtn").addEventListener("click", abortPendingVersionTag);
  $("#scheduleForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    saveSchedule(event.currentTarget).catch((error) => appendLog("保存自动任务失败", error.message));
  });
  $("#scheduleForm")?.addEventListener("input", renderSchedulePreviewFromForm);
  $("#scheduleForm")?.addEventListener("change", renderSchedulePreviewFromForm);
  $("#newScheduleBtn")?.addEventListener("click", () => fillScheduleForm(null));
  $("#scheduleDryRunBtn")?.addEventListener("click", () => dryRunSchedule().catch((error) => appendLog("自动任务试运行失败", error.message)));
  $("#refreshSchedulesBtn")?.addEventListener("click", () => refreshSchedules().catch((error) => appendLog("刷新自动任务失败", error.message)));
  $("#manualReleaseForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    runManualRelease(event.currentTarget).catch((error) => appendLog("手动完整发版失败", error.message));
  });
  $("#rerunTagForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    rerunExistingTag(event.currentTarget).catch((error) => appendLog("重跑已有 Tag 失败", error.message));
  });
  $("#scheduleListBody")?.addEventListener("click", (event) => {
    const editId = event.target.dataset.editSchedule;
    const deleteId = event.target.dataset.deleteSchedule;
    if (editId) {
      const schedule = state.schedules.find((item) => item.id === editId);
      if (schedule) fillScheduleForm(schedule);
    }
    if (deleteId) {
      deleteSchedule(deleteId).catch((error) => appendLog("删除自动任务失败", error.message));
    }
  });
  $("#scheduleRunsBody")?.addEventListener("click", (event) => {
    const runId = event.target.dataset.continueRun;
    if (runId) {
      continueReleaseRun(runId).catch((error) => appendLog("继续发版任务失败", error.message));
    }
  });

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
