import { beforeEach, describe, expect, it } from "vitest";

import {
  PLAN,
  PROJECT,
  RUN_DETAIL,
  RUNNING_DETAIL,
  TASK,
  installHost,
  resetWorkspace,
  workspaceRoutes,
} from "./fixtures";
import {
  createTask,
  generatePlan,
  generateReport,
  openHistoryRun,
  openIssue,
  rerunFailed,
  startRun,
  workspace,
} from "@/state/workspace";

function baseState(): void {
  resetWorkspace();
  workspace.project = PROJECT;
  workspace.tasks = [{ task_id: TASK.task_id, title: TASK.title, revision: 1 }];
  workspace.task = TASK;
  workspace.planIds = [PLAN.plan_id];
  workspace.plan = PLAN;
  workspace.planDetail = {
    plan: PLAN,
    coverage: { selected: 2, applicable: 2 },
    stale: false,
    stale_reason: "",
    task_updated: false,
  };
  workspace.scope = ["ai_1", "ai_2"];
}

describe("任务台账状态动作", () => {
  beforeEach(() => {
    baseState();
  });

  it("建任务使用当前目标，不静默创建", async () => {
    const harness = installHost(workspaceRoutes());
    workspace.stage = "no-task";
    workspace.goal = "验证下单成功后订单只出现一次";
    workspace.candidateIndex = 0;
    workspace.candidates = [{ title: workspace.goal, intent: "business" }];
    const ok = await createTask();
    expect(ok).toBe(true);
    const call = harness.calls.find((item) => item.command === "task.add");
    expect(call?.payload["title"]).toBe(workspace.goal);
    expect(call?.payload["requirement"]).toBe(workspace.goal);
  });

  it("生成计划只提交已选范围，并生成新计划", async () => {
    const harness = installHost(
      workspaceRoutes({ "run.list": { runs: [] } }),
    );
    const ok = await generatePlan();
    expect(ok).toBe(true);
    const call = harness.calls.find((item) => item.command === "plan.generate");
    expect(call?.payload["scope"]).toEqual({
      preset: "selected",
      module_ids: [],
      acceptance_item_ids: ["ai_1", "ai_2"],
    });
  });

  it("项目快速检查允许空范围生成 L1 计划", async () => {
    const smokeTask = { ...TASK, intent: "smoke" as const, acceptance_items: [] };
    workspace.task = smokeTask;
    workspace.scope = [];
    const harness = installHost(
      workspaceRoutes({
        "task.show": { task: smokeTask, plans: [] },
        "run.list": { runs: [] },
      }),
    );
    const ok = await generatePlan();
    expect(ok).toBe(true);
    const call = harness.calls.find((item) => item.command === "plan.generate");
    expect(call?.payload["scope"]).toEqual({
      preset: "selected",
      module_ids: [],
      acceptance_item_ids: [],
    });
  });

  it("开始运行必须带确认，成功后进入运行中", async () => {
    const harness = installHost(
      workspaceRoutes({
        "run.status": RUNNING_DETAIL,
        "run.list": { runs: [] },
      }),
    );
    const ok = await startRun();
    expect(ok).toBe(true);
    const call = harness.calls.find((item) => item.command === "run.start");
    expect(call?.options.confirm).toBe(true);
    expect(workspace.stage).toBe("running");
  });

  it("重跑未通过项只收窄到失败的验收项", async () => {
    workspace.run = RUN_DETAIL;
    const harness = installHost(
      workspaceRoutes({
        "run.status": RUNNING_DETAIL,
        "run.list": { runs: [] },
      }),
    );
    const ok = await rerunFailed();
    expect(ok).toBe(true);
    const call = harness.calls.find((item) => item.command === "run.start");
    expect(call?.payload["acceptance_item_ids"]).toEqual(["ai_1"]);
  });

  it("打开历史运行使用运行快照并标记只读", async () => {
    installHost(workspaceRoutes());
    const ok = await openHistoryRun("run_1");
    expect(ok).toBeUndefined();
    expect(workspace.historical).toBe(true);
    expect(workspace.run?.task?.title).toBe(TASK.title);
  });

  it("打开问题详情读取完整问题", async () => {
    const harness = installHost(workspaceRoutes());
    await openIssue("issue_1");
    expect(workspace.issue?.issue_id).toBe("issue_1");
    expect(harness.calls.some((item) => item.command === "issue.show")).toBe(true);
  });

  it("生成报告后读取最新版本并保存验收意见", async () => {
    workspace.run = RUN_DETAIL;
    const harness = installHost(workspaceRoutes());
    await generateReport();
    expect(workspace.report?.report_id).toBe("rep_1");
    expect(workspace.reports.length).toBe(1);
    expect(harness.calls.some((item) => item.command === "report.show")).toBe(true);
  });
});
