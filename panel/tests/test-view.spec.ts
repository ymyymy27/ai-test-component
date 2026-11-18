import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";

import TestView from "@/views/TestView.vue";
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
import { workspace } from "@/state/workspace";

function planState(): void {
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
  workspace.stage = "plan";
}

describe("测试工作区", () => {
  beforeEach(() => {
    installHost(workspaceRoutes());
  });

  it("没有项目时只给登记入口", () => {
    resetWorkspace();
    const wrapper = mount(TestView);
    expect(wrapper.text()).toContain("先登记一个项目");
    expect(wrapper.text()).toContain("添加项目");
  });

  it("没有任务时显示目标候选并要求确认", () => {
    resetWorkspace();
    workspace.project = PROJECT;
    workspace.stage = "no-task";
    workspace.candidates = [
      { title: "验证下单成功后订单只出现一次", reason: "近期提交", intent: "business" },
    ];
    workspace.goal = "验证下单成功后订单只出现一次";
    const wrapper = mount(TestView);
    expect(wrapper.text()).toContain("这次要验证什么");
    expect(wrapper.text()).toContain("开始验证这个目标");
    expect(wrapper.text()).toContain("验证下单成功后订单只出现一次");
  });

  it("计划态显示范围、台账与执行前确认", () => {
    planState();
    const wrapper = mount(TestView);
    expect(wrapper.text()).toContain("计划 1");
    expect(wrapper.text()).toContain("本次 2 / 共 2 项");
    expect(wrapper.text()).toContain("执行前确认");
    expect(wrapper.text()).toContain("开始测试");
    expect(wrapper.text()).toContain("本次 2 / 共 2 项");
  });

  it("未纳入本次的验收项收在摘要里，不混进主台账", async () => {
    resetWorkspace();
    workspace.project = PROJECT;
    workspace.task = {
      ...TASK,
      acceptance_items: [
        ...TASK.acceptance_items,
        {
          item_id: "ai_3",
          text: "通知在 30 秒内送达",
          observable: true,
          status: "active",
        },
      ],
    };
    workspace.plan = {
      ...PLAN,
      applicable_count: 3,
      scope: { ...PLAN.scope, acceptance_item_ids: ["ai_1", "ai_2"] },
    };
    workspace.planDetail = {
      plan: workspace.plan,
      coverage: { selected: 2, applicable: 3 },
      stale: false,
      stale_reason: "",
      task_updated: false,
    };
    workspace.scope = ["ai_1", "ai_2"];
    workspace.stage = "plan";
    const wrapper = mount(TestView);
    expect(wrapper.text()).toContain("未纳入本次 1 项");
    expect(wrapper.text()).not.toContain("通知在 30 秒内送达");
    await wrapper.get(".excluded .ui-link").trigger("click");
    expect(wrapper.text()).toContain("通知在 30 秒内送达");
  });

  it("结果态显示结论、证据和解释，失败行展开", () => {
    resetWorkspace();
    workspace.project = PROJECT;
    workspace.task = TASK;
    workspace.plan = PLAN;
    workspace.run = RUN_DETAIL;
    workspace.stage = "result";
    const wrapper = mount(TestView);
    const text = wrapper.text();
    expect(text).toContain("没通过");
    expect(text).toContain("证据");
    expect(text).toContain("解释 · 不改变结论");
    expect(text).toContain("重跑未通过项");
    expect(text).toContain("已完成 2 / 2 条检查");
  });

  it("结果态可以调整范围并生成新计划", async () => {
    resetWorkspace();
    workspace.project = PROJECT;
    workspace.task = TASK;
    workspace.plan = PLAN;
    workspace.run = RUN_DETAIL;
    workspace.stage = "result";
    const wrapper = mount(TestView);
    const link = wrapper.findAll("button").find((item) => item.text() === "调整范围");
    expect(link).toBeTruthy();
    await link!.trigger("click");
    expect(wrapper.text()).toContain("调整本次范围");
    expect(wrapper.text()).toContain("生成新计划");
  });

  it("运行中显示停止和等待结果，不显示报告入口", () => {
    resetWorkspace();
    workspace.project = PROJECT;
    workspace.task = TASK;
    workspace.plan = PLAN;
    workspace.run = RUNNING_DETAIL;
    workspace.stage = "running";
    const wrapper = mount(TestView);
    const text = wrapper.text();
    expect(text).toContain("运行中");
    expect(text).toContain("停止");
    expect(text).toContain("运行中");
    expect(text).toContain("尚未执行");
    expect(text).not.toContain("填写验收意见");
  });

  it("确认后开始运行会带 confirm 调用 run.start", async () => {
    planState();
    const harness = installHost(
      workspaceRoutes({
        "run.status": RUNNING_DETAIL,
        "run.list": { runs: [] },
      }),
    );
    const wrapper = mount(TestView);
    await wrapper.find('input[type="checkbox"]').setValue(true);
    const start = wrapper.findAll("button").find((item) => item.text() === "开始测试");
    expect(start).toBeTruthy();
    await start!.trigger("click");
    await flushPromises();
    const call = harness.calls.find((item) => item.command === "run.start");
    expect(call?.options.confirm).toBe(true);
  });
});
