/** 测试用宿主：按命令返回固定数据，并记录调用。 */
import type { HostBridge, HostCallResult } from "@/api/host";
import { session, type ProposalState } from "@/state/session";
import type {
  DoctorFacts,
  EvidenceItem,
  IssueSummary,
  Plan,
  Project,
  RunDetail,
  Task,
  TemplateResult,
} from "@/api/types";

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
    getState: () => ({}),
    setState: () => undefined,
    async openPath(path: string) {
      opened.push(path);
      return { ok: true, data: true };
    },
  };
  window.aitestHost = bridge;
  return { calls, bridge, opened };
}

export function emptyProposal(): ProposalState {
  return {
    candidates: [],
    index: -1,
    fallback: "",
    goal: "",
    basis: "",
    taskId: "",
    taskRevision: 0,
    taskGoal: "",
    items: [],
    itemsLoading: false,
    itemsError: null,
    itemsDirty: false,
    preset: "all_applicable",
    selected: [],
    moduleIds: [],
    suggestLoading: false,
    suggestError: null,
    previewPlan: null,
    previewSignature: "",
    starting: false,
    startError: null,
  };
}

export function resetSession(): void {
  session.ready = true;
  session.booting = false;
  session.bootError = null;
  session.screen = "session";
  session.phase = "proposal";
  session.projects = [];
  session.project = null;
  session.doctor = null;
  session.doctorLoading = false;
  session.env = "unknown";
  session.history = [];
  session.lastRun = null;
  session.activeRunId = "";
  session.activePlanId = "";
  session.activeTaskId = "";
  session.result = null;
  session.plan = null;
  session.task = null;
  session.issues = [];
  session.proposal = emptyProposal();
  session.loading = {};
  session.notice = "";
  session.hostContext = null;
  session.prefillRoot = "";
  session.historyOpen = false;
  session.adjustOpen = false;
  session.projectOpen = false;
  session.evidenceOpen = false;
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
  },
  {
    item_id: "ai_2",
    text: "库存扣减一次",
    observable: true,
    status: "active" as const,
    source: "任务描述",
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
    verify_method: "command:pytest tests/test_order.py",
    importance: "high" as const,
  },
  {
    case_id: "case_2",
    acceptance_item_id: "ai_2",
    layer: "L3" as const,
    title: "并发下单核对库存",
    expected: "库存只扣一次",
    expectation_source: "库存扣减一次",
    verify_method: "script:check_stock.py",
    importance: "high" as const,
  },
];

export const PLAN: Plan = {
  plan_id: "plan_1",
  task_id: "task_1",
  scope: {
    preset: "selected",
    module_ids: [],
    acceptance_item_ids: ["ai_1", "ai_2"],
  },
  applicable_count: 3,
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
  github: { ok: true },
  proxy: { ok: true },
  repairs: [],
};

export const RUN_DETAIL: RunDetail = {
  outcome: "failed",
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
    cases: [
      {
        case_id: "case_1",
        result: "failed",
        attempts: [
          {
            attempt: 1,
            result: "failed",
            message: "AssertionError: 期望 1 条订单，实际 2 条",
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
  outcome: null,
  run: {
    ...RUN_DETAIL.run,
    status: "running",
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
  },
  {
    ref: "evidence/order-detail.json",
    name: "order-detail.json",
    path: "C:/data/ws/evidence/run_1/order-detail.json",
    bytes: 512,
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

export const ISSUES: IssueSummary[] = [
  {
    issue_id: "issue_abc123",
    title: "重复提交生成两条订单",
    status: "pending",
    severity: "P1",
    occurrences: 2,
  },
];

export const RUN_SUMMARIES = [
  { run_id: "run_1", status: "completed" as const, started_at: "2026-11-16T14:20:00+08:00" },
  { run_id: "run_prev", status: "cancelled" as const, started_at: "2026-11-15T10:02:00+08:00" },
  { run_id: "run_old", status: "interrupted" as const, started_at: "2026-11-14T16:40:00+08:00" },
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
    {
      item_id: "state",
      layer: "L2",
      objective: "状态可核对",
      disposition: "unknown",
      reason: "模型未提供理由",
    },
  ],
  summary: { applicable: 1, not_applicable: 0, unsupported: 0, unknown: 1 },
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

export function sessionRoutes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return {
    doctor: DOCTOR,
    "project.list": { projects: [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }] },
    "project.show": { project: PROJECT },
    "project.add": { project_id: PROJECT.project_id, revision: 1, existing: true },
    "project.config": { config: {} },
    "task.suggest": {
      candidates: [
        {
          title: "验证下单成功后订单只出现一次",
          reason: "order 模块近三次提交",
          source: "git",
          intent: "business",
        },
        {
          title: "验证并发下单库存只扣一次",
          reason: "未关闭问题",
          source: "issue",
          intent: "business",
        },
      ],
      fallback: "验证项目能否启动并跑通主流程",
      intent: "smoke",
      fallback_intent: "smoke",
      ai_generated: true,
    },
    "task.list": { tasks: [] },
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
    "task.show": { task: TASK },
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
    "plan.generate": {
      plan_id: PLAN.plan_id,
      revision: 1,
      status: "published",
      scope: PLAN.scope,
      coverage: { selected: 2, applicable: 3 },
      cases: PLAN_CASES.length,
    },
    "plan.show": { plan: PLAN },
    "run.start": { run_id: "run_1", status: "running", scope: { selected: 2, applicable: 3 } },
    "run.status": RUN_DETAIL,
    "run.list": { runs: RUN_SUMMARIES },
    "run.cancel": { cancel_requested: true },
    "issue.list": { issues: ISSUES },
    "issue.update": { issue_id: "issue_abc123", status: "in_progress", revision: 2 },
    "issue.close": { issue_id: "issue_abc123", status: "closed" },
    "evidence.list": { run_id: "run_1", evidence: EVIDENCE },
    "analysis.run": { status: "generated", analysis: RUN_DETAIL.run.analysis[0] },
    "report.generate": { report_id: "rep_1", outcome: "failed", counts: {} },
    "report.export": { path: "E:\\reports\\run.md", bytes: 10 },
    "report.opinion": { opinion: { by: "李四", at: "2026-11-16T15:00:00+08:00", text: "复测通过" } },
    "template.list": {
      templates: [{ template_id: "python-service", name: "Python服务", version: "1.0.0", item_count: 2 }],
    },
    "template.apply": {
      template: TEMPLATE_RESULT,
      path: "tasks/task_1/templates/python-service.json",
      plan_id: PLAN.plan_id,
      plan_revision: 2,
    },
    "module.suggest": {
      modules: [
        { name: "order", responsibility: "下单", owner: "", deps: [{ to: "stock", type: "calls", note: "扣减" }] },
        { name: "stock", responsibility: "库存", owner: "", deps: [] },
      ],
      registered: [],
    },
    "module.add": { module_id: "mod_new", revision: 3 },
    "delivery.draft": {
      draft: DELIVERY_DRAFT,
      sources: { readme: "README.md", manifest: "pyproject.toml", commit_stat: true },
    },
    "delivery.save": { delivery_id: "dlv_1", revision: 1, complete: false },
    ...overrides,
  };
}
