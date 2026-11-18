/** 测试用宿主与固定事实：只做面板行为测试，不连接真实工作空间。 */
import type { HostBridge, HostCallResult } from "@/api/host";
import type {
  DoctorFacts,
  Delivery,
  EvidenceItem,
  Issue,
  IssueSummary,
  Plan,
  Project,
  Report,
  ReportVersion,
  RunDetail,
  RunSummary,
  Task,
  TemplateResult,
} from "@/api/types";
import { workspace } from "@/state/workspace";

export interface HostRecord {
  command: string;
  payload: Record<string, unknown>;
  options: { confirm?: boolean };
}

export type Route = unknown | ((payload: Record<string, unknown>) => unknown | Promise<unknown>);

export interface Harness {
  calls: HostRecord[];
  bridge: HostBridge;
  opened: string[];
}

export function installHost(routes: Record<string, Route>): Harness {
  const calls: HostRecord[] = [];
  const opened: string[] = [];
  let state: unknown = {};
  const bridge: HostBridge = {
    async call(
      command: string,
      payload: object,
      options?: { confirm?: boolean },
    ): Promise<HostCallResult<unknown>> {
      const body = (payload ?? {}) as Record<string, unknown>;
      calls.push({ command, payload: body, options: options ?? {} });
      const route = routes[command];
      if (route === undefined) {
        return {
          ok: false,
          error: {
            code: "COMMAND_NOT_ALLOWED",
            message: `测试宿主没有配置 ${command}`,
            next_step: "补一条路由",
          },
        };
      }
      const data = typeof route === "function" ? await route(body) : route;
      if (data && typeof data === "object" && "__error" in data) {
        return {
          ok: false,
          error: (data as { __error: { code: string; message: string; next_step?: string } }).__error,
        };
      }
      return { ok: true, data };
    },
    getState: () => state,
    setState: (next: unknown) => {
      state = next;
    },
    async openPath(path: string) {
      opened.push(path);
      return { ok: true, data: true };
    },
  };
  window.aitestHost = bridge;
  return { calls, bridge, opened };
}

export function resetWorkspace(): void {
  workspace.ready = true;
  workspace.booting = false;
  workspace.bootError = null;
  workspace.view = "test";
  workspace.stage = "plan";
  workspace.sheet = null;
  workspace.projects = [];
  workspace.project = null;
  workspace.doctor = null;
  workspace.env = "unknown";
  workspace.tasks = [];
  workspace.task = null;
  workspace.candidates = [];
  workspace.candidateIndex = -1;
  workspace.candidateLoading = false;
  workspace.candidateError = null;
  workspace.goal = "";
  workspace.planIds = [];
  workspace.plans = [];
  workspace.plan = null;
  workspace.planDetail = null;
  workspace.scope = [];
  workspace.run = null;
  workspace.historical = false;
  workspace.history = [];
  workspace.issues = [];
  workspace.issue = null;
  workspace.reports = [];
  workspace.report = null;
  workspace.loading = {};
  workspace.notice = "";
  workspace.lastError = null;
  workspace.hostContext = null;
  workspace.prefillRoot = "";
  workspace.addProjectOpen = false;
}

export const PROJECT: Project = {
  project_id: "proj_test",
  name: "订单服务",
  root: "E:\\work\\order-service",
  goal: "下单改版验证",
  revision: 2,
  modules: [
    { module_id: "mod_order", name: "订单", responsibility: "下单与查询" },
    { module_id: "mod_stock", name: "库存", responsibility: "扣减与回滚" },
  ],
};

export const ITEMS = [
  {
    item_id: "ai_1",
    text: "下单后订单列表出现该订单",
    observable: true,
    status: "active" as const,
    source: "任务描述",
    module_id: "mod_order",
  },
  {
    item_id: "ai_2",
    text: "库存扣减一次",
    observable: true,
    status: "active" as const,
    source: "任务描述",
    module_id: "mod_stock",
  },
  {
    item_id: "ai_3",
    text: "系统要稳定",
    observable: false,
    status: "pending" as const,
    source: "任务描述",
  },
];

export const TASK: Task = {
  task_id: "task_1",
  project_id: PROJECT.project_id,
  title: "验证下单成功后订单只出现一次",
  goal: "验证下单成功后订单只出现一次",
  intent: "business",
  acceptance_items: ITEMS,
  revision: 1,
};

export const PLAN_CASES = [
  {
    case_id: "case_1",
    acceptance_item_id: "ai_1",
    layer: "L2" as const,
    title: "重复提交同一订单",
    expected: "订单列表只出现一条",
    expectation_source: "下单后订单列表出现该订单",
    expectation_verified: true,
    warnings: [],
    verify_method: "command:pytest tests/test_order.py",
    importance: "high" as const,
    preconditions: ["测试库可用"],
    steps: ["提交两次", "查订单列表"],
  },
  {
    case_id: "case_2",
    acceptance_item_id: "ai_2",
    layer: "L3" as const,
    title: "并发下单核对库存",
    expected: "库存只扣一次",
    expectation_source: "库存扣减一次",
    expectation_verified: true,
    warnings: [],
    verify_method: "script:check_stock.py",
    importance: "high" as const,
    preconditions: ["服务已启动"],
    steps: ["并发提交", "查库存流水"],
  },
];

export const PLAN: Plan = {
  plan_id: "plan_1",
  task_id: "task_1",
  plan_no: 1,
  task_revision: 1,
  task_contract: "contract-1",
  created_at: "2026-11-16T14:00:00+08:00",
  scope: {
    preset: "selected",
    module_ids: [],
    acceptance_item_ids: ["ai_1", "ai_2"],
  },
  applicable_count: 2,
  cases: PLAN_CASES,
  status: "published",
  source_signature: "sha256:test-plan",
  revision: 1,
};

export const DOCTOR: DoctorFacts = {
  workspace: { ok: true, path: "C:\\data\\ws" },
  python: { ok: true, version: "3.13.1" },
  package: { ok: true, version: "0.5.0" },
  model: { ok: true, source: "user-config" },
  github: { ok: true, path: "C:\\Program Files\\GitHub CLI\\gh.exe" },
  proxy: { ok: true },
  repairs: [],
};

export const RUN_DETAIL: RunDetail = {
  outcome: "failed",
  plan: PLAN,
  task: TASK,
  task_snapshot: true,
  counts: {
    selected: 2,
    applicable: 2,
    covered: 2,
    passed: 1,
    failed: 1,
    incomplete: 0,
  },
  item_outcomes: [
    { item_id: "ai_1", result: "failed", case_ids: ["case_1"] },
    { item_id: "ai_2", result: "passed", case_ids: ["case_2"] },
  ],
  pending_items: 0,
  manual_cases: 0,
  uncovered_items: [],
  run: {
    run_id: "run_1",
    plan_id: "plan_1",
    project_id: "proj_test",
    scope: {
      preset: "selected",
      module_ids: [],
      acceptance_item_ids: ["ai_1", "ai_2"],
    },
    status: "completed",
    started_at: "2026-11-16T14:20:00+08:00",
    heartbeat_at: "2026-11-16T14:21:12+08:00",
    latest_report_id: "rep_1",
    cases: [
      {
        case_id: "case_1",
        result: "failed",
        attempts: [
          {
            attempt: 1,
            result: "failed",
            message: "期望 1 条订单，实际 2 条",
            evidence_refs: ["evidence/order-list.png"],
          },
        ],
        evidence_refs: ["evidence/order-list.png"],
      },
      {
        case_id: "case_2",
        result: "passed",
        attempts: [],
        evidence_refs: [],
      },
    ],
    evidence_refs: ["evidence/order-list.png"],
    analysis: [
      {
        status: "generated",
        reason: "",
        observed: "两次相同幂等键都生成了订单",
        evidence_refs: ["evidence/order-list.png"],
        candidate_causes: [{ cause: "幂等键没有唯一约束", confidence: "medium" }],
        unverified: ["生产环境是否同样"],
        next_steps: ["加唯一索引后重放"],
      },
    ],
  },
};

export const RUNNING_DETAIL: RunDetail = {
  ...RUN_DETAIL,
  outcome: null,
  counts: { selected: 2, applicable: 2, covered: 1, passed: 1, failed: 0, incomplete: 0 },
  item_outcomes: [
    { item_id: "ai_1", result: "passed", case_ids: ["case_1"] },
    { item_id: "ai_2", result: "not_run", case_ids: ["case_2"] },
  ],
  run: {
    ...RUN_DETAIL.run,
    status: "running",
    current_case_id: "case_2",
    cases: [
      {
        case_id: "case_1",
        result: "passed",
        attempts: [],
        evidence_refs: [],
      },
      {
        case_id: "case_2",
        result: "not_run",
        attempts: [],
        evidence_refs: [],
      },
    ],
  },
};

export const EVIDENCE: EvidenceItem[] = [
  {
    ref: "evidence/order-list.png",
    name: "order-list.png",
    path: "C:/data/ws/evidence/run_1/order-list.png",
    bytes: 2048,
    exists: true,
    media_type: "image/png",
    sha256: "abc123",
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

export const ISSUES: IssueSummary[] = [
  {
    issue_id: "issue_1",
    title: "重复提交生成两条订单",
    status: "pending",
    severity: "P1",
    occurrences: 2,
    last_at: "2026-11-16T14:20:00+08:00",
    last_result: "failed",
    unstable: false,
    disposition: null,
    reason: "",
    verification_run_id: "",
    last_case_id: "case_1",
    last_run_id: "run_1",
  },
];

export const ISSUE_DETAIL: Issue = {
  issue_id: "issue_1",
  project_id: PROJECT.project_id,
  signature: "case_1:assertion:order-count",
  title: "重复提交生成两条订单",
  steps: ["提交两次", "查订单列表"],
  expected: "订单列表只出现一条",
  actual: "出现两条",
  status: "pending",
  severity: "P1",
  disposition: null,
  reason: "",
  unstable: false,
  last_result: "failed",
  verification_run_id: "",
  fix_version: null,
  revision: 1,
  occurrences: [
    {
      run_id: "run_1",
      case_id: "case_1",
      at: "2026-11-16T14:20:00+08:00",
      evidence_refs: ["evidence/order-list.png"],
    },
  ],
};

export const REPORT: Report = {
  report_id: "rep_1",
  run_id: "run_1",
  generated_at: "2026-11-16T14:22:00+08:00",
  outcome: "failed",
  conclusion: "本范围 2 / 2 通过 1、失败 1。",
  counts: { selected: 2, applicable: 2, covered: 2, passed: 1, failed: 1, incomplete: 0 },
  coverage: { selected: 2, applicable: 2 },
  changed_modules: ["订单"],
  unmapped_files: [],
  evidence_refs: ["evidence/order-list.png"],
  mock_judgement: [],
  real_chain: true,
  summary: "本范围存在失败。",
  summary_source: "fallback",
  opinion: null,
  issues: [{ issue_id: "issue_1", title: "重复提交生成两条订单", status: "pending" }],
  issue_count: 1,
  failure_occurrences: 1,
  analysis: RUN_DETAIL.run.analysis as unknown as Record<string, unknown>[],
  layers: { L1: 0, L2: 1, L3: 1 },
  rules_version_id: null,
  source_commit: null,
  templates: [],
  environment: { python: "3.13.1", services: {} },
  github: null,
  uncovered_items: [],
  unverified_items: [],
  item_details: [
    {
      item_id: "ai_1",
      text: "下单后订单列表出现该订单",
      module_id: "mod_order",
      selected: true,
      result: "failed",
      case_ids: ["case_1"],
    },
    {
      item_id: "ai_2",
      text: "库存扣减一次",
      module_id: "mod_stock",
      selected: true,
      result: "passed",
      case_ids: ["case_2"],
    },
  ],
  case_details: PLAN_CASES.map((item) => ({
    case_id: item.case_id,
    acceptance_item_id: item.acceptance_item_id,
    acceptance_item_ids: [item.acceptance_item_id],
    layer: item.layer,
    title: item.title,
    expected: item.expected,
    expectation_verified: true,
    warnings: [],
    result: item.case_id === "case_1" ? "failed" : "passed",
    evidence_refs: item.case_id === "case_1" ? ["evidence/order-list.png"] : [],
    attempts: [],
  })),
  evidence_index: [
    { ref: "evidence/order-list.png", exists: true, bytes: 2048, sha256: "abc123", media_type: "image/png" },
  ],
  l1: null,
  revision: 1,
};

export const REPORT_VERSIONS: ReportVersion[] = [
  {
    report_id: "rep_1",
    run_id: "run_1",
    generated_at: "2026-11-16T14:22:00+08:00",
    outcome: "failed",
    revision: 1,
    has_opinion: false,
  },
];

export const RUN_SUMMARIES: RunSummary[] = [
  {
    run_id: "run_1",
    status: "completed",
    started_at: "2026-11-16T14:20:00+08:00",
    heartbeat_at: "2026-11-16T14:21:12+08:00",
    elapsed_seconds: 72,
    plan_id: "plan_1",
    plan_no: 1,
    plan_revision: 1,
    task_id: "task_1",
    task_title: TASK.title,
    task_snapshot: true,
    outcome: "failed",
    counts: { selected: 2, applicable: 2, covered: 2, passed: 1, failed: 1, incomplete: 0 },
    scope: { selected: 2, applicable: 2 },
    latest_report_id: "rep_1",
  },
  {
    run_id: "run_old",
    status: "completed",
    started_at: "2026-11-15T10:02:00+08:00",
    heartbeat_at: "2026-11-15T10:03:00+08:00",
    elapsed_seconds: 60,
    plan_id: "plan_old",
    plan_no: 1,
    plan_revision: 1,
    task_id: "task_old",
    task_title: "上一版下单流程",
    task_snapshot: false,
    outcome: "passed",
    counts: { selected: 1, applicable: 1, covered: 1, passed: 1, failed: 0, incomplete: 0 },
    scope: { selected: 1, applicable: 1 },
    latest_report_id: "",
  },
];

export const TEMPLATE_RESULT: TemplateResult = {
  template_id: "python-service",
  name: "Python服务",
  version: "1.0.0",
  applied_at: "2026-11-16T10:00:00+08:00",
  items: [
    {
      item_id: "startup",
      layer: "L1",
      objective: "服务能启动",
      disposition: "applicable",
      reason: "有启动命令",
    },
  ],
  summary: { applicable: 1, not_applicable: 0, unsupported: 0, unknown: 0 },
};

export const DELIVERY: Delivery = {
  delivery_id: "dlv_1",
  task_id: TASK.task_id,
  done: ["下单接口"],
  not_done: [],
  changed_modules: ["order"],
  interface_changes: [],
  run_commands: ["pytest tests/test_order.py"],
  test_data: "测试库",
  dependencies: [],
  mocks: [],
  self_test_evidence: ["自测截图"],
  commit: "abc1234",
  complete: true,
  revision: 1,
};

export const DELIVERY_DRAFT = {
  done: ["下单接口"],
  not_done: [],
  changed_modules: ["order"],
  interface_changes: [],
  run_commands: ["pytest tests/test_order.py"],
  test_data: ["测试库"],
  dependencies: [],
  mocks: [],
  self_test_evidence: ["自测截图"],
};

export function workspaceRoutes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return {
    doctor: DOCTOR,
    "project.list": { projects: [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }] },
    "project.show": { project: PROJECT },
    "project.add": { project_id: PROJECT.project_id, revision: 1, existing: true },
    "project.config": { config: {} },
    "rules.list": {
      rules: [{ rules_version_id: "rule_1", name: "默认规则", version: "1.0.0" }],
    },
    "rules.show": {
      rule: {
        rules_version_id: "rule_1",
        name: "默认规则",
        version: "1.0.0",
        steps_md: "按页面主流程验证",
        ai_rules: ["只读操作"],
        evidence_requirements: ["截图"],
      },
    },
    "rules.save": { rules_version_id: "rule_2", version: "1.0.1" },
    "rules.bind": { project_id: PROJECT.project_id, rules_version_id: "rule_1" },
    "task.suggest": {
      candidates: [
        {
          title: "验证下单成功后订单只出现一次",
          reason: "order 模块近三次提交",
          source: "git",
          intent: "business",
        },
      ],
      fallback: "验证项目能否启动并跑通主流程",
      intent: "smoke",
      fallback_intent: "smoke",
      ai_generated: true,
    },
    "task.list": { tasks: [{ task_id: TASK.task_id, title: TASK.title, revision: 1 }] },
    "task.add": {
      task_id: TASK.task_id,
      revision: 1,
      intent: "business",
      acceptance_items: ITEMS.map((item) => ({
        item_id: item.item_id,
        status: item.status,
        text: item.text,
      })),
    },
    "task.show": { task: TASK, plans: [PLAN.plan_id] },
    "task.update": (payload: Record<string, unknown>) => {
      const patch = payload["patch"] as
        | { acceptance_items?: { item_id?: string; text: string; status: string }[] }
        | undefined;
      const items = patch?.acceptance_items ?? [];
      return {
        task_id: TASK.task_id,
        revision: 2,
        previous_revision: 1,
        acceptance_items: items.map((item, index) => ({
          item_id: item.item_id ?? `ai_new_${index}`,
          status: item.status,
          text: item.text,
        })),
      };
    },
    "template.list": {
      templates: [
        { template_id: "python-service", name: "Python服务", version: "1.0.0", item_count: 1 },
      ],
    },
    "template.apply": {
      template: TEMPLATE_RESULT,
      path: "tasks/task_1/templates/python-service.json",
      plan_id: PLAN.plan_id,
      plan_revision: 2,
    },
    "module.suggest": {
      modules: [
        {
          name: "order",
          responsibility: "下单",
          owner: "",
          deps: [{ to: "stock", type: "calls", note: "扣减" }],
        },
      ],
      registered: [],
    },
    "module.add": { module_id: "mod_new", revision: 3 },
    "plan.generate": {
      plan_id: PLAN.plan_id,
      plan_no: 1,
      task_revision: 1,
      created_at: PLAN.created_at,
      revision: 1,
      status: "published",
      scope: PLAN.scope,
      coverage: { selected: 2, applicable: 2 },
      cases: PLAN_CASES.length,
    },
    "plan.show": {
      plan: PLAN,
      coverage: { selected: 2, applicable: 2 },
      stale: false,
      stale_reason: "",
      task_updated: false,
    },
    "plan.update": {
      plan_id: PLAN.plan_id,
      plan_no: 1,
      task_revision: 1,
      revision: 2,
      status: "published",
      warnings: [],
      unverified_cases: [],
    },
    "run.start": { run_id: "run_1", status: "running", scope: { selected: 2, applicable: 2 } },
    "run.status": RUN_DETAIL,
    "run.list": { runs: RUN_SUMMARIES },
    "run.cancel": { cancel_requested: true },
    "run.manual": {
      case_id: "case_2",
      result: "passed",
      steps: [{ step: "并发提交", result: "passed" }],
    },
    "issue.list": { issues: ISSUES },
    "issue.show": { issue: ISSUE_DETAIL },
    "issue.update": { issue_id: "issue_1", status: "in_progress", revision: 2 },
    "issue.close": { issue_id: "issue_1", status: "closed" },
    "evidence.list": { run_id: "run_1", evidence: EVIDENCE },
    "analysis.run": { status: "generated", analysis: RUN_DETAIL.run.analysis[0] },
    "report.generate": {
      report_id: "rep_1",
      run_id: "run_1",
      generated_at: "2026-11-16T14:22:00+08:00",
      outcome: "failed",
      counts: {},
    },
    "report.show": { report: REPORT },
    "report.list": { reports: REPORT_VERSIONS },
    "report.export": { path: "E:\\reports\\run.md", bytes: 10 },
    "report.opinion": { opinion: { by: "李四", at: "2026-11-16T15:00:00+08:00", text: "复测通过" } },
    "link.assert": { result: "passed" },
    "delivery.show": { delivery: DELIVERY },
    "delivery.draft": {
      draft: DELIVERY_DRAFT,
      sources: { readme: "README.md", manifest: "pyproject.toml", commit_stat: true },
    },
    "delivery.save": { delivery_id: "dlv_1", revision: 1, complete: true },
    "backup.create": {
      path: "E:\\backup\\aitest.zip",
      sha256: "abc123456789",
      bytes: 100,
      includes_evidence: false,
      files: 3,
    },
    ...overrides,
  };
}
