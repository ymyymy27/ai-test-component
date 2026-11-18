/** 开发/截图用的静态宿主：只在 `vite dev` 且 URL 带 mock 参数时加载。
 *
 * 数据是与契约同形的演示数据，不连接真实工作空间，也不进入生产构建。
 */
import type { HostBridge } from "@/api/host";
import type { DoctorFacts, Issue, IssueSummary, TemplateResult } from "@/api/types";

const NOW = "2026-11-16T14:20:00+08:00";
const TASK_ID = "task_demo";
const PLAN_ID = "plan_demo";
const RUN_ID = "run_demo";

const project = {
  project_id: "proj_demo",
  name: "订单服务",
  root: "E:\\work\\order-service",
  goal: "下单改版验证",
  revision: 4,
  modules: [
    { module_id: "mod_order", name: "订单", responsibility: "下单与查询", status: "开发中" },
    { module_id: "mod_stock", name: "库存", responsibility: "扣减与回滚", status: "开发中" },
    { module_id: "mod_pay", name: "支付", responsibility: "支付回调", status: "稳定" },
    { module_id: "mod_notify", name: "通知", responsibility: "短信与站内信", status: "稳定" },
  ],
};

const items = [
  { item_id: "ai_1", text: "下单后订单列表出现该订单，且只出现一次", observable: true, status: "active", source: "任务描述" },
  { item_id: "ai_2", text: "库存扣减一次", observable: true, status: "active", source: "任务描述" },
  { item_id: "ai_3", text: "支付回调重复到达时不重复扣款", observable: true, status: "active", source: "任务描述" },
  { item_id: "ai_4", text: "下单失败时库存回滚", observable: true, status: "active", source: "任务描述" },
  { item_id: "ai_5", text: "收银台页面能提交订单", observable: true, status: "active", source: "任务描述" },
  { item_id: "ai_6", text: "订单详情显示正确金额", observable: true, status: "active", source: "任务描述" },
  { item_id: "ai_7", text: "通知在 30 秒内送达", observable: true, status: "active", source: "任务描述" },
  { item_id: "ai_8", text: "系统整体要稳定", observable: true, status: "active", source: "任务描述" },
];

const task = {
  task_id: TASK_ID,
  project_id: project.project_id,
  title: "下单改版验证",
  goal: "上线前验证下单与库存扣减",
  intent: "business",
  acceptance_items: items,
  revision: 2,
};

const planCases = [
  {
    case_id: "case_1",
    acceptance_item_id: "ai_1",
    layer: "L2",
    title: "重复提交同一订单请求",
    expected: "订单列表只出现一条该订单",
    expectation_source: "下单后订单列表出现该订单，且只出现一次",
    verify_method: "command:python -m pytest tests/test_order.py",
    importance: "high",
    depends_on: "build",
    steps: ["使用相同幂等键连续提交两次", "查询订单列表", "核对订单数量与库存"],
    preconditions: ["测试库可用", "订单服务已启动"],
  },
  {
    case_id: "case_2",
    acceptance_item_id: "ai_1",
    layer: "L2",
    title: "单次下单后查询订单",
    expected: "订单详情返回下单时的商品与金额",
    expectation_source: "下单后订单列表出现该订单，且只出现一次",
    verify_method: "command:python -m pytest tests/test_order_query.py",
    importance: "medium",
  },
  {
    case_id: "case_3",
    acceptance_item_id: "ai_2",
    layer: "L3",
    title: "并发下单核对库存流水",
    expected: "库存只扣减一次，流水只有一条",
    expectation_source: "库存扣减一次",
    verify_method: "script:check_stock.py",
    importance: "high",
    depends_on: "service",
    steps: ["并发提交 2 个订单", "读取库存流水", "核对扣减次数"],
  },
  {
    case_id: "case_4",
    acceptance_item_id: "ai_5",
    layer: "L3",
    title: "收银台提交订单",
    expected: "提交后跳转订单成功页",
    expectation_source: "收银台页面能提交订单",
    verify_method: "playwright:checkout.spec.ts",
    importance: "high",
    depends_on: "service",
  },
  {
    case_id: "case_5",
    acceptance_item_id: "ai_5",
    layer: "L3",
    title: "收银台地址选择",
    expected: "选择地址后运费刷新",
    expectation_source: "收银台页面能提交订单",
    verify_method: "manual:现场核对",
    importance: "low",
  },
  {
    case_id: "case_6",
    acceptance_item_id: null,
    layer: "L1",
    title: "构建与入口加载",
    expected: "项目能构建且入口可导入",
    expectation_source: "项目级检查",
    verify_method: "command:python -m compileall src",
    importance: "medium",
    depends_on: "build",
  },
];

const run = {
  run_id: RUN_ID,
  plan_id: PLAN_ID,
  project_id: project.project_id,
  scope: {
    preset: "selected",
    module_ids: ["mod_order", "mod_stock"],
    acceptance_item_ids: ["ai_1", "ai_2", "ai_5"],
    frozen_at: NOW,
  },
  confirmed_by: "李四",
  confirmed_at: NOW,
  status: "completed",
  started_at: NOW,
  heartbeat_at: "2026-11-16T14:26:41+08:00",
  latest_report_id: "rep_demo",
  evidence_refs: [
    "evidence/screenshot-order-list.png",
    "evidence/response.json",
    "evidence/stock-log.txt",
  ],
  cases: [
    {
      case_id: "case_1",
      result: "passed",
      attempts: [
        {
          attempt: 1,
          result: "failed",
          started_at: NOW,
          finished_at: "2026-11-16T14:22:10+08:00",
          message:
            "AssertionError: 期望订单列表只有 1 条，实际 2 条\n  order_id=ORD-8821\n  POST /orders 返回 201，但幂等键未生效",
          evidence_refs: ["evidence/screenshot-order-list.png", "evidence/response.json"],
        },
      ],
      evidence_refs: ["evidence/screenshot-order-list.png", "evidence/response.json"],
    },
    {
      case_id: "case_2",
      result: "passed",
      attempts: [
        {
          attempt: 1,
          result: "passed",
          finished_at: "2026-11-16T14:23:02+08:00",
          message: "2 个断言通过；订单详情金额与下单金额一致",
          evidence_refs: ["evidence/order-detail.json"],
        },
      ],
      evidence_refs: ["evidence/order-detail.json"],
    },
    {
      case_id: "case_3",
      result: "failed",
      attempts: [
        {
          attempt: 1,
          result: "failed",
          finished_at: "2026-11-16T14:24:38+08:00",
          message:
            "库存流水出现 2 条扣减记录\n  expected stock=99, actual=97\n  trace: stock_service.deduct -> stock_service.deduct",
          evidence_refs: ["evidence/stock-log.txt"],
        },
      ],
      evidence_refs: ["evidence/stock-log.txt"],
    },
    {
      case_id: "case_4",
      result: "blocked",
      attempts: [
        {
          attempt: 1,
          result: "blocked",
          finished_at: "2026-11-16T14:25:05+08:00",
          message: "缺少运行依赖：playwright 未安装；未自动安装，用例标为阻塞",
          evidence_refs: [],
        },
      ],
      evidence_refs: [],
    },
    {
      case_id: "case_5",
      result: "manual_pending",
      attempts: [
        {
          attempt: 1,
          result: "manual_pending",
          message: "等待人工记录：测试环境地址选择组件不可用",
          evidence_refs: [],
        },
      ],
      evidence_refs: [],
    },
    {
      case_id: "case_6",
      result: "passed",
      attempts: [
        {
          attempt: 1,
          result: "passed",
          finished_at: "2026-11-16T14:20:40+08:00",
          message: "构建通过；入口模块可导入",
          evidence_refs: ["evidence/l1-build.txt"],
        },
      ],
      evidence_refs: ["evidence/l1-build.txt"],
    },
  ],
  analysis: [
    {
      status: "generated",
      reason: "",
      model: "deepseek-chat",
      generated_at: "2026-11-16T14:27:10+08:00",
      observed:
        "两次相同幂等键的下单请求都返回 201，订单表新增两条记录；库存扣减在订单事务提交前执行，并发时各扣一次。",
      evidence_refs: ["evidence/response.json", "evidence/stock-log.txt"],
      candidate_causes: [
        { cause: "幂等键只在网关校验，订单服务未做唯一约束", confidence: "medium" },
        { cause: "库存扣减不在订单事务内，失败回滚没有覆盖", confidence: "medium" },
      ],
      unverified: ["生产环境是否存在同样的并发窗口", "支付回调重复时的扣款路径是否独立"],
      next_steps: ["为订单号加唯一索引后重放同一幂等键", "串行执行两次下单并核对库存流水条数"],
    },
  ],
  mock_judgement: [
    { dependency: "支付", kind: "mock", reason: "tests/conftest.py:18 使用本地桩" },
  ],
};

const plan = {
  plan_id: PLAN_ID,
  task_id: TASK_ID,
  plan_no: 2,
  task_revision: 2,
  task_contract: "demo-contract",
  created_at: NOW,
  scope: run.scope,
  applicable_count: 8,
  cases: planCases,
  status: "published",
  source_signature: "sha256:demo-plan",
  revision: 1,
};

const report = {
  report_id: "rep_demo",
  run_id: RUN_ID,
  generated_at: "2026-11-16T14:27:10+08:00",
  outcome: "failed",
  conclusion: "本范围 3 / 8 通过 1、失败 1、阻塞 1；2 项未覆盖。",
  counts: { selected: 3, applicable: 8, covered: 3, passed: 1, failed: 1, incomplete: 1 },
  coverage: { selected: 3, applicable: 8 },
  changed_modules: ["order", "stock"],
  unmapped_files: [],
  evidence_refs: ["evidence/stock-log.txt"],
  mock_judgement: [{ dependency: "支付", kind: "mock", reason: "tests/conftest.py:18 使用本地桩" }],
  real_chain: false,
  summary: "本范围没有全部通过；库存扣减仍有重复。",
  summary_source: "fallback",
  opinion: null,
  issues: [{ issue_id: "issue_1", title: "并发下单时库存被重复扣减", status: "pending" }],
  issue_count: 1,
  failure_occurrences: 1,
  analysis: run.analysis,
  layers: { L1: 1, L2: 2, L3: 3 },
  rules_version_id: null,
  source_commit: null,
  templates: [],
  environment: { python: "3.13.1", services: {} },
  github: null,
  uncovered_items: [
    "ai_3：支付回调重复到达时不重复扣款",
    "ai_4：下单失败时库存回滚",
    "ai_6：订单详情显示正确金额",
    "ai_7：通知在 30 秒内送达",
    "ai_8：系统整体要稳定",
  ],
  unverified_items: [],
  item_details: [
    { item_id: "ai_1", text: items[0].text, module_id: "mod_order", selected: true, result: "passed", case_ids: ["case_1", "case_2"] },
    { item_id: "ai_2", text: items[1].text, module_id: "mod_stock", selected: true, result: "failed", case_ids: ["case_3"] },
    { item_id: "ai_5", text: items[4].text, module_id: "mod_order", selected: true, result: "blocked", case_ids: ["case_4", "case_5"] },
  ],
  case_details: planCases.map((item) => ({
    case_id: item.case_id,
    acceptance_item_id: item.acceptance_item_id,
    acceptance_item_ids: item.acceptance_item_id ? [item.acceptance_item_id] : [],
    layer: item.layer,
    title: item.title,
    expected: item.expected,
    expectation_verified: true,
    warnings: [],
    result: run.cases.find((row) => row.case_id === item.case_id)?.result ?? "not_run",
    evidence_refs: run.cases.find((row) => row.case_id === item.case_id)?.evidence_refs ?? [],
    attempts: [],
  })),
  evidence_index: [
    { ref: "evidence/screenshot-order-list.png", exists: true, bytes: 184320, sha256: "demo", media_type: "image/png" },
    { ref: "evidence/response.json", exists: true, bytes: 2048, sha256: "demo", media_type: "application/json" },
    { ref: "evidence/stock-log.txt", exists: true, bytes: 4096, sha256: "demo", media_type: "text/plain" },
  ],
  l1: { build: { ok: true }, entries: [], cases: [planCases[5]], blocking_reasons: [] },
  revision: 2,
};

const reportVersions = [
  { report_id: "rep_demo", run_id: RUN_ID, generated_at: "2026-11-16T14:27:10+08:00", outcome: "failed", revision: 2, has_opinion: false },
  { report_id: "rep_demo_1", run_id: RUN_ID, generated_at: "2026-11-16T14:26:00+08:00", outcome: "failed", revision: 1, has_opinion: true },
];

const doctor: DoctorFacts = {
  workspace: { ok: true, path: "C:\\Users\\demo\\AppData\\Roaming\\aitest\\workspaces\\ws_demo", workspace_id: "ws_demo" },
  python: { ok: true, version: "3.13.1" },
  package: { ok: true, version: "0.5.0" },
  model: { ok: true, source: "user-config" },
  github: { ok: true, path: "C:\\Program Files\\GitHub CLI\\gh.exe" },
  proxy: { ok: true },
  repairs: [
    { risk: "medium", command: "安装 playwright 浏览器依赖：python -m playwright install chromium", auto: false },
  ],
  repairs_done: [],
};

const tasks = [{ task_id: TASK_ID, title: task.title, revision: 2 }];

const deliveryDraft = {
  done: ["下单接口与订单列表查询", "库存扣减事务化"],
  not_done: ["并发下单的库存回滚仍有窗口"],
  changed_modules: ["order", "stock", "pay"],
  interface_changes: ["POST /orders 增加幂等键"],
  run_commands: ["python -m pytest tests/test_order.py"],
  test_data: ["本地测试库 order_test，每次运行前重置"],
  dependencies: ["postgres 16", "redis 7"],
  mocks: ["支付回调：tests/conftest.py 使用本地桩"],
  self_test_evidence: ["本地 12 条用例通过", "下单截图 evidence/order-list.png"],
};

const config = {
  model: { base_url: "https://api.example", name: "deepseek-chat", timeout_seconds: 90 },
  services: { api: { start: "uvicorn app:main --port 8000", health: "http://localhost:8000/health" } },
  link: { log_paths: ["logs/app.log"], log_format: "jsonl" },
  runners: { http: { timeout_seconds: 30 } },
  evidence: { media_budget_mb: 100, video: false },
  l1: { build: "python -m compileall src", entry: ["app.main"] },
  data: { cleanup: "rm -rf aitest_{run_id}" },
};


const issues: IssueSummary[] = [
  {
    issue_id: "issue_1",
    title: "并发下单时库存被重复扣减",
    status: "pending",
    severity: "P1",
    occurrences: 2,
    last_at: NOW,
    last_result: "failed",
    unstable: true,
    disposition: null,
    reason: "",
    verification_run_id: "",
    last_case_id: "case_3",
    last_run_id: RUN_ID,
  },
  {
    issue_id: "issue_2",
    title: "收银台提交后订单状态未更新",
    status: "retest",
    severity: "P2",
    occurrences: 1,
    last_at: "2026-11-16T14:25:05+08:00",
    last_result: "failed",
    unstable: false,
    disposition: "fixed",
    reason: "已补状态回写",
    verification_run_id: "",
    last_case_id: "case_4",
    last_run_id: RUN_ID,
  },
];

const issueDetails: Record<string, Issue> = {
  issue_1: {
    ...issues[0],
    reason: issues[0].reason ?? "",
    unstable: issues[0].unstable ?? false,
    last_result: issues[0].last_result ?? "",
    verification_run_id: issues[0].verification_run_id ?? "",
    disposition: issues[0].disposition ?? null,
    project_id: project.project_id,
    signature: "case_3:assertion:stock-deducted-twice",
    steps: ["并发提交 2 个订单", "读取库存流水", "核对扣减次数"],
    expected: "库存只扣减一次，流水只有一条",
    actual: "库存流水出现 2 条扣减记录",
    fix_version: null,
    revision: 2,
    occurrences: [
      { run_id: RUN_ID, case_id: "case_3", at: NOW, evidence_refs: ["evidence/stock-log.txt"] },
      { run_id: "run_prev", case_id: "case_3", at: "2026-11-15T10:06:00+08:00", evidence_refs: [] },
    ],
  },
  issue_2: {
    ...issues[1],
    reason: issues[1].reason ?? "",
    unstable: issues[1].unstable ?? false,
    last_result: issues[1].last_result ?? "",
    verification_run_id: issues[1].verification_run_id ?? "",
    disposition: issues[1].disposition ?? null,
    project_id: project.project_id,
    signature: "case_4:timeout:status-not-updated",
    steps: ["打开收银台", "提交订单", "查询订单状态"],
    expected: "提交后订单状态变为已支付",
    actual: "30 秒内状态仍未更新",
    fix_version: "1.2.0",
    revision: 3,
    occurrences: [
      { run_id: RUN_ID, case_id: "case_4", at: "2026-11-16T14:25:05+08:00", evidence_refs: [] },
    ],
  },
};

const evidenceFiles = [
  {
    ref: "evidence/screenshot-order-list.png",
    name: "screenshot-order-list.png",
    path: "C:\\Users\\demo\\AppData\\Roaming\\aitest\\workspaces\\ws_demo\\projects\\proj_demo\\runs\\run_demo\\evidence\\screenshot-order-list.png",
    bytes: 184320,
    exists: true,
  },
  {
    ref: "evidence/response.json",
    name: "response.json",
    path: "C:\\Users\\demo\\AppData\\Roaming\\aitest\\workspaces\\ws_demo\\projects\\proj_demo\\runs\\run_demo\\evidence\\response.json",
    bytes: 2048,
    exists: true,
  },
  {
    ref: "evidence/stock-log.txt",
    name: "stock-log.txt",
    path: "C:\\Users\\demo\\AppData\\Roaming\\aitest\\workspaces\\ws_demo\\projects\\proj_demo\\runs\\run_demo\\evidence\\stock-log.txt",
    bytes: 4096,
    exists: true,
  },
  {
    ref: "evidence/missing.log",
    name: "missing.log",
    path: "",
    bytes: 0,
    exists: false,
    missing_reason: "证据文件不在运行目录中",
  },
];

const templateResult: TemplateResult = {
  template_id: "python-service",
  name: "Python服务",
  version: "1.0.0",
  applied_at: NOW,
  items: [
    {
      item_id: "startup",
      layer: "L1",
      objective: "服务能启动",
      disposition: "applicable",
      reason: "有启动命令",
    },
    {
      item_id: "state",
      layer: "L2",
      objective: "状态可核对",
      disposition: "unknown",
      reason: "模型未提供理由",
    },
    {
      item_id: "k8s",
      layer: "L3",
      objective: "集群探针",
      disposition: "not_applicable",
      reason: "项目没有 Kubernetes 配置",
    },
  ],
  summary: { applicable: 1, not_applicable: 1, unsupported: 0, unknown: 1 },
};


function ok(data: unknown) {
  return { ok: true as const, data };
}

function fail(code: string, message: string, nextStep: string) {
  return { ok: false as const, error: { code, message, next_step: nextStep } };
}

export function installMockHost(scenario = "proposal"): void {
  const queryView = new URLSearchParams(window.location.search).get("view");
  const initial = {
    view:
      queryView === "issues" || queryView === "history" || queryView === "settings"
        ? queryView
        : "test",
    screen: scenario === "settings" ? "settings" : "session",
    projectId: project.project_id,
    runId: scenario === "proposal" || scenario === "smoke" ? "" : RUN_ID,
    planId: PLAN_ID,
  };
  let state: unknown = initial;
  let startedRun = false;
  let cancelled = false;
  let taskRevision = 2;
  let taskItems: {
    item_id: string;
    text: string;
    observable: boolean;
    status: "active" | "pending";
    source: string;
  }[] = items.map((item) => ({
    ...item,
    status: item.status === "pending" ? "pending" : "active",
  }));
  const planTemplates: TemplateResult[] = [];

  const taskSnapshot = () => ({
    ...task,
    intent: scenario === "smoke" ? "smoke" : task.intent,
    acceptance_items: scenario === "smoke" ? [] : taskItems,
    revision: taskRevision,
  });

  const makeDetail = (status: string, outcome: string | null, allPassed = false) => {
    const cases = allPassed
      ? run.cases.map((item) => ({ ...item, result: "passed", attempts: [] }))
      : status === "running"
        ? run.cases.map((item, index) =>
            index < 2 ? item : { ...item, result: "not_run", attempts: [], evidence_refs: [] },
          )
        : run.cases;
    const counts = allPassed
      ? { selected: 3, applicable: 8, covered: 3, passed: 3, failed: 0, incomplete: 0 }
      : status === "running"
        ? { selected: 3, applicable: 8, covered: 2, passed: 1, failed: 1, incomplete: 0 }
        : { selected: 3, applicable: 8, covered: 3, passed: 1, failed: 1, incomplete: 1 };
    const itemOutcomes = allPassed
      ? [
          { item_id: "ai_1", result: "passed", case_ids: ["case_1", "case_2"] },
          { item_id: "ai_2", result: "passed", case_ids: ["case_3"] },
          { item_id: "ai_5", result: "passed", case_ids: ["case_4", "case_5"] },
        ]
      : [
          { item_id: "ai_1", result: "passed", case_ids: ["case_1", "case_2"] },
          { item_id: "ai_2", result: status === "running" ? "not_run" : "failed", case_ids: ["case_3"] },
          { item_id: "ai_5", result: status === "running" ? "not_run" : "blocked", case_ids: ["case_4", "case_5"] },
        ];
    const current = status === "running" ? cases.find((item) => item.result === "not_run") : undefined;
    return {
      run: { ...run, status, current_case_id: current?.case_id ?? "", cases },
      plan: { ...plan, cases: planCases },
      task: taskSnapshot(),
      task_snapshot: true,
      outcome,
      counts,
      item_outcomes: itemOutcomes,
      pending_items: status === "running" ? 1 : 0,
      manual_cases: 1,
      l1: { build: { ok: true }, entries: [], cases: [planCases[5]] },
      uncovered_items: [
        "ai_3：支付回调重复到达时不重复扣款",
        "ai_4：下单失败时库存回滚",
        "ai_6：订单详情显示正确金额",
        "ai_7：通知在 30 秒内送达",
        "ai_8：系统整体要稳定",
      ],
      link: null,
    };
  };

  const host: HostBridge = {
    getState: () => state,
    setState: (next: unknown) => {
      state = next;
    },
    openPath: async () => ({ ok: true, data: true }),
    async call(command: string, payload: object): Promise<ReturnType<typeof ok> | ReturnType<typeof fail>> {
      const body = (payload ?? {}) as Record<string, unknown>;
      switch (command) {
        case "doctor":
          return ok(doctor);
        case "project.list":
          return ok({
            projects:
              scenario === "no-project"
                ? []
                : [
                    { project_id: project.project_id, name: project.name, root: project.root },
                    { project_id: "proj_other", name: "会员中心", root: "E:\\work\\member-center" },
                  ],
          });
        case "project.show":
          if (body["project_id"] === "proj_other") {
            return ok({
              project: {
                ...project,
                project_id: "proj_other",
                name: "会员中心",
                root: "E:\\work\\member-center",
              },
            });
          }
          return ok({ project });
        case "project.add":
          return ok({ project_id: project.project_id, revision: 1, existing: true });
        case "project.config":
          if (body["action"] === "update") {
            return ok({ config: { ...config, ...(body["patch"] as Record<string, unknown>) } });
          }
          return ok({ config });
        case "task.list":
          return ok({ tasks });
        case "delivery.draft":
          return ok({ draft: deliveryDraft, sources: { readme: "README.md", manifest: "pyproject.toml", commit_stat: true } });
        case "delivery.show":
          return ok({
            delivery: {
              delivery_id: "dlv_demo",
              task_id: TASK_ID,
              done: deliveryDraft.done,
              not_done: deliveryDraft.not_done,
              changed_modules: deliveryDraft.changed_modules,
              interface_changes: deliveryDraft.interface_changes,
              run_commands: deliveryDraft.run_commands,
              test_data: deliveryDraft.test_data.join("；"),
              dependencies: deliveryDraft.dependencies,
              mocks: deliveryDraft.mocks,
              self_test_evidence: deliveryDraft.self_test_evidence,
              commit: "abc1234",
              complete: true,
              revision: 1,
            },
          });
        case "delivery.save":
          return ok({ delivery_id: "dlv_demo", revision: 1, complete: true });
        case "template.list":
          return ok({ templates: [{ template_id: "python-service", name: "Python服务", version: "1.0.0", item_count: 3 }] });
        case "template.apply":
          planTemplates.push(templateResult);
          return ok({
            template: templateResult,
            path: "tasks/task_demo/templates/python-service.json",
            plan_id: PLAN_ID,
            plan_revision: 2,
          });
        case "task.suggest":
          if (scenario === "smoke") {
            return ok({
              candidates: [],
              fallback: "验证项目能否启动并跑通主流程",
              intent: "smoke",
              fallback_intent: "smoke",
              ai_generated: false,
            });
          }
          return ok({
            candidates: [
              {
                title: "验证下单成功后订单只出现一次",
                reason: "order 模块近三次提交都改动了创建逻辑",
                source: "git",
                intent: "business",
              },
              {
                title: "验证并发下单时库存只扣减一次",
                reason: "未关闭问题里有库存重复扣减",
                source: "issue",
                intent: "business",
              },
            ],
            fallback: "验证项目能否启动并跑通主流程",
            intent: "smoke",
            fallback_intent: "smoke",
            ai_generated: true,
          });
        case "task.add":
          return ok({
            task_id: TASK_ID,
            revision: taskRevision,
            intent: scenario === "smoke" ? "smoke" : "business",
            acceptance_items:
              scenario === "smoke"
                ? []
                : taskItems.map((item) => ({
                    item_id: item.item_id,
                    status: item.status,
                    text: item.text,
                  })),
          });
        case "task.show":
          return ok({
            task: {
              ...task,
              intent: scenario === "smoke" ? "smoke" : task.intent,
              acceptance_items: scenario === "smoke" ? [] : taskItems,
              revision: taskRevision,
            },
            plans: scenario === "smoke" ? [] : [PLAN_ID],
          });
        case "task.update": {
          const patch = (body["patch"] ?? {}) as {
            acceptance_items?: {
              item_id?: string;
              text: string;
              observable?: boolean;
              status?: string;
              source?: string;
            }[];
          };
          const incoming = patch.acceptance_items ?? [];
          taskItems = incoming.map((item, index) => ({
            item_id: item.item_id ?? `ai_demo_${index}`,
            text: item.text,
            observable: item.observable ?? true,
            status: item.status === "pending" ? "pending" : "active",
            source: item.source ?? "手动补充",
          }));
          taskRevision += 1;
          return ok({
            task_id: TASK_ID,
            revision: taskRevision,
            previous_revision: taskRevision - 1,
            acceptance_items: taskItems.map((item) => ({
              item_id: item.item_id,
              status: item.status,
              text: item.text,
            })),
          });
        }
        case "plan.generate": {
          const scope = (body["scope"] ?? {}) as {
            preset?: string;
            acceptance_item_ids?: string[];
          };
          const applicable = items.filter((item) => item.observable && item.status === "active").length;
          const selected =
            scope.preset === "all_applicable"
              ? applicable
              : (scope.acceptance_item_ids ?? []).length;
          return ok({
            plan_id: PLAN_ID,
            revision: 1,
            status: "published",
            source_signature: plan.source_signature,
            scope: { ...plan.scope, ...scope },
            coverage: { selected, applicable },
            cases: planCases.length,
          });
        }
        case "plan.show":
          return ok({
            plan: { ...plan, templates: planTemplates },
            coverage: { selected: plan.scope.acceptance_item_ids.length, applicable: 8 },
            stale: false,
            stale_reason: "",
            task_updated: false,
          });
        case "run.start":
          startedRun = true;
          cancelled = false;
          return ok({ run_id: RUN_ID, status: "running", scope: { selected: 3, applicable: 8 } });
        case "run.status":
          if (body["run_id"] === RUN_ID) {
            if (scenario === "passed") {
              return ok(makeDetail("completed", "passed", true));
            }
            if (cancelled) {
              return ok(makeDetail("cancelled", "incomplete"));
            }
            if (scenario === "running" || startedRun) {
              return ok(makeDetail("running", null));
            }
            return ok(makeDetail("completed", "failed"));
          }
          return ok(makeDetail("cancelled", "incomplete"));
        case "run.list": {
          if (scenario === "proposal" || scenario === "no-project" || scenario === "smoke") {
            return ok({ runs: [] });
          }
          return ok({
            runs: [
              {
                run_id: RUN_ID,
                status: scenario === "running" ? "running" : "completed",
                started_at: NOW,
                heartbeat_at: NOW,
                elapsed_seconds: 401,
                plan_id: PLAN_ID,
                plan_no: 2,
                plan_revision: 1,
                task_id: TASK_ID,
                task_title: task.title,
                task_snapshot: true,
                outcome: scenario === "running" ? null : scenario === "passed" ? "passed" : "failed",
                counts: { selected: 3, applicable: 8, covered: 3, passed: 1, failed: 1, incomplete: 1 },
                scope: { selected: 3, applicable: 8 },
                latest_report_id: "rep_demo",
              },
              {
                run_id: "run_prev",
                status: "completed",
                started_at: "2026-11-15T10:02:00+08:00",
                heartbeat_at: "2026-11-15T10:08:00+08:00",
                elapsed_seconds: 360,
                plan_id: "plan_prev",
                plan_no: 1,
                plan_revision: 2,
                task_id: TASK_ID,
                task_title: task.title,
                task_snapshot: true,
                outcome: "passed",
                counts: { selected: 2, applicable: 8, covered: 2, passed: 2, failed: 0, incomplete: 0 },
                scope: { selected: 2, applicable: 8 },
                latest_report_id: "rep_prev",
              },
              {
                run_id: "run_old",
                status: "interrupted",
                started_at: "2026-11-14T16:40:00+08:00",
                heartbeat_at: "2026-11-14T16:41:00+08:00",
                elapsed_seconds: 60,
                plan_id: "plan_old",
                plan_no: 1,
                plan_revision: 1,
                task_id: "task_old",
                task_title: "上一版下单流程",
                task_snapshot: false,
                outcome: "incomplete",
                counts: { selected: 2, applicable: 6, covered: 1, passed: 0, failed: 0, incomplete: 1 },
                scope: { selected: 2, applicable: 6 },
                latest_report_id: "",
              },
            ],
          });
        }
        case "run.cancel":
          cancelled = true;
          return ok({ cancel_requested: true });
        case "run.manual": {
          const caseId = String(body["case_id"] ?? "");
          const target = run.cases.find((item) => item.case_id === caseId);
          if (target) {
            target.result = String(body["result"] ?? "passed") as typeof target.result;
          }
          return ok({
            case_id: caseId,
            result: target?.result ?? "passed",
            steps: [{ step: String(body["step"] ?? "人工核对"), result: body["result"] ?? "passed" }],
          });
        }
        case "analysis.run":
          return ok({ status: "generated", analysis: run.analysis[0] });
        case "issue.list":
          return ok({ issues });
        case "issue.show":
          return ok({ issue: issueDetails[String(body["issue_id"] ?? "")] ?? issueDetails["issue_1"] });
        case "issue.update": {
          const issue = issues.find((item) => item.issue_id === body["issue_id"]);
          if (issue) {
            issue.status = String(body["status"] ?? issue.status) as IssueSummary["status"];
          }
          return ok({
            issue_id: String(body["issue_id"] ?? ""),
            status: issue?.status ?? "pending",
            revision: 2,
          });
        }
        case "issue.close": {
          const issue = issues.find((item) => item.issue_id === body["issue_id"]);
          if (issue) {
            issue.status = "closed";
          }
          return ok({ issue_id: String(body["issue_id"] ?? ""), status: "closed" });
        }
        case "evidence.list":
          return ok({ run_id: String(body["run_id"] ?? ""), evidence: evidenceFiles });
        case "rules.list":
          return ok({
            rules: [
              {
                rules_version_id: "rules_v1",
                name: "默认检查规则",
                version: "1.0",
              },
            ],
          });
        case "rules.show":
          return ok({
            rule: {
              rules_version_id: "rules_v1",
              project_id: project.project_id,
              name: "默认检查规则",
              version: "1.0",
              steps_md: "复现步骤要能独立执行；失败必须附证据。",
              ai_rules: ["不要用模拟数据证明真实链路"],
              evidence_requirements: ["失败截图或日志"],
              revision: 1,
            },
          });
        case "rules.bind":
          return ok({
            project_id: project.project_id,
            rules_version_id: String(body["rules_version_id"] ?? ""),
          });
        case "github.read":
          return ok({
            repo: String(body["repo"] ?? ""),
            branch: String(body["ref"] ?? "main"),
            commit: "0123456789abcdef0123456789abcdef01234567",
            files: ["src/order.py", "src/stock.py"],
            prs: [{ number: 12, title: "修复重复下单" }],
            checks: [{ name: "ci", conclusion: "success" }],
            deployment: null,
            path: "github/demo.json",
          });
        case "link.import":
          return ok({
            nodes: [],
            assertions: [],
            gaps: [],
            location: {
              first_anomaly: null,
              upstream: [],
              downstream: [],
              location: { known: false, reason: "演示宿主没有调用链日志" },
            },
            path: "runs/demo/link.json",
          });
        case "evidence.add":
          return ok({ evidence_ref: "manual-demo.log" });
        case "module.suggest":
          return ok({
            modules: [
              {
                name: "order",
                responsibility: "下单与查询",
                owner: "张三",
                deps: [{ to: "stock", type: "calls", note: "扣减" }],
              },
              { name: "stock", responsibility: "库存扣减与回滚", owner: "李四", deps: [] },
            ],
            registered: project.modules.map((item) => item.name),
          });
        case "module.add":
          return ok({ module_id: "mod_new", revision: 5 });
        case "report.generate":
          return ok({
            report_id: "rep_demo",
            run_id: RUN_ID,
            generated_at: "2026-11-16T14:35:00+08:00",
            outcome: "failed",
            counts: { selected: 3, applicable: 8 },
          });
        case "report.show":
          return ok({ report });
        case "report.list":
          return ok({ reports: reportVersions });
        case "report.export":
          return ok({ path: String(body["path"] ?? ""), bytes: 2048 });
        case "report.opinion":
          return ok({
            opinion: {
              by: String(body["by"] ?? ""),
              at: NOW,
              text: String(body["text"] ?? ""),
            },
          });
        default:
          return fail("COMMAND_NOT_ALLOWED", `演示宿主不支持：${command}`, "换一个操作");
      }
    },
  };

  window.aitestHost = host;
}
