(() => {
  "use strict";

  const PROJECT = {
    project_id: "proj_demo01",
    name: "构建、测试发现与最小测试模块",
    root: "D:\\work\\demo-service",
    goal: "验证本地构建、测试发现与最小启动链路，并保留可复核的运行记录。",
    modules: 6,
    rules_version_id: "rules_2026_11_19",
    revision: 7,
    updated_at: "2026-11-19 10:42"
  };

  const TASK = {
    task_id: "task_demo01",
    title: "构建与最小启动验证",
    goal: "确认构建产物可以生成，入口可以启动，正常与边界输入行为可核验。",
    intent: "验证构建、测试发现、最小启动和 JSON 结果",
    scope: "核心构建、启动与正常/边界/异常输入",
    inputs: "Python 3.13、项目根目录、最小测试入口",
    outputs: "构建结果、启动结果、断言值、证据引用",
    preconditions: "使用独立工作空间，不修改真实业务数据",
    executor: "本地测试核心",
    reviewer: "人工核验",
    acceptance_items: 4,
    revision: 3
  };

  const PLANS = [
    {
      plan_id: "plan_demo01", task_id: "task_demo01", plan_no: 1,
      task_revision: 3, status: "published", applicable_count: 4,
      scope: "核心构建、启动与正常/边界/异常输入",
      created_at: "2026-11-19 10:45",
      cases: [
        { case_id: "case_load", layer: "L1", title: "加载与语法", expected: "入口加载并输出 JSON", verify_method: "python_checks", result: "passed", attempts: 1, evidence_refs: ["ev_load"] },
        { case_id: "case_normal", layer: "L2", title: "正常输入", expected: "2 加 3 返回 5", verify_method: "python_checks", result: "passed", attempts: 1, evidence_refs: ["ev_normal"] },
        { case_id: "case_boundary", layer: "L2", title: "边界输入", expected: "0 加 0 返回 0", verify_method: "python_checks", result: "passed", attempts: 1, evidence_refs: ["ev_boundary"] },
        { case_id: "case_error", layer: "L2", title: "异常输入", expected: "字符串与数字相加被安全处理", verify_method: "python_checks", result: "passed", attempts: 1, evidence_refs: ["ev_error"] }
      ]
    },
    {
      plan_id: "plan_demo02", task_id: "task_demo01", plan_no: 2,
      task_revision: 3, status: "waiting_confirmation", applicable_count: 3,
      scope: "启动、异常与边界回归",
      created_at: "2026-11-19 11:12",
      cases: []
    }
  ];

  const RUNS = [
    {
      run_id: "run_demo01", plan_id: "plan_demo01", project_id: PROJECT.project_id,
      scope: "核心构建、启动与正常/边界/异常输入",
      status: "completed", outcome: "passed",
      counts: { applicable: 4, completed: 4, failed: 0, blocked: 0, incomplete: 0 },
      started_at: "2026-11-19 10:50", elapsed_seconds: 85,
      confirmation_id: "confirm_demo01", latest_report_id: "report_demo01"
    },
    {
      run_id: "run_demo00", plan_id: "plan_demo02", project_id: PROJECT.project_id,
      scope: "启动、异常与边界回归",
      status: "failed", outcome: "failed",
      counts: { applicable: 3, completed: 3, failed: 1, blocked: 0, incomplete: 0 },
      started_at: "2026-11-18 16:20", elapsed_seconds: 61,
      confirmation_id: "confirm_demo00", latest_report_id: "report_demo00"
    }
  ];

  const CASES = [
    { case_id: "case_load", title: "加载与语法", expected: "入口加载并输出 JSON", actual: "JSON 输出成功", verify_method: "PythonChecks", result: "passed", evidence_refs: ["ev_load"], depends_on: [] },
    { case_id: "case_normal", title: "正常输入", expected: "2 加 3 返回 5", actual: "normal=5", verify_method: "PythonChecks", result: "passed", evidence_refs: ["ev_normal"], depends_on: ["case_load"] },
    { case_id: "case_boundary", title: "边界输入", expected: "0 加 0 返回 0", actual: "boundary=0", verify_method: "PythonChecks", result: "passed", evidence_refs: ["ev_boundary"], depends_on: ["case_load"] },
    { case_id: "case_error", title: "异常输入", expected: "字符串与数字相加被安全处理", actual: "error_handled=true", verify_method: "PythonChecks", result: "passed", evidence_refs: ["ev_error"], depends_on: ["case_load"] }
  ];

  const EVIDENCE = [
    { ref: "ev_load", sha256: "sha256:12a6…9f0e", media_type: "application/json", size: 92, exists: true, truncated: false, note: "加载输出" },
    { ref: "ev_normal", sha256: "sha256:3a9d…ff12", media_type: "application/json", size: 67, exists: true, truncated: false, note: "normal 字段" },
    { ref: "ev_boundary", sha256: "sha256:a0d4…e9e", media_type: "application/json", size: 67, exists: true, truncated: false, note: "boundary 字段" },
    { ref: "ev_error", sha256: "sha256:b2f0…19e9", media_type: "application/json", size: 67, exists: true, truncated: false, note: "error_handled 字段" }
  ];

  const ISSUES = [
    {
      issue_id: "issue_demo01", signature: "startup/port/existing",
      title: "最小启动端口占用时错误信息不完整",
      severity: "high", status: "pending_retest", revision: 2,
      fix_version: "0.4.10", verification_run_id: "run_demo01",
      occurrences: 1, latest_at: "2026-11-19 10:12",
      expected: "端口占用时给出明确原因并保留可复核证据",
      actual: "只显示启动失败，没有指出被占用的端口"
    },
    {
      issue_id: "issue_demo02", signature: "boundary/input/type",
      title: "边界输入说明不足",
      severity: "medium", status: "processing", revision: 1,
      fix_version: "", verification_run_id: "",
      occurrences: 2, latest_at: "2026-11-18 15:32",
      expected: "边界输入提示预期类型",
      actual: "只提示输入无效"
    }
  ];

  const STATUS_LABELS = {
    completed: ["已完成", "ok"], passed: ["通过", "ok"], failed: ["失败", "bad"],
    blocked: ["阻塞", "warn"], pending: ["待执行", "warn"], running: ["进行中", "info"],
    waiting_confirmation: ["待确认", "warn"], published: ["已发布", "ok"],
    pending_retest: ["待复测", "warn"], processing: ["处理中", "info"],
    closed: ["已关闭", "ok"], open: ["待确认", "warn"], cancelled: ["已取消", "bad"],
    incomplete: ["未完成", "warn"], not_applicable: ["不适用", ""],
    ok: ["正常", "ok"], info: ["信息", "info"], bad: ["异常", "bad"]
  };

  const state = {
    projectId: PROJECT.project_id,
    issueTab: "all",
    issueSeverity: "all",
    issueQuery: "",
    runId: "run_demo01",
    menuOpen: false
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
  const status = (value) => {
    const [label, tone] = STATUS_LABELS[value] || [value || "未知", ""];
    return `<span class="status ${tone}">${esc(label)}</span>`;
  };
  const button = (label, action, tone = "", attrs = "") =>
    `<button class="button ${tone} ${attrs.includes("small") ? "small" : ""}" data-action="${action}" ${attrs}>${label}</button>`;
  const link = (label, href, tone = "") =>
    `<a class="button ${tone}" href="${href}">${label}</a>`;
  const row = (cells, action = "") => `<tr ${action ? `class="clickable" data-action="${action}" tabindex="0"` : ""}>${cells}</tr>`;
  const empty = (colspan = 6, text = "暂无记录") => `<tr><td class="empty-row" colspan="${colspan}">${text}</td></tr>`;
  const fmt = (value) => value == null || value === "" ? "—" : esc(value);
  const project = () => PROJECT;
  const currentPlan = () => PLANS.find((plan) => plan.plan_id === "plan_demo01");
  const currentRun = () => RUNS.find((run) => run.run_id === state.runId) || RUNS[0];
  const pendingPlans = () => PLANS.filter((plan) => plan.status === "waiting_confirmation");
  const openIssues = () => ISSUES.filter((issue) => issue.status !== "closed");

  function pageHead(title, description, actions = "") {
    return `<div class="page-head"><div><h1>${title}</h1>${description ? `<p>${description}</p>` : ""}</div>${actions ? `<div class="head-actions">${actions}</div>` : ""}</div>`;
  }

  function panel(title, body, actions = "", extraClass = "") {
    return `<section class="panel ${extraClass}"><div class="panel-head"><h2>${title}</h2>${actions ? `<div class="head-actions">${actions}</div>` : ""}</div>${body}</section>`;
  }

  function projectCard() {
    return `<a class="project-card" href="#/project/${PROJECT.project_id}">
      <span class="project-orb">P</span>
      <span class="project-card-main"><h2>${esc(PROJECT.name)}</h2><p>${esc(PROJECT.goal)}</p></span>
      <span class="project-count"><strong>1</strong>被测项目</span>
    </a>`;
  }

  function workPage() {
    const plan = currentPlan();
    const run = currentRun();
    return `${pageHead("测试工作", "当前工作空间总览 · 项目、计划与运行结果", button("刷新状态", "refresh", "small"))}
      ${projectCard()}
      <div class="grid-2" style="margin-top:14px">
        ${panel("当前工作", `<div class="panel-body">
          <div class="section-label">项目信息</div>
          <dl class="kv-list">
            <dt>项目名称</dt><dd>${esc(PROJECT.name)}</dd>
            <dt>项目 ID</dt><dd class="mono">${esc(PROJECT.project_id)}</dd>
            <dt>根目录</dt><dd class="mono">${esc(PROJECT.root)}</dd>
            <dt>任务</dt><dd>${esc(TASK.title)}</dd>
            <dt>验证意图</dt><dd>${esc(TASK.intent)}</dd>
            <dt>当前计划</dt><dd>计划 ${plan.plan_no} · ${status(plan.status)} · ${plan.applicable_count} 项</dd>
            <dt>最近运行</dt><dd><span class="mono">${esc(run.run_id)}</span> · ${status(run.status)} · ${status(run.outcome)}</dd>
          </dl>
          <div class="inline-actions">${link("打开项目", `#/project/${PROJECT.project_id}`, "primary")}${link("打开台账", "#/ledger")}${link("处理问题", "#/issues")}</div>
        </div>`)}
        ${panel("待处理", `<div class="table-wrap"><table class="data">
          <thead><tr><th>类型</th><th>说明</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            ${pendingPlans().map((item) => `<tr><td>计划</td><td>计划 ${item.plan_no} · ${esc(item.scope)}</td><td>${status(item.status)}</td><td><button class="button small" data-action="confirm-plan">执行确认</button> <button class="button small ghost" data-action="view-plan">查看</button></td></tr>`).join("")}
            ${openIssues().map((issue) => `<tr><td>问题</td><td>${esc(issue.title)}</td><td>${status(issue.status)}</td><td><button class="button small ghost" data-action="issue-detail" data-issue-id="${issue.issue_id}">查看</button></td></tr>`).join("") || empty(4, "暂无待处理项")}
          </tbody>
        </table></div>`)}
      </div>
      ${panel("最近运行", `<div class="table-wrap"><table class="data">
        <thead><tr><th>运行 case 名称</th><th>状态</th><th>结果</th><th>检查方式</th><th>时间</th><th>报告</th></tr></thead>
        <tbody>${RUNS.map((item) => {
          const firstCase = item.plan_id === "plan_demo01" ? CASES[0].title : "启动与异常回归";
          return row(`<td class="strong">${esc(firstCase)}</td><td>${status(item.status)}</td><td>${status(item.outcome)}</td><td>PythonChecks</td><td>${esc(item.started_at)}</td><td><button class="button small ghost" data-action="open-ledger">打开台账</button>`, "");
        }).join("")}</tbody>
      </table></div>`)}
      ${panel("执行进度", `<div class="panel-body">
        <div class="filterbar" style="padding:0 0 11px;background:transparent;border:0">
          <label>本轮运行 <select class="input" id="home-run-select">${RUNS.map((item) => `<option value="${item.run_id}" ${item.run_id === run.run_id ? "selected" : ""}>${item.run_id} · ${esc(item.started_at)}</option>`).join("")}</select></label>
          <span class="muted small">${run.counts.completed}/${run.counts.applicable} 个适用步骤已完成</span>
        </div>
        <div class="progress"><i style="width:${run.counts.completed / run.counts.applicable * 100}%"></i></div>
        <div class="table-wrap"><table class="data"><thead><tr><th>步骤名称</th><th>状态</th><th>结果</th><th>检查方式</th></tr></thead><tbody>
          ${CASES.map((item) => `<tr><td class="strong">${esc(item.title)}</td><td>${status(run.run_id === "run_demo01" ? "completed" : "completed")}</td><td>${status(item.result)}</td><td>${esc(item.verify_method)}</td></tr>`).join("")}
        </tbody></table></div>
      </div>`)}
      <div class="grid-2">
        ${panel("本轮结论", `<div class="panel-body">
          <div class="summary-strip" style="padding:0 0 12px;border:0;background:transparent">
            <div class="metric"><strong>${status(run.outcome)}</strong><span>业务结论</span></div>
            <div class="metric"><strong>${run.counts.completed}/${run.counts.applicable}</strong><span>适用步骤</span></div>
            <div class="metric"><strong>${run.counts.failed}</strong><span>失败</span></div>
            <div class="metric"><strong>0</strong><span>阻塞</span></div>
          </div>
          <div class="callout">运行已完成；本轮未发现影响结论的缺口。</div>
          <div class="inline-actions">${button("去核验", "open-ledger", "primary")}${button("查看报告", "view-report")}${button("导出", "export-report")}${button("重跑", "rerun")}</div>
        </div>`)}
        ${panel("证据与待处理问题", `<div class="panel-body">
          <div class="section-label">证据</div>
          <div class="table-wrap"><table class="data"><thead><tr><th>引用</th><th>媒体类型</th><th>大小</th><th>状态</th></tr></thead><tbody>${EVIDENCE.map((item) => `<tr><td class="mono">${esc(item.ref)}</td><td>${esc(item.media_type)}</td><td>${item.size} B</td><td>${item.exists ? status("ok") : status("bad")}</td></tr>`).join("")}</tbody></table></div>
          <div class="separator"></div><div class="section-label">待处理问题</div>
          <table class="data"><tbody>${openIssues().slice(0, 2).map((issue) => `<tr><td class="strong">${esc(issue.title)}</td><td>${status(issue.status)}</td><td>${status(issue.severity)}</td></tr>`).join("")}</tbody></table>
        </div>`)}
      </div>`;
  }

  function projectPage() {
    return `${pageHead("被测项目", "项目内部的任务、计划、问题与配置", `${button("打开台账", "open-ledger")}${button("处理问题", "open-issues")}${button("编辑项目", "edit-project")}${button("新建任务", "new-task", "primary")}`)}
      ${panel("项目信息", `<div class="panel-body"><dl class="detail-list">
        <div class="fact"><dt>项目 ID</dt><dd class="mono">${esc(PROJECT.project_id)}</dd></div><div class="fact"><dt>根目录</dt><dd class="mono">${esc(PROJECT.root)}</dd></div>
        <div class="fact"><dt>目标</dt><dd>${esc(PROJECT.goal)}</dd></div><div class="fact"><dt>模块数量</dt><dd>${PROJECT.modules}</dd></div>
        <div class="fact"><dt>规则版本</dt><dd class="mono">${esc(PROJECT.rules_version_id)}</dd></div><div class="fact"><dt>当前修订</dt><dd>${PROJECT.revision}</dd></div>
        <div class="fact"><dt>更新时间</dt><dd>${esc(PROJECT.updated_at)}</dd></div>
      </dl></div>`)}
      ${panel("测试任务", `<div class="table-wrap"><table class="data"><thead><tr><th>任务</th><th>负责人</th><th>验证意图</th><th>验收项</th><th>修订</th><th>当前计划</th><th>操作</th></tr></thead><tbody>
        <tr><td class="strong">${esc(TASK.title)}</td><td>${esc(TASK.executor)}</td><td>${esc(TASK.intent)}</td><td>${TASK.acceptance_items}</td><td>${TASK.revision}</td><td>计划 1</td><td><button class="button small ghost" data-action="task-detail">任务详情</button> <button class="button small" data-action="generate-plan">生成计划</button></td></tr>
      </tbody></table></div>`)}
      ${panel("待确认计划", `<div class="table-wrap"><table class="data"><thead><tr><th>任务</th><th>序号</th><th>修订</th><th>范围</th><th>用例数</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>
        ${PLANS.map((plan) => `<tr><td>${esc(TASK.title)}</td><td>${plan.plan_no}</td><td>${plan.task_revision}</td><td>${esc(plan.scope)}</td><td>${plan.applicable_count}</td><td>${status(plan.status)}</td><td>${esc(plan.created_at)}</td><td>${plan.status === "waiting_confirmation" ? `<button class="button small primary" data-action="confirm-plan">执行前确认</button>` : `<button class="button small ghost" data-action="view-plan">查看计划</button>`}</td></tr>`).join("")}
      </tbody></table></div>`)}
      ${panel("待处理问题", `<div class="table-wrap"><table class="data"><thead><tr><th>问题</th><th>状态</th><th>严重度</th><th>预期 / 实际</th><th>修订</th><th>操作</th></tr></thead><tbody>
        ${ISSUES.map((issue) => `<tr><td class="strong">${esc(issue.title)}</td><td>${status(issue.status)}</td><td>${status(issue.severity)}</td><td>${esc(issue.expected)}<br><span class="faint">${esc(issue.actual)}</span></td><td>${issue.revision}</td><td><button class="button small ghost" data-action="issue-detail" data-issue-id="${issue.issue_id}">去核验</button></td></tr>`).join("")}
      </tbody></table></div>`)}
      ${panel("项目配置", `<details class="config-row"><summary>配置分组</summary><div class="config-content">
        <div class="table-wrap"><table class="data"><thead><tr><th>配置段</th><th>覆盖项</th><th>修订</th></tr></thead><tbody>
          <tr><td>model</td><td>端点、凭据引用、超时</td><td>3</td></tr><tr><td>services</td><td>启动、端口、健康检查</td><td>2</td></tr>
          <tr><td>runners</td><td>Python 3.13、工作目录</td><td>4</td></tr><tr><td>evidence</td><td>脱敏、对象引用</td><td>1</td></tr>
        </tbody></table></div>
      </div></details>`)}`;
  }

  function ledgerPage() {
    const run = currentRun();
    return `${pageHead("测试台账", "以单次运行为中心的只读检查结果", `<label class="small muted">运行 <select class="input" id="ledger-run-select">${RUNS.map((item) => `<option value="${item.run_id}" ${item.run_id === run.run_id ? "selected" : ""}>${item.run_id}</option>`).join("")}</select></label>${button("查看计划", "view-plan")}`)}
      <div class="summary-strip">
        <div class="metric"><strong>${status(run.status)}</strong><span>运行状态</span></div><div class="metric"><strong>${status(run.outcome)}</strong><span>业务结论</span></div>
        <div class="metric"><strong>${run.counts.applicable}</strong><span>适用项</span></div><div class="metric"><strong>${run.counts.completed}</strong><span>已覆盖</span></div><div class="metric"><strong>0</strong><span>未覆盖</span></div>
      </div>
      ${panel("运行信息", `<div class="panel-body"><dl class="detail-list">
        <div class="fact"><dt>Plan</dt><dd class="mono">${esc(run.plan_id)}</dd></div><div class="fact"><dt>Run</dt><dd class="mono">${esc(run.run_id)}</dd></div>
        <div class="fact"><dt>任务</dt><dd>${esc(TASK.title)}</dd></div><div class="fact"><dt>范围</dt><dd>${esc(run.scope)}</dd></div>
        <div class="fact"><dt>开始时间</dt><dd>${esc(run.started_at)}</dd></div><div class="fact"><dt>用时</dt><dd>${run.elapsed_seconds} 秒</dd></div>
        <div class="fact"><dt>确认记录</dt><dd class="mono">${esc(run.confirmation_id)}</dd></div><div class="fact"><dt>最新报告</dt><dd class="mono">${esc(run.latest_report_id)}</dd></div>
      </dl></div>`, button("查看报告", "view-report", "small"))}
      ${panel("执行确认", `<div class="panel-body"><dl class="detail-list">
        <div class="fact"><dt>确认状态</dt><dd>${status("completed")}</dd></div><div class="fact"><dt>确认来源</dt><dd>本地人工确认</dd></div>
        <div class="fact"><dt>确认内容</dt><dd>启动测试入口并执行 4 个适用用例</dd></div><div class="fact"><dt>确认时间</dt><dd>${esc(run.started_at)}</dd></div>
      </dl></div>`)}
      ${panel("项目级检查", `<div class="panel-body"><div class="table-wrap"><table class="data"><thead><tr><th>检查项</th><th>状态</th><th>结果</th><th>说明</th></tr></thead><tbody>
        <tr><td>Python 运行环境</td><td>${status("ok")}</td><td>通过</td><td>Python 3.13.13 · 独立 venv</td></tr>
        <tr><td>构建入口</td><td>${status("ok")}</td><td>通过</td><td>入口文件可加载</td></tr>
        <tr><td>证据持久化</td><td>${status("ok")}</td><td>通过</td><td>4 份对象均可读取</td></tr>
      </tbody></table></div></div>`)}
      ${panel("验收项与用例明细", `<div class="table-wrap"><table class="data"><thead><tr><th>用例</th><th>层级</th><th>预期</th><th>结果</th><th>检查方式</th><th>证据</th></tr></thead><tbody>
        ${CASES.map((item) => `<tr><td class="strong">${esc(item.title)}<br><span class="faint mono">${esc(item.case_id)}</span></td><td>${esc(item.layer)}</td><td>${esc(item.expected)}</td><td>${status(item.result)}</td><td>${esc(item.verify_method)}</td><td><button class="button small ghost" data-action="evidence-detail" data-evidence="${item.evidence_refs[0]}">查看证据</button></td></tr>`).join("")}
      </tbody></table></div>`)}
      ${panel("失败用例", `<div class="panel-body"><div class="callout warn">当前运行没有失败用例。下表中保留失败字段结构，便于后续接入失败结果。</div><div class="table-wrap"><table class="data"><thead><tr><th>用例</th><th>预期</th><th>实际</th><th>关联问题</th><th>处理</th></tr></thead><tbody>${empty(5, "本轮没有失败用例")}</tbody></table></div></div>`)}
      ${panel("报告字段", `<div class="panel-body"><dl class="detail-list">
        <div class="fact"><dt>业务结论</dt><dd>${status(run.outcome)}</dd></div><div class="fact"><dt>报告 ID</dt><dd class="mono">${esc(run.latest_report_id)}</dd></div>
        <div class="fact"><dt>覆盖</dt><dd>S=4 · M=4 · E=4 · R=0 · V=4</dd></div><div class="fact"><dt>失败发生</dt><dd>0</dd></div>
        <div class="fact"><dt>验收意见</dt><dd>主流程通过；演示数据不替代真实业务验收。</dd></div><div class="fact"><dt>问题数量</dt><dd>${openIssues().length}</dd></div>
      </dl><div class="inline-actions">${button("生成报告", "view-report", "primary")}${button("重跑未通过项", "rerun")}</div></div>`)}`;
  }

  function issuesPage() {
    const filtered = ISSUES.filter((issue) => {
      const tab = state.issueTab;
      const tabOk = tab === "all" || (tab === "pending" && ["open", "pending_retest"].includes(issue.status)) || issue.status === tab;
      const severityOk = state.issueSeverity === "all" || issue.severity === state.issueSeverity;
      const queryOk = !state.issueQuery || `${issue.title} ${issue.signature}`.toLowerCase().includes(state.issueQuery.toLowerCase());
      return tabOk && severityOk && queryOk;
    });
    const tabs = [["all", "全部"], ["open", "待确认"], ["processing", "处理中"], ["pending_retest", "待复测"], ["closed", "已关闭"]];
    return `${pageHead("问题", "问题状态、复现历史与关闭门禁")}
      ${panel("问题列表", `<div class="tabs">${tabs.map(([value, label]) => `<button class="tab ${state.issueTab === value ? "is-active" : ""}" data-action="issue-tab" data-tab="${value}">${label}<span class="faint"> ${value === "all" ? ISSUES.length : ISSUES.filter((issue) => issue.status === value).length}</span></button>`).join("")}</div>
        <div class="filterbar"><label>搜索 <input class="input search" id="issue-search" placeholder="问题标题或签名" value="${esc(state.issueQuery)}"></label><label>严重度 <select class="input" id="issue-severity"><option value="all" ${state.issueSeverity === "all" ? "selected" : ""}>全部</option><option value="high" ${state.issueSeverity === "high" ? "selected" : ""}>高</option><option value="medium" ${state.issueSeverity === "medium" ? "selected" : ""}>中</option><option value="low" ${state.issueSeverity === "low" ? "selected" : ""}>低</option></select></label><span class="muted small">共 ${filtered.length} 条</span></div>
        <div class="table-wrap"><table class="data"><thead><tr><th>问题</th><th>状态</th><th>严重度</th><th>发生次数</th><th>最近结果</th><th>最近发生</th></tr></thead><tbody>
          ${filtered.length ? filtered.map((issue) => row(`<td class="strong">${esc(issue.title)}<br><span class="faint mono">${esc(issue.signature)}</span></td><td>${status(issue.status)}</td><td>${status(issue.severity)}</td><td>${issue.occurrences}</td><td>${status(issue.status === "pending_retest" ? "failed" : "pending")}</td><td>${esc(issue.latest_at)}</td>`, `issue-detail:${issue.issue_id}`)).join("") : empty(6, "没有符合条件的问题")}
        </tbody></table></div>`)}
      ${panel("状态流转", `<div class="panel-body"><div class="callout">问题关闭必须绑定新的验证运行中的通过结果；当前原型只展示状态结构与操作入口。</div><div class="inline-actions">${button("新建问题", "new-issue", "primary")}${button("复测通过并关闭", "close-issue")}</div></div>`)}`;
  }

  function historyPage() {
    return `${pageHead("历史", "按项目查看运行历史与报告入口")}
      ${panel("运行历史", `<div class="filterbar"><label>运行状态 <select class="input"><option>全部</option><option>completed</option><option>failed</option></select></label><label>业务结论 <select class="input"><option>全部</option><option>passed</option><option>failed</option></select></label></div>
      <div class="table-wrap"><table class="data"><thead><tr><th>运行 ID</th><th>状态</th><th>结论</th><th>范围</th><th>开始时间</th><th>用时</th><th>报告</th></tr></thead><tbody>
        ${RUNS.map((run) => `<tr><td class="mono">${esc(run.run_id)}</td><td>${status(run.status)}</td><td>${status(run.outcome)}</td><td>${esc(run.scope)}</td><td>${esc(run.started_at)}</td><td>${run.elapsed_seconds} 秒</td><td><button class="button small ghost" data-action="open-ledger">打开台账</button></td></tr>`).join("")}
      </tbody></table></div>`)}`;
  }

  function settingRow(name, value, extra = "") {
    return `<div class="fact"><dt>${name}</dt><dd>${value} ${extra}</dd></div>`;
  }

  function settingsPage() {
    return `${pageHead("设置", "按配置域管理项目、环境与本地数据")}
      ${panel("环境自检", `<div class="panel-body"><dl class="detail-list">
        ${settingRow("Python", "3.13.13 · 独立 venv", status("ok"))}${settingRow("包版本", "演示数据 · 0.4.10", status("info"))}
        ${settingRow("模型", "未配置", status("warn"))}${settingRow("GitHub", "未配置", status("warn"))}
        ${settingRow("工作空间", "%APPDATA%\\aitest\\workspaces\\demo", status("ok"))}${settingRow("代理", "系统代理", status("info"))}
      </dl><div class="inline-actions">${button("重新检测", "refresh")}</div></div>`)}
      ${panel("项目规则", `<div class="panel-body"><dl class="detail-list">${settingRow("规则版本", PROJECT.rules_version_id)}${settingRow("证据要求", "必需证据、脱敏策略与对象摘要")}${settingRow("当前修订", PROJECT.revision)}</dl><div class="inline-actions">${button("编辑规则", "edit-rules")}</div></div>`)}
      ${panel("项目配置", `<div class="panel-body"><div class="table-wrap"><table class="data"><thead><tr><th>配置段</th><th>内容</th><th>状态</th></tr></thead><tbody>
        <tr><td>model</td><td>HTTPS 端点与凭据引用</td><td>${status("warn")}</td></tr><tr><td>services</td><td>启动命令、端口与健康检查</td><td>${status("ok")}</td></tr>
        <tr><td>link</td><td>源码定位与模块映射</td><td>${status("ok")}</td></tr><tr><td>runners</td><td>Python 3.13、工作目录</td><td>${status("ok")}</td></tr>
        <tr><td>evidence</td><td>对象存储、脱敏和保留</td><td>${status("ok")}</td></tr><tr><td>l1</td><td>加载、语法与入口检查</td><td>${status("ok")}</td></tr>
        <tr><td>data</td><td>数据隔离与复位策略</td><td>${status("ok")}</td></tr>
      </tbody></table></div></div>`)}
      ${panel("模板与备份", `<div class="panel-body"><div class="table-wrap"><table class="data"><thead><tr><th>项目</th><th>说明</th><th>操作</th></tr></thead><tbody>
        <tr><td>内置模板</td><td>Python service、Python library、ticket workflow</td><td><button class="button small ghost" data-action="template-detail">查看清单</button></td></tr>
        <tr><td>备份与恢复</td><td>已提交记录与永久证据对象</td><td><button class="button small" data-action="backup">创建备份</button></td></tr>
        <tr><td>运行数据清理</td><td>只清理确认可回收的临时材料</td><td><button class="button small danger" data-action="cleanup">检查清理范围</button></td></tr>
      </tbody></table></div></div>`)}`;
  }

  function route() {
    const hash = location.hash || "#/work";
    let view = "work";
    let projectMode = false;
    if (hash.startsWith("#/project/")) { view = "project"; projectMode = true; }
    else if (hash.startsWith("#/ledger")) view = "ledger";
    else if (hash.startsWith("#/issues")) view = "issues";
    else if (hash.startsWith("#/history")) view = "history";
    else if (hash.startsWith("#/settings")) view = "settings";
    const activeNav = view === "project" ? "work" : view;
    $$(".nav-item").forEach((item) => {
      const active = item.dataset.nav === activeNav;
      item.classList.toggle("is-active", active);
      if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
    });
    $("#current-project").textContent = PROJECT.name;
    $("#workspace-meta").textContent = "ws_demo01";
    $("#workspace-path").textContent = "%APPDATA%\\aitest\\workspaces\\demo";
    $("#nav-issue-count").textContent = openIssues().length;
    $("#nav-history-count").textContent = RUNS.length;
    const viewNode = $("#view");
    viewNode.innerHTML = projectMode ? projectPage() : view === "ledger" ? ledgerPage() : view === "issues" ? issuesPage() : view === "history" ? historyPage() : view === "settings" ? settingsPage() : workPage();
    viewNode.focus({preventScroll: true});
    bindViewControls();
  }

  function showToast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.classList.add("is-visible");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => node.classList.remove("is-visible"), 2600);
  }

  function modal(title, body, footer = "") {
    $("#modal-root").innerHTML = `<div class="modal-backdrop" data-action="modal-close"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div class="modal-head"><h2 id="modal-title">${title}</h2><button class="modal-close" data-action="modal-close" aria-label="关闭">×</button></div><div class="modal-body">${body}</div>${footer ? `<div class="modal-foot">${footer}</div>` : ""}</section></div>`;
    $(".modal").addEventListener("click", (event) => event.stopPropagation());
  }

  function reportModal() {
    modal("测试报告", `<dl class="detail-list">
      <div class="fact"><dt>报告 ID</dt><dd class="mono">${esc(currentRun().latest_report_id)}</dd></div>
      <div class="fact"><dt>业务结论</dt><dd>${status(currentRun().outcome)}</dd></div>
      <div class="fact"><dt>覆盖</dt><dd>S=4 · M=4 · E=4 · R=0 · V=4 · P=4</dd></div>
      <div class="fact"><dt>缺口</dt><dd>无影响结论的缺口</dd></div>
      <div class="fact"><dt>报告类型</dt><dd>运行快照 · 只读</dd></div>
      <div class="fact"><dt>验收意见</dt><dd>演示数据，不替代真实业务验收</dd></div>
    </dl><div class="callout">演示原型只展示报告字段和证据引用，不内联证据正文。</div>`, `${button("关闭", "modal-close")}${button("导出 Markdown", "export-report", "primary")}`);
  }

  function issueModal(id) {
    const issue = ISSUES.find((item) => item.issue_id === id) || ISSUES[0];
    modal(`问题详情 · ${esc(issue.issue_id)}`, `<dl class="detail-list">
      <div class="fact"><dt>状态</dt><dd>${status(issue.status)}</dd></div><div class="fact"><dt>严重度</dt><dd>${status(issue.severity)}</dd></div>
      <div class="fact"><dt>签名</dt><dd class="mono">${esc(issue.signature)}</dd></div><div class="fact"><dt>修订</dt><dd>${issue.revision}</dd></div>
      <div class="fact"><dt>预期</dt><dd>${esc(issue.expected)}</dd></div><div class="fact"><dt>实际</dt><dd>${esc(issue.actual)}</dd></div>
      <div class="fact"><dt>修复版本</dt><dd>${fmt(issue.fix_version)}</dd></div><div class="fact"><dt>验证运行</dt><dd class="mono">${fmt(issue.verification_run_id)}</dd></div>
      <div class="fact"><dt>发生次数</dt><dd>${issue.occurrences}</dd></div><div class="fact"><dt>最近发生</dt><dd>${esc(issue.latest_at)}</dd></div>
    </dl><div class="callout warn">关闭门禁：需要绑定新的运行中的通过结果，并保留复测证据。</div>`, `${button("关闭", "modal-close")}${button("记录复测结果", "issue-transition", "primary")}`);
  }

  function planModal(waiting = false) {
    const plan = PLANS[waiting ? 1 : 0];
    modal(`计划 ${plan.plan_no} · ${waiting ? "执行前确认" : "计划详情"}`, `<dl class="detail-list">
      <div class="fact"><dt>计划 ID</dt><dd class="mono">${esc(plan.plan_id)}</dd></div><div class="fact"><dt>状态</dt><dd>${status(plan.status)}</dd></div>
      <div class="fact"><dt>范围</dt><dd>${esc(plan.scope)}</dd></div><div class="fact"><dt>用例数</dt><dd>${plan.applicable_count}</dd></div>
    </dl>${waiting ? '<label style="display:flex;gap:8px;align-items:flex-start;margin-top:14px"><input id="confirm-plan-check" type="checkbox"> <span>我已核对计划范围和执行入口，确认启动本次测试。</span></label>' : ""}`, waiting ? `${button("取消", "modal-close")}${button("确认并开始测试", "start-run", "primary")}` : button("关闭", "modal-close"));
  }

  function evidenceModal(ref) {
    const item = EVIDENCE.find((entry) => entry.ref === ref) || EVIDENCE[0];
    modal(`证据 · ${esc(item.ref)}`, `<dl class="detail-list">
      <div class="fact"><dt>媒体类型</dt><dd>${esc(item.media_type)}</dd></div><div class="fact"><dt>大小</dt><dd>${item.size} B</dd></div>
      <div class="fact"><dt>对象摘要</dt><dd class="mono">${esc(item.sha256)}</dd></div><div class="fact"><dt>存在</dt><dd>${item.exists ? status("ok") : status("bad")}</dd></div>
      <div class="fact"><dt>截断</dt><dd>${item.truncated ? "是" : "否"}</dd></div><div class="fact"><dt>备注</dt><dd>${esc(item.note)}</dd></div>
    </dl><div class="mono-block">{"boundary":0,"error_handled":true,"load":"ok","normal":5}</div>`, `${button("关闭", "modal-close")}${button("复制摘要", "copy-evidence")}`);
  }

  function bindViewControls() {
    const homeRun = $("#home-run-select");
    if (homeRun) homeRun.addEventListener("change", () => { state.runId = homeRun.value; route(); });
    const ledgerRun = $("#ledger-run-select");
    if (ledgerRun) ledgerRun.addEventListener("change", () => { state.runId = ledgerRun.value; route(); });
    const issueSearch = $("#issue-search");
    if (issueSearch) issueSearch.addEventListener("change", () => { state.issueQuery = issueSearch.value; route(); });
    const issueSeverity = $("#issue-severity");
    if (issueSeverity) issueSeverity.addEventListener("change", () => { state.issueSeverity = issueSeverity.value; route(); });
  }

  function handleAction(action, element) {
    if (action.startsWith("issue-detail:")) return issueModal(action.split(":")[1]);
    switch (action) {
      case "refresh": showToast("已刷新演示数据"); route(); break;
      case "open-ledger": location.hash = "#/ledger"; break;
      case "open-issues": location.hash = "#/issues"; break;
      case "view-report": reportModal(); break;
      case "view-plan": planModal(false); break;
      case "confirm-plan": planModal(true); break;
      case "start-run": {
        const checked = $("#confirm-plan-check")?.checked;
        if (!checked) { showToast("请先勾选执行前确认"); return; }
        closeModal(); showToast("演示原型：已确认启动测试，未连接真实后端"); break;
      }
      case "export-report": closeModal(); showToast("演示原型：将报告导出到 report.export 路径"); break;
      case "rerun": modal("重跑确认", '<div class="callout">实际接入后将创建新的运行，不复用旧执行结果。</div>', `${button("取消", "modal-close")}${button("确认重跑", "rerun-confirm", "primary")}`); break;
      case "rerun-confirm": closeModal(); showToast("演示原型：已记录重跑意图"); break;
      case "issue-detail": issueModal(element.dataset.issueId); break;
      case "evidence-detail": evidenceModal(element.dataset.evidence); break;
      case "issue-tab": state.issueTab = element.dataset.tab; route(); break;
      case "issue-transition": closeModal(); showToast("演示原型：等待绑定新的验证运行"); break;
      case "new-task": modal("新建任务", '<label class="fact" style="display:block"><span class="muted">任务名称</span><input class="input" style="width:100%;margin-top:6px" placeholder="例如：构建与最小启动验证"></label><label class="fact" style="display:block"><span class="muted">验证意图</span><textarea class="input" style="width:100%;margin-top:6px;min-height:80px" placeholder="说明要验证的行为"></textarea></label>', `${button("取消", "modal-close")}${button("保存草稿", "save-task", "primary")}`); break;
      case "save-task": closeModal(); showToast("演示原型：任务草稿未提交后端"); break;
      case "task-detail": modal("任务详情", `<dl class="detail-list"><div class="fact"><dt>任务</dt><dd>${esc(TASK.title)}</dd></div><div class="fact"><dt>负责人</dt><dd>${esc(TASK.executor)}</dd></div><div class="fact"><dt>输入</dt><dd>${esc(TASK.inputs)}</dd></div><div class="fact"><dt>输出</dt><dd>${esc(TASK.outputs)}</dd></div><div class="fact"><dt>前置条件</dt><dd>${esc(TASK.preconditions)}</dd></div><div class="fact"><dt>验收项</dt><dd>${TASK.acceptance_items}</dd></div></dl>`, button("关闭", "modal-close")); break;
      case "generate-plan": showToast("演示原型：将基于当前任务生成计划草稿"); break;
      case "edit-project": showToast("演示原型：编辑项目仅做界面反馈"); break;
      case "edit-rules": showToast("演示原型：规则编辑入口已打开"); break;
      case "template-detail": modal("内置模板", '<div class="table-wrap"><table class="data"><thead><tr><th>模板</th><th>用途</th></tr></thead><tbody><tr><td>python-service</td><td>Python 服务与启动检查</td></tr><tr><td>python-library</td><td>库函数与单元行为</td></tr><tr><td>ticket-workflow</td><td>工单闭环与人工核验</td></tr></tbody></table></div>', button("关闭", "modal-close")); break;
      case "backup": showToast("演示原型：将创建本地备份"); break;
      case "cleanup": modal("运行数据清理", '<div class="callout warn">只清理已确认可回收的临时材料；永久证据与报告不会被删除。</div>', `${button("取消", "modal-close")}${button("检查范围", "cleanup-check", "primary")}`); break;
      case "cleanup-check": closeModal(); showToast("当前没有可回收的临时材料"); break;
      case "copy-evidence": showToast("演示原型：已复制证据摘要"); break;
      default: break;
    }
  }

  function closeModal() { $("#modal-root").innerHTML = ""; }

  document.addEventListener("click", (event) => {
    const actionNode = event.target.closest("[data-action]");
    if (actionNode && actionNode.dataset.action === "modal-close") {
      closeModal();
      return;
    }
    if (actionNode && actionNode.dataset.action) {
      handleAction(actionNode.dataset.action, actionNode);
      return;
    }
    const routeNode = event.target.closest("[data-route]");
    if (routeNode) location.hash = routeNode.dataset.route;
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
    const rowNode = event.target.closest("tr[data-action]");
    if (rowNode && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      handleAction(rowNode.dataset.action, rowNode);
    }
  });

  $("#project-switch-btn").addEventListener("click", () => {
    state.menuOpen = !state.menuOpen;
    const menu = $("#project-menu");
    menu.hidden = !state.menuOpen;
    $("#project-switch-btn").setAttribute("aria-expanded", String(state.menuOpen));
    menu.innerHTML = `<button class="is-active" data-project-id="${PROJECT.project_id}">${esc(PROJECT.name)}<br><span class="faint small mono">${esc(PROJECT.project_id)}</span></button>`;
  });
  $("#project-menu").addEventListener("click", (event) => {
    if (event.target.closest("[data-project-id]")) {
      state.menuOpen = false;
      $("#project-menu").hidden = true;
      $("#project-switch-btn").setAttribute("aria-expanded", "false");
      showToast("已切换到当前演示项目");
    }
  });

  window.addEventListener("hashchange", route);
  if (!location.hash) location.hash = "#/work";
  else route();
})();
