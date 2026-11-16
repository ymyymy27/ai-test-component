/** 开发/截图用的静态宿主：只在 `vite dev` 且 URL 带 mock 参数时加载。
 *
 * 数据是与契约同形的演示数据，不连接真实工作空间，也不进入生产构建。
 */
import type { HostBridge } from "@/api/host";
import type { DoctorFacts } from "@/api/types";

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
  evidence_refs: [
    "evidence/screenshot-order-list.png",
    "evidence/response.json",
    "evidence/stock-log.txt",
  ],
  cases: [
    {
      case_id: "case_1",
      result: "failed",
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
  scope: run.scope,
  applicable_count: 8,
  cases: planCases,
  status: "published",
  revision: 1,
};

const doctor: DoctorFacts = {
  workspace: { ok: true, path: "C:\\Users\\demo\\AppData\\Roaming\\aitest\\workspaces\\ws_demo", workspace_id: "ws_demo" },
  python: { ok: true, version: "3.13.1" },
  package: { ok: true, version: "0.4.10" },
  model: { ok: true, source: "user-config" },
  github: { ok: true, path: "C:\\Program Files\\GitHub CLI\\gh.exe" },
  proxy: { ok: true },
  repairs: [
    { risk: "medium", command: "安装 playwright 浏览器依赖：python -m playwright install chromium", auto: false },
  ],
  repairs_done: [],
};

const issues = [
  {
    issue_id: "issue_1",
    title: "重复提交同一幂等键生成两条订单",
    status: "pending",
    severity: "P1",
    occurrences: 2,
  },
  {
    issue_id: "issue_2",
    title: "并发下单时库存被重复扣减",
    status: "retest",
    severity: "P2",
    occurrences: 1,
  },
];

function ok(data: unknown) {
  return { ok: true as const, data };
}

function fail(code: string, message: string, nextStep: string) {
  return { ok: false as const, error: { code, message, next_step: nextStep } };
}

export function installMockHost(scenario = "result"): void {
  const initial = {
    view: scenario,
    projectId: project.project_id,
    runId: RUN_ID,
    planId: PLAN_ID,
  };
  let state: unknown = initial;

  const host: HostBridge = {
    getState: () => state,
    setState: (next: unknown) => {
      state = next;
    },
    async call(command: string, payload: object): Promise<ReturnType<typeof ok> | ReturnType<typeof fail>> {
      const body = (payload ?? {}) as Record<string, unknown>;
      switch (command) {
        case "doctor":
          return ok(doctor);
        case "project.list":
          return ok({
            projects: [
              { project_id: project.project_id, name: project.name, root: project.root },
              { project_id: "proj_other", name: "会员中心", root: "E:\\work\\member-center" },
            ],
          });
        case "project.show":
          return ok({ project });
        case "project.add":
          return ok({ project_id: project.project_id, revision: 1, existing: true });
        case "task.suggest":
          return ok({
            candidates: [
              {
                title: "验证下单成功后订单只出现一次",
                reason: "order 模块近三次提交都改动了创建逻辑",
                source: "git",
              },
              {
                title: "验证并发下单时库存只扣减一次",
                reason: "未关闭问题里有库存重复扣减",
                source: "issue",
              },
            ],
            fallback: "验证项目能否启动并跑通主流程",
            ai_generated: true,
          });
        case "task.add":
          return ok({
            task_id: TASK_ID,
            revision: 1,
            acceptance_items: items.map((item) => ({
              item_id: item.item_id,
              status: item.status,
              text: item.text,
            })),
          });
        case "task.show":
          return ok({ task });
        case "plan.generate":
          return ok({
            plan_id: PLAN_ID,
            revision: 1,
            status: "published",
            scope: plan.scope,
            coverage: { selected: 3, applicable: 8 },
            cases: planCases.length,
          });
        case "plan.show":
          return ok({ plan });
        case "run.start":
          return ok({ run_id: RUN_ID, status: "running", scope: { selected: 1, applicable: 8 } });
        case "run.status":
          if (body["run_id"] === RUN_ID) {
            if (scenario === "run") {
              return ok({
                run: {
                  ...run,
                  status: "running",
                  cases: run.cases.map((item, index) =>
                    index < 4 ? item : { ...item, result: "not_run", attempts: [], evidence_refs: [] },
                  ),
                },
                outcome: null,
              });
            }
            return ok({ run, outcome: "failed" });
          }
          return ok({
            run: { ...run, run_id: String(body["run_id"] ?? ""), status: "cancelled" },
            outcome: "incomplete",
          });
        case "run.list":
          return ok({
            runs: [
              { run_id: RUN_ID, status: "completed", started_at: NOW, heartbeat_at: NOW },
              {
                run_id: "run_prev",
                status: "cancelled",
                started_at: "2026-11-15T10:02:00+08:00",
                heartbeat_at: "2026-11-15T10:08:00+08:00",
              },
              {
                run_id: "run_old",
                status: "interrupted",
                started_at: "2026-11-14T16:40:00+08:00",
                heartbeat_at: "2026-11-14T16:41:00+08:00",
              },
            ],
          });
        case "run.cancel":
          return ok({ cancel_requested: true });
        case "analysis.run":
          return ok({ status: "generated", analysis: run.analysis[0] });
        case "issue.list":
          return ok({ issues });
        case "report.generate":
          return ok({ report_id: "rep_demo", outcome: "failed", counts: { selected: 3, applicable: 8 } });
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
