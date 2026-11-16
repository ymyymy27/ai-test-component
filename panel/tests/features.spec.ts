import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import SessionView from "@/views/SessionView.vue";
import { loadProposal, session } from "@/state/session";

import {
  PLAN,
  PROJECT,
  TEMPLATE_RESULT,
  installHost,
  resetSession,
  sessionRoutes,
  type Route,
} from "./fixtures";

async function settle(times = 4): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await flushPromises();
  }
}

async function openAdjust(overrides: Record<string, Route> = {}): Promise<{
  wrapper: VueWrapper;
  harness: ReturnType<typeof installHost>;
}> {
  const harness = installHost(sessionRoutes(overrides));
  resetSession();
  session.project = PROJECT;
  session.projects = [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }];
  await loadProposal();
  const wrapper = mount(SessionView, { attachTo: document.body });
  await settle();
  await wrapper.find('[data-testid="open-adjust"]').trigger("click");
  await settle();
  return { wrapper, harness };
}

describe("调整抽屉：模板处置", () => {
  it("应用模板后只读展示检查项与处置", async () => {
    let planShows = 0;
    const { wrapper, harness } = await openAdjust({
      "plan.show": () => {
        planShows += 1;
        return { plan: planShows === 1 ? PLAN : { ...PLAN, templates: [TEMPLATE_RESULT] } };
      },
    });

    expect(wrapper.find('[data-testid="adjust-drawer"]').text()).toContain("模板检查项");
    await wrapper.find('[data-testid="apply-template"]').trigger("click");
    await settle(6);

    const apply = harness.calls.find((call) => call.command === "template.apply");
    expect(apply?.payload).toMatchObject({
      project_id: PROJECT.project_id,
      task_id: "task_1",
      template_name: "python-service",
      plan_id: PLAN.plan_id,
    });

    const text = wrapper.find('[data-testid="adjust-drawer"]').text();
    expect(text).toContain("服务能启动");
    expect(text).toContain("适用");
    expect(text).toContain("状态可核对");
    expect(text).toContain("未知");
    expect(text).toContain("模型未提供理由");
    expect(text).toContain("不进入必测分母");
    wrapper.unmount();
  });
});

describe("调整抽屉：模块建议", () => {
  it("展示建议，确认后逐条调用 module.add", async () => {
    const { wrapper, harness } = await openAdjust();

    await wrapper.find('[data-testid="module-suggest-button"]').trigger("click");
    await settle();

    const list = wrapper.find('[data-testid="module-suggestions"]');
    expect(list.text()).toContain("order");
    expect(list.text()).toContain("stock（calls）");

    await wrapper.find('[data-testid="module-confirm-button"]').trigger("click");
    await settle(6);

    const adds = harness.calls.filter((call) => call.command === "module.add");
    expect(adds).toHaveLength(2);
    expect(adds[0].payload).toMatchObject({
      project_id: PROJECT.project_id,
      name: "order",
      deps: [{ to: "stock", type: "calls", note: "扣减" }],
    });
    expect(adds[1].payload).toMatchObject({ name: "stock" });
    wrapper.unmount();
  });
});
