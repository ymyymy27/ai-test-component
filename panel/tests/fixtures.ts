/** 测试用宿主：按命令返回固定数据，并记录调用。 */
import type { HostBridge, HostCallResult } from "@/api/host";
import { session } from "@/state/session";
import type { DoctorFacts, Plan, Project, RunDetail, Task } from "@/api/types";

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

export function resetSession(): void {
  session.ready = true;
  session.booting = false;
  session.bootError = null;
  session.view = "project";
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
  session.loading = {};
  session.notice = "";
  session.hostContext = null;
  session.prefillRoot = "";
}

export const PROJECT: Project = {
  project_id: "proj_test",
  name: "订单服务",
  root: "E:\\work\\order-service",
  goal: "下单改版验证",
  revision: 2,
  modules: [
    { module_id: "mod_order", name: "订单" },
    { module_id: "mod_stock", name: "库存" },
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
  title: "下单改版验证",
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
  revision: 1,
};

export const DOCTOR: DoctorFacts = {
  workspace: { ok: true, path: "C:\\data\\ws" },
  python: { ok: true, version: "3.13.1" },
  package: { ok: true, version: "0.4.10" },
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
        observed: "两次相同幂等键都生成了订单",
        evidence_refs: ["evidence/order-list.png"],
        candidate_causes: [{ cause: "幂等键没有唯一约束", confidence: "medium" }],
        unverified: ["生产环境是否同样"],
        next_steps: ["加唯一索引后重放"],
      },
    ],
  },
};

export function wizardRoutes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return {
    "task.suggest": {
      candidates: [
        { title: "验证下单成功后订单只出现一次", reason: "order 模块近三次提交", source: "git" },
        { title: "验证并发下单库存只扣一次", reason: "未关闭问题", source: "issue" },
      ],
      fallback: "验证项目能否启动并跑通主流程",
      ai_generated: true,
    },
    "task.add": {
      task_id: TASK.task_id,
      revision: 1,
      acceptance_items: ITEMS.map((item) => ({
        item_id: item.item_id,
        status: item.status,
        text: item.text,
      })),
    },
    "task.show": { task: TASK },
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
    ...overrides,
  };
}
