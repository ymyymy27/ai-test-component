import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";

import PlanDocument from "@/views/PlanDocument.vue";
import {
  PLAN,
  PROJECT,
  TASK,
  installHost,
  resetWorkspace,
  workspaceRoutes,
} from "./fixtures";
import { workspace } from "@/state/workspace";

function ready(): void {
  resetWorkspace();
  workspace.project = PROJECT;
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

describe("任务与计划文档", () => {
  beforeEach(() => {
    ready();
    installHost(workspaceRoutes());
  });

  it("保存任务时整份提交验收项", async () => {
    const harness = installHost(workspaceRoutes());
    const wrapper = mount(PlanDocument);
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((item) => item.text() === "编辑任务与验收项")!
      .trigger("click");
    const first = wrapper.find(".item-row input.ui-input");
    await first.setValue("下单后订单列表只出现一条记录");
    const save = wrapper.findAll("button").find((item) => item.text() === "保存任务");
    await save!.trigger("click");
    await flushPromises();
    const call = harness.calls.find((item) => item.command === "task.update");
    const patch = call?.payload["patch"] as
      | { acceptance_items?: { text: string; source?: string; module_id?: string | null }[] }
      | undefined;
    expect(patch?.acceptance_items?.[0]?.text).toBe("下单后订单列表只出现一条记录");
    expect(patch?.acceptance_items?.[0]?.source).toBe("任务描述");
    expect(patch?.acceptance_items?.[0]?.module_id).toBe("mod_order");
  });

  it("保存用例生成计划新版本", async () => {
    const harness = installHost(workspaceRoutes());
    const wrapper = mount(PlanDocument);
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((item) => item.text() === "编辑")!
      .trigger("click");
    const expected = wrapper.find(".case-block textarea");
    await expected.setValue("订单列表只能出现一条");
    const save = wrapper.findAll("button").find((item) => item.text() === "保存为新版本");
    await save!.trigger("click");
    await flushPromises();
    const call = harness.calls.find((item) => item.command === "plan.update");
    const patch = call?.payload["patch"] as { cases?: { expected: string }[] } | undefined;
    expect(patch?.cases?.[0]?.expected).toBe("订单列表只能出现一条");
  });

  it("模板应用使用 template_id，而不是展示名", async () => {
    const harness = installHost(workspaceRoutes());
    const wrapper = mount(PlanDocument);
    await flushPromises();
    const apply = wrapper.findAll("button").find((item) => item.text() === "应用");
    await apply!.trigger("click");
    await flushPromises();
    const call = harness.calls.find((item) => item.command === "template.apply");
    expect(call?.payload["template_name"]).toBe("python-service");
  });

  it("模块建议逐条采用写入项目", async () => {
    const harness = installHost(workspaceRoutes());
    const wrapper = mount(PlanDocument);
    await flushPromises();
    const suggest = wrapper.findAll("button").find((item) => item.text() === "让 AI 建议模块");
    await suggest!.trigger("click");
    await flushPromises();
    const adopt = wrapper.findAll("button").find((item) => item.text() === "采用");
    await adopt!.trigger("click");
    await flushPromises();
    const call = harness.calls.find((item) => item.command === "module.add");
    expect(call?.payload["name"]).toBe("order");
  });

  it("计划过期时明确提示并禁止改用例", async () => {
    workspace.planDetail = {
      ...workspace.planDetail!,
      stale: true,
      stale_reason: "验收项或验证方式已变化，需要重新生成计划",
    };
    const wrapper = mount(PlanDocument);
    await flushPromises();
    const text = wrapper.text();
    expect(text).toContain("验收项或验证方式已变化");
    expect(wrapper.find(".case-block textarea").exists()).toBe(false);
    expect(wrapper.findAll("button").some((item) => item.text() === "保存为新版本")).toBe(false);
  });
});
