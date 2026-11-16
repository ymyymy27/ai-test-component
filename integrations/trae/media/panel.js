/* AI 测试面板：纯 HTML/CSS/JS，只展示 CLI 返回值，不做业务判定。 */
(function () {
  "use strict";

  const vscode = acquireVsCodeApi();
  const state = {
    projects: [],
    projectId: "",
    tasks: [],
    taskId: "",
    task: null,
    planId: "",
    plan: null,
    planSummary: null,
    runId: "",
    run: null,
    outcome: null,
    reportId: "",
    report: null,
    workspacePath: "",
    pythonPath: "",
    pollTimer: null,
    pollError: "",
  };

  let nextId = 1;
  const pending = new Map();
  let queue = Promise.resolve();

  function $(id) {
    return document.getElementById(id);
  }

  function post(message) {
    vscode.postMessage(message);
  }

  /** 串行调用 CLI：同一面板同时最多一个桥进程，避免并发初始化同一工作空间。 */
  function call(command, payload, options) {
    const run = () =>
      new Promise((resolve) => {
        const id = "c" + nextId++;
        pending.set(id, resolve);
        post({
          type: "call",
          id,
          command,
          payload: payload || {},
          confirm: !!(options && options.confirm),
        });
      });
    const result = queue.then(run, run);
    queue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  function setMessage(id, text, kind) {
    const node = $(id);
    node.textContent = text || "";
    node.className = "message" + (kind ? " " + kind : "");
  }

  function formatError(error) {
    if (!error || typeof error !== "object") {
      return "未知错误";
    }
    const parts = [];
    if (error.code) {
      parts.push("[" + error.code + "]");
    }
    parts.push(error.message || "未提供错误说明");
    if (error.next_step) {
      parts.push("下一步：" + error.next_step);
    }
    return parts.join(" ");
  }

  function setGrid(id, rows) {
    const grid = $(id);
    grid.textContent = "";
    for (const row of rows) {
      const key = document.createElement("div");
      key.className = "k";
      key.textContent = row[0];
      const value = document.createElement("div");
      value.textContent = row[1];
      grid.appendChild(key);
      grid.appendChild(value);
    }
  }

  function pill(ok) {
    const node = document.createElement("span");
    node.className = "pill " + (ok ? "ok" : "bad");
    node.textContent = ok ? "ok" : "未通过";
    return node;
  }

  function renderContext() {
    const python = state.pythonPath ? " · 设置解释器：" + state.pythonPath : " · 解释器：自动发现";
    $("context-line").textContent = "工作空间：" + (state.workspacePath || "未配置") + python;
    if (!state.workspacePath) {
      setMessage("doctor-message", "请先在设置中填写 aitest.workspacePath（用户数据工作空间，不是源码目录）。", "warn");
    }
    if (state.reportId && !$("export-path").value) {
      $("export-path").value = defaultExportPath();
    }
  }

  function defaultExportPath() {
    if (!state.workspacePath || !state.reportId) {
      return "";
    }
    const root = state.workspacePath.replace(/[\\/]+$/, "");
    return root + "/reports/" + state.reportId + ".md";
  }

  // -- 项目 -----------------------------------------------------------------

  async function loadProjects() {
    const result = await call("project.list", {});
    if (!result.ok) {
      setMessage("project-message", formatError(result.error), "error");
      return;
    }
    const data = result.data || {};
    state.projects = Array.isArray(data.projects) ? data.projects : [];
    renderProjects();
    if (state.projectId) {
      await loadTasks();
    }
  }

  function renderProjects() {
    const select = $("sel-project");
    select.textContent = "";
    if (!state.projects.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "（尚无项目，请先新建）";
      select.appendChild(option);
      state.projectId = "";
      return;
    }
    if (!state.projects.some((item) => item.project_id === state.projectId)) {
      state.projectId = state.projects[0].project_id;
    }
    for (const item of state.projects) {
      const option = document.createElement("option");
      option.value = item.project_id;
      option.textContent = (item.name || item.root) + " (" + item.project_id + ")";
      option.selected = item.project_id === state.projectId;
      select.appendChild(option);
    }
  }

  async function createProject() {
    const root = $("new-root").value.trim();
    if (!root) {
      setMessage("project-message", "请填写项目目录的绝对路径。", "warn");
      return;
    }
    const payload = { root };
    const name = $("new-name").value.trim();
    const goal = $("new-goal").value.trim();
    if (name) {
      payload.name = name;
    }
    if (goal) {
      payload.goal = goal;
    }
    const result = await call("project.add", payload);
    if (!result.ok) {
      setMessage("project-message", formatError(result.error), "error");
      return;
    }
    state.projectId = result.data.project_id;
    resetTaskState();
    await loadProjects();
    setMessage(
      "project-message",
      result.data.existing ? "该目录已登记，已切换到现有项目。" : "项目已登记。",
      "info",
    );
  }

  function resetTaskState() {
    state.tasks = [];
    state.taskId = "";
    state.task = null;
    resetPlanState();
    resetRunState();
    renderTasks();
    renderScope();
  }

  function resetPlanState() {
    state.planId = "";
    state.plan = null;
    state.planSummary = null;
    $("plan-detail").textContent = "";
    $("plan-detail").classList.add("hidden");
    renderRunSummary();
  }

  function resetRunState() {
    stopPolling();
    state.runId = "";
    state.run = null;
    state.outcome = null;
    state.reportId = "";
    state.report = null;
    state.pollError = "";
    renderRun();
    updateReportSummary("");
    $("export-path").value = "";
    $("report-detail").classList.add("hidden");
  }

  // -- 目标与任务 -------------------------------------------------------------

  async function suggestTargets() {
    if (!state.projectId) {
      setMessage("task-message", "请先选择或新建项目。", "warn");
      return;
    }
    const result = await call("task.suggest", { project_id: state.projectId });
    if (!result.ok) {
      setMessage("task-message", formatError(result.error), "error");
      return;
    }
    const data = result.data || {};
    const candidates = Array.isArray(data.candidates) ? data.candidates : [];
    const list = $("candidate-list");
    list.textContent = "";
    for (const candidate of candidates) {
      const item = document.createElement("li");
      const title = document.createElement("div");
      title.textContent = candidate.title || "";
      item.appendChild(title);
      if (candidate.reason) {
        const reason = document.createElement("div");
        reason.className = "muted";
        reason.textContent = "依据：" + candidate.reason;
        item.appendChild(reason);
      }
      const use = document.createElement("button");
      use.type = "button";
      use.className = "secondary";
      use.textContent = "使用这个目标";
      use.addEventListener("click", () => {
        $("task-goal").value = candidate.title || "";
        setMessage("task-message", "已填入目标，可直接修改后创建任务。", "info");
      });
      item.appendChild(use);
      list.appendChild(item);
    }
    if (!candidates.length) {
      const item = document.createElement("li");
      item.className = "muted";
      item.textContent = data.ai_generated === false
        ? "AI 不可用或没有足够信号；可直接自定义目标。"
        : "没有候选目标；可直接自定义目标。";
      list.appendChild(item);
    }
    if (data.fallback) {
      const fallback = document.createElement("li");
      fallback.className = "muted";
      fallback.textContent = "通用目标：" + data.fallback;
      list.appendChild(fallback);
    }
  }

  async function createTask() {
    if (!state.projectId) {
      setMessage("task-message", "请先选择或新建项目。", "warn");
      return;
    }
    const goal = $("task-goal").value.trim();
    if (!goal) {
      setMessage("task-message", "请先填写测试目标或选择候选。", "warn");
      return;
    }
    const lines = $("task-items").value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    const items = lines.map((text) => ({ text, observable: true, source: "面板输入" }));
    const result = await call("task.add", {
      project_id: state.projectId,
      title: goal,
      goal,
      acceptance_items: items,
    });
    if (!result.ok) {
      setMessage("task-message", formatError(result.error), "error");
      return;
    }
    const data = result.data || {};
    state.taskId = data.task_id;
    state.task = {
      task_id: data.task_id,
      title: goal,
      goal,
      acceptance_items: Array.isArray(data.acceptance_items) ? data.acceptance_items : [],
    };
    resetPlanState();
    resetRunState();
    await loadTasks();
    renderScope();
    const pendingCount = state.task.acceptance_items.filter((item) => item.status !== "active").length;
    setMessage(
      "task-message",
      "任务 " + data.task_id + " 已创建" +
        (pendingCount ? "；" + pendingCount + " 条验收项不可观察，不进入范围。" : "。"),
      "info",
    );
  }

  async function loadTasks() {
    if (!state.projectId) {
      state.tasks = [];
      renderTasks();
      return;
    }
    const result = await call("task.list", { project_id: state.projectId });
    if (!result.ok) {
      setMessage("task-message", formatError(result.error), "error");
      return;
    }
    const data = result.data || {};
    state.tasks = Array.isArray(data.tasks) ? data.tasks : [];
    if (!state.tasks.some((item) => item.task_id === state.taskId)) {
      state.taskId = state.tasks.length ? state.tasks[0].task_id : "";
    }
    renderTasks();
    if (state.taskId) {
      await loadTask();
    } else {
      state.task = null;
      renderScope();
    }
  }

  function renderTasks() {
    const select = $("sel-task");
    select.textContent = "";
    if (!state.tasks.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "（尚无任务）";
      select.appendChild(option);
      return;
    }
    for (const item of state.tasks) {
      const option = document.createElement("option");
      option.value = item.task_id;
      option.textContent = (item.title || item.task_id) + " (" + item.task_id + ")";
      option.selected = item.task_id === state.taskId;
      select.appendChild(option);
    }
  }

  async function loadTask() {
    if (!state.projectId || !state.taskId) {
      state.task = null;
      renderScope();
      return;
    }
    const result = await call("task.show", {
      project_id: state.projectId,
      task_id: state.taskId,
    });
    if (!result.ok) {
      setMessage("task-message", formatError(result.error), "error");
      return;
    }
    state.task = (result.data && result.data.task) || null;
    resetPlanState();
    renderScope();
    if (state.task) {
      setMessage("task-message", "已加载任务：" + (state.task.title || state.task.task_id), "info");
    }
  }

  // -- 范围与计划 -------------------------------------------------------------

  function renderScope() {
    const container = $("scope-items");
    container.textContent = "";
    if (!state.task) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "先创建或加载任务。";
      container.appendChild(empty);
      syncScopeEnabled();
      return;
    }
    const items = Array.isArray(state.task.acceptance_items) ? state.task.acceptance_items : [];
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "该任务没有验收项；计划会因范围为空被拒绝。";
      container.appendChild(empty);
      syncScopeEnabled();
      return;
    }
    for (const item of items) {
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.value = item.item_id;
      box.dataset.pending = item.status === "active" ? "0" : "1";
      box.disabled = item.status !== "active";
      label.appendChild(box);
      const text = document.createElement("span");
      text.textContent = item.text || item.item_id;
      label.appendChild(text);
      if (item.status !== "active") {
        const badge = document.createElement("span");
        badge.className = "muted";
        badge.textContent = "（待补充，不进入范围）";
        label.appendChild(badge);
      }
      container.appendChild(label);
    }
    syncScopeEnabled();
  }

  function presetValue() {
    const checked = document.querySelector('input[name="preset"]:checked');
    return checked ? checked.value : "all_applicable";
  }

  function syncScopeEnabled() {
    const disabled = presetValue() === "all_applicable";
    for (const box of document.querySelectorAll("#scope-items input[type=checkbox]")) {
      box.disabled = disabled || box.dataset.pending === "1";
    }
  }

  async function generatePlan() {
    if (!state.task) {
      setMessage("plan-message", "请先创建或加载任务。", "warn");
      return;
    }
    const preset = presetValue();
    let ids = [];
    if (preset === "selected") {
      ids = Array.from(document.querySelectorAll("#scope-items input[type=checkbox]:checked"))
        .filter((box) => !box.disabled)
        .map((box) => box.value);
      if (!ids.length) {
        setMessage("plan-message", "自选范围至少要勾选一条验收项。", "warn");
        return;
      }
    }
    const result = await call("plan.generate", {
      project_id: state.projectId,
      task_id: state.task.task_id,
      scope: { preset, module_ids: [], acceptance_item_ids: ids },
    });
    if (!result.ok) {
      setMessage("plan-message", formatError(result.error), "error");
      return;
    }
    const data = result.data || {};
    state.planId = data.plan_id;
    state.planSummary = data;
    state.plan = null;
    state.reportId = "";
    const coverage = data.coverage || {};
    setMessage(
      "plan-message",
      "计划 " + data.plan_id + " 已生成并发布；范围 " +
        (coverage.selected === undefined ? "?" : coverage.selected) + "/" +
        (coverage.applicable === undefined ? "?" : coverage.applicable) +
        "，用例 " + (data.cases === undefined ? "?" : data.cases) + " 条。",
      "info",
    );
    await showPlan();
  }

  async function showPlan() {
    if (!state.planId) {
      setMessage("plan-message", "先生成计划。", "warn");
      return;
    }
    const result = await call("plan.show", {
      project_id: state.projectId,
      plan_id: state.planId,
    });
    if (!result.ok) {
      setMessage("plan-message", formatError(result.error), "error");
      return;
    }
    state.plan = (result.data && result.data.plan) || null;
    renderPlanDetail();
  }

  function renderPlanDetail() {
    const plan = state.plan;
    if (!plan) {
      $("plan-detail").classList.add("hidden");
      renderRunSummary();
      return;
    }
    const scope = plan.scope || {};
    const selected = Array.isArray(scope.acceptance_item_ids) ? scope.acceptance_item_ids : [];
    const cases = Array.isArray(plan.cases) ? plan.cases : [];
    const lines = [
      "计划：" + plan.plan_id + "  状态：" + plan.status + "  修订：" + plan.revision,
      "范围：" + scope.preset + "，已选 " + selected.length + " / 适用 " + plan.applicable_count,
      "用例：" + cases.length + " 条",
    ];
    for (const item of cases) {
      lines.push(
        "- [" + (item.layer || "?") + "] " + item.case_id +
          (item.depends_on ? "（依赖 " + item.depends_on + "）" : "") +
          " 预期：" + (item.expected || "未提供"),
      );
    }
    const detail = $("plan-detail");
    detail.textContent = lines.join("\n");
    detail.classList.remove("hidden");
    renderRunSummary();
  }

  function renderRunSummary() {
    let summary = null;
    if (state.plan) {
      const scope = state.plan.scope || {};
      summary = {
        selected: Array.isArray(scope.acceptance_item_ids) ? scope.acceptance_item_ids.length : 0,
        applicable: state.plan.applicable_count,
        cases: Array.isArray(state.plan.cases) ? state.plan.cases.length : 0,
      };
    } else if (state.planSummary) {
      const coverage = state.planSummary.coverage || {};
      summary = {
        selected: coverage.selected,
        applicable: coverage.applicable,
        cases: state.planSummary.cases,
      };
    }
    $("run-summary").textContent = summary
      ? "将执行范围：已选 " + summary.selected + " / 适用 " + summary.applicable +
        "；用例数：" + summary.cases + "。"
      : "尚未生成计划。";
  }

  // -- 执行与轮询 -------------------------------------------------------------

  function prepareRun() {
    if (!state.planId) {
      setMessage("run-message", "先生成计划。", "warn");
      return;
    }
    const summary = $("run-summary").textContent;
    $("confirm-text").textContent =
      summary + "\n确认后调用 run.start（带确认记录），在本机启动一次性测试 worker。" +
      "关闭面板不会取消运行。";
    $("confirm-box").classList.remove("hidden");
    setMessage("run-message", "");
  }

  async function confirmRun() {
    if (!state.planId) {
      return;
    }
    $("confirm-box").classList.add("hidden");
    const result = await call(
      "run.start",
      { project_id: state.projectId, plan_id: state.planId },
      { confirm: true },
    );
    if (!result.ok) {
      setMessage("run-message", formatError(result.error), "error");
      return;
    }
    const data = result.data || {};
    state.runId = data.run_id;
    state.reportId = "";
    state.run = null;
    state.outcome = null;
    state.pollError = "";
    setMessage("run-message", "运行 " + data.run_id + " 已启动。", "info");
    startPolling();
  }

  function startPolling() {
    stopPolling();
    state.pollTimer = setInterval(() => {
      void pollRun();
    }, 2000);
    void pollRun();
  }

  function stopPolling() {
    if (state.pollTimer !== null) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  async function pollRun() {
    if (!state.projectId || !state.runId) {
      return;
    }
    const result = await call("run.status", {
      project_id: state.projectId,
      run_id: state.runId,
    });
    if (!result.ok) {
      state.pollError = "连接中断：" + formatError(result.error);
      renderRun();
      return;
    }
    state.pollError = "";
    const data = result.data || {};
    state.run = data.run || null;
    state.outcome = data.outcome || null;
    renderRun();
    if (state.run && state.run.status !== "running") {
      stopPolling();
    }
  }

  function renderRun() {
    const run = state.run;
    const line = $("run-status-line");
    if (!run) {
      line.textContent = state.pollError || (state.runId ? "运行 " + state.runId + "：等待状态" : "尚未启动运行。");
      setGrid("run-grid", []);
      $("run-detail").classList.add("hidden");
      return;
    }
    line.textContent =
      "运行 " + run.run_id + "：" + run.status +
      (state.pollError ? "（" + state.pollError + "）" : "");
    const cases = Array.isArray(run.cases) ? run.cases : [];
    setGrid("run-grid", [
      ["开始", run.started_at || "—"],
      ["心跳", run.heartbeat_at || "—"],
      ["确认人", run.confirmed_by || "—"],
      ["确认时间", run.confirmed_at || "—"],
      ["用例数", String(cases.length)],
      ["业务结论", state.outcome === null ? "（运行未结束）" : JSON.stringify(state.outcome)],
    ]);
    const detail = $("run-detail");
    detail.textContent = JSON.stringify(
      { scope: run.scope, cases: run.cases, outcome: state.outcome },
      null,
      2,
    );
    detail.classList.remove("hidden");
    if (state.reportId && !$("export-path").value) {
      $("export-path").value = defaultExportPath();
    }
  }

  async function refreshLatestRun() {
    if (!state.projectId) {
      setMessage("run-message", "请先选择或新建项目。", "warn");
      return;
    }
    const result = await call("run.list", { project_id: state.projectId, limit: 20 });
    if (!result.ok) {
      setMessage("run-message", formatError(result.error), "error");
      return;
    }
    const runs = (result.data && Array.isArray(result.data.runs)) ? result.data.runs : [];
    if (!runs.length) {
      setMessage("run-message", "该项目尚无运行记录。", "info");
      return;
    }
    state.runId = runs[0].run_id;
    state.pollError = "";
    await pollRun();
    if (state.run && state.run.status === "running") {
      startPolling();
    }
  }

  // -- 报告 -----------------------------------------------------------------

  async function generateReport() {
    if (!state.runId) {
      setMessage("result-message", "先启动或刷新一次运行。", "warn");
      return;
    }
    const result = await call("report.generate", {
      project_id: state.projectId,
      run_id: state.runId,
    });
    if (!result.ok) {
      setMessage("result-message", formatError(result.error), "error");
      return;
    }
    const data = result.data || {};
    state.reportId = data.report_id;
    state.report = data;
    $("export-path").value = defaultExportPath();
    const detail = $("report-detail");
    detail.textContent = JSON.stringify(data, null, 2);
    detail.classList.remove("hidden");
    setMessage("result-message", "报告 " + data.report_id + " 已生成。", "info");
  }

  async function exportReport() {
    if (!state.reportId) {
      setMessage("result-message", "先生成报告。", "warn");
      return;
    }
    const path = $("export-path").value.trim();
    if (!path) {
      setMessage("result-message", "请填写导出路径（绝对路径）。", "warn");
      return;
    }
    const result = await call("report.export", {
      project_id: state.projectId,
      report_id: state.reportId,
      path,
    });
    if (!result.ok) {
      setMessage("result-message", formatError(result.error), "error");
      return;
    }
    const data = result.data || {};
    setMessage("result-message", "已导出：" + data.path + "（" + data.bytes + " 字节）", "info");
  }

  function updateReportSummary(text) {
    $("report-detail").textContent = "";
    if (text) {
      $("report-detail").textContent = text;
      $("report-detail").classList.remove("hidden");
    }
  }

  // -- 环境自检 -------------------------------------------------------------

  async function runDoctor() {
    setMessage("doctor-message", "正在检查环境…", "info");
    const result = await call("doctor", {});
    if (!result.ok) {
      setMessage("doctor-message", formatError(result.error), "error");
      setGrid("doctor-grid", []);
      $("doctor-repairs").textContent = "";
      return;
    }
    const data = result.data || {};
    const rows = [];
    for (const key of ["workspace", "python", "package", "model", "github", "proxy"]) {
      const value = data[key];
      if (!value || typeof value !== "object") {
        continue;
      }
      const bits = [];
      for (const field of Object.keys(value)) {
        if (field === "ok") {
          continue;
        }
        bits.push(field + "=" + String(value[field]));
      }
      rows.push([key, (value.ok ? "ok" : "未通过") + (bits.length ? " · " + bits.join(", ") : "")]);
    }
    setGrid("doctor-grid", rows);
    const repairs = Array.isArray(data.repairs) ? data.repairs : [];
    const list = $("doctor-repairs");
    list.textContent = "";
    for (const repair of repairs) {
      const item = document.createElement("li");
      item.textContent =
        "[" + (repair.risk || "?") + "] " + (repair.command || "未提供命令") +
        (repair.auto ? "（低风险，可自动执行）" : "（需人工执行）");
      list.appendChild(item);
    }
    setMessage("doctor-message", repairs.length ? "发现 " + repairs.length + " 项待修复建议。" : "环境检查完成。", "info");
  }

  // -- 消息与初始化 -----------------------------------------------------------

  function handleCommand(name) {
    if (name === "doctor") {
      void runDoctor();
    } else if (name === "runStatus") {
      void refreshLatestRun();
    }
  }

  function wire() {
    $("btn-refresh-projects").addEventListener("click", () => void loadProjects());
    $("btn-create-project").addEventListener("click", () => void createProject());
    $("sel-project").addEventListener("change", () => {
      state.projectId = $("sel-project").value;
      resetTaskState();
      void loadTasks();
    });
    $("btn-suggest").addEventListener("click", () => void suggestTargets());
    $("btn-create-task").addEventListener("click", () => void createTask());
    $("btn-refresh-tasks").addEventListener("click", () => void loadTasks());
    $("sel-task").addEventListener("change", () => {
      state.taskId = $("sel-task").value;
      void loadTask();
    });
    for (const radio of document.querySelectorAll('input[name="preset"]')) {
      radio.addEventListener("change", () => syncScopeEnabled());
    }
    $("btn-generate-plan").addEventListener("click", () => void generatePlan());
    $("btn-show-plan").addEventListener("click", () => void showPlan());
    $("btn-prepare-run").addEventListener("click", prepareRun);
    $("btn-confirm-run").addEventListener("click", () => void confirmRun());
    $("btn-cancel-run").addEventListener("click", () => {
      $("confirm-box").classList.add("hidden");
      setMessage("run-message", "已取消，本次没有启动运行。", "info");
    });
    $("btn-refresh-run").addEventListener("click", () => void pollRun());
    $("btn-generate-report").addEventListener("click", () => void generateReport());
    $("btn-export-report").addEventListener("click", () => void exportReport());
    $("btn-doctor").addEventListener("click", () => void runDoctor());
  }

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (!message || typeof message !== "object") {
      return;
    }
    if (message.type === "result") {
      const resolve = pending.get(message.id);
      if (resolve) {
        pending.delete(message.id);
        resolve(message);
      }
      return;
    }
    if (message.type === "context") {
      state.pythonPath = typeof message.pythonPath === "string" ? message.pythonPath : "";
      state.workspacePath = typeof message.workspacePath === "string" ? message.workspacePath : "";
      renderContext();
      return;
    }
    if (message.type === "command") {
      const name = message.command;
      if (name === "doctor") {
        handleCommand(name);
      } else if (name === "runStatus") {
        handleCommand(name);
      }
    }
  });

  function init() {
    wire();
    post({ type: "ready" });
    void (async () => {
      await loadProjects();
      await runDoctor();
    })();
  }

  init();
})();
