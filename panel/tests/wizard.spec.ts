import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import WizardView from "@/views/WizardView.vue";
import { session } from "@/state/session";

import {
  PROJECT,
  installHost,
  resetSession,
  wizardRoutes,
  type Route,
} from "./fixtures";

async function mountWizard(overrides: Record<string, Route> = {}) {
  const harness = installHost(wizardRoutes(overrides));
  resetSession();
  session.project = PROJECT;
  session.projects = [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }];
  const wrapper = mount(WizardView, { attachTo: document.body });
  await flushPromises();
  return { wrapper, harness };
}

async function answerGoal(wrapper: VueWrapper) {
  await wrapper.find("textarea").setValue("验证下单流程");
  await wrapper.find('[data-testid="wizard-next"]').trigger("click");
  await flushPromises();
}

async function reachScope(wrapper: VueWrapper) {
  await answerGoal(wrapper);
  await wrapper.find('[data-testid="wizard-next"]').trigger("click");
  await flushPromises();
}

describe("新建测试向导", () => {
  it("第一步展示目标候选，选中候选后写入目标", async () => {
    const { wrapper } = await mountWizard();
    expect(wrapper.find('[data-testid="wizard-step-1"]').exists()).toBe(true);

    const candidates = wrapper.findAll('[data-testid="candidate"]');
    expect(candidates).toHaveLength(2);

    await candidates[0].trigger("click");
    const goal = wrapper.find("textarea").element as HTMLTextAreaElement;
    expect(goal.value).toBe("验证下单成功后订单只出现一次");

    const next = wrapper.find('[data-testid="wizard-next"]');
    expect(next.attributes("disabled")).toBeUndefined();
  });

  it("目标为空时不能进入下一步", async () => {
    const { wrapper } = await mountWizard();
    const next = wrapper.find('[data-testid="wizard-next"]');
    expect(next.attributes("disabled")).toBeDefined();
  });

  it("从目标进入验收标准，并逐条启用/停用", async () => {
    const { wrapper, harness } = await mountWizard();
    await answerGoal(wrapper);

    expect(wrapper.find('[data-testid="wizard-step-2"]').exists()).toBe(true);
    const addCall = harness.calls.find((call) => call.command === "task.add");
    expect(addCall?.payload).toMatchObject({
      project_id: PROJECT.project_id,
      title: "验证下单流程",
      requirement: "验证下单流程",
    });

    const rows = wrapper.findAll('[data-testid="acceptance-list"] li');
    expect(rows).toHaveLength(3);
    expect(rows[2].classes()).toContain("off");
    expect(rows[2].text()).toContain("不可观察");

    const checkbox = rows[0].find('input[type="checkbox"]');
    await checkbox.setValue(false);
    expect((checkbox.element as HTMLInputElement).checked).toBe(false);
    expect((rows[2].find('input[type="checkbox"]').element as HTMLInputElement).disabled).toBe(true);
  });

  it("范围页显示已选与适用数量，切换自选后计数随勾选变化", async () => {
    const { wrapper } = await mountWizard();
    await reachScope(wrapper);

    expect(wrapper.find('[data-testid="wizard-step-3"]').exists()).toBe(true);
    const count = wrapper.find('[data-testid="range-count"]');
    expect(count.text().replace(/\s+/g, "")).toContain("已选2/适用2条");

    const presetButtons = wrapper.findAll('.segmented button');
    await presetButtons[1].trigger("click");

    const checkboxes = wrapper.findAll('[data-testid="scope-list"] input[type="checkbox"]');
    expect(checkboxes).toHaveLength(3);
    await checkboxes[0].setValue(false);
    expect(
      wrapper.find('[data-testid="range-count"]').text().replace(/\s+/g, ""),
    ).toContain("已选1/适用2条");
  });

  it("确认页按验收项分组展示用例，开始测试带确认调用 run.start", async () => {
    const { wrapper, harness } = await mountWizard();
    await reachScope(wrapper);
    await wrapper.find('[data-testid="wizard-next"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="wizard-step-4"]').exists()).toBe(true);
    expect(harness.calls.some((call) => call.command === "plan.generate")).toBe(true);
    expect(harness.calls.some((call) => call.command === "plan.show")).toBe(true);

    const text = wrapper.text();
    expect(text).toContain("下单后订单列表出现该订单");
    expect(text).toContain("重复提交同一订单");
    expect(text).toContain("库存扣减一次");

    await wrapper.find('[data-testid="start-run"]').trigger("click");
    await flushPromises();

    const start = harness.calls.find((call) => call.command === "run.start");
    expect(start?.options.confirm).toBe(true);
    expect(start?.payload).toMatchObject({ project_id: PROJECT.project_id, plan_id: "plan_1" });
    expect(session.view).toBe("run");
    expect(session.activeRunId).toBe("run_1");
  });

  it("验收标准提取失败时说明原因，并保留手动补充入口", async () => {
    const { wrapper, harness } = await mountWizard({
      "task.add": {
        __error: {
          code: "MODEL_UNAVAILABLE",
          message: "未配置模型",
          next_step: "配置模型密钥后重试",
        },
      },
    });
    await answerGoal(wrapper);

    expect(wrapper.text()).toContain("模型没有配置或连接不上");
    expect(wrapper.text()).toContain("下一步：配置模型密钥后重试");
    expect(wrapper.find(".add-input").exists()).toBe(true);
    expect(harness.calls.some((call) => call.command === "task.add")).toBe(true);
  });

  it("计划生成失败时给出可重试的错误说明", async () => {
    const { wrapper } = await mountWizard({
      "plan.generate": {
        __error: {
          code: "SCOPE_EMPTY",
          message: "本次范围为空",
          next_step: "至少选择一条验收项",
        },
      },
    });
    await reachScope(wrapper);
    await wrapper.find('[data-testid="wizard-next"]').trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("本次范围里没有可执行的验收标准");
    expect(wrapper.text()).toContain("下一步：至少选择一条验收项");
    expect(wrapper.find('[data-testid="wizard-step-4"]').exists()).toBe(true);
  });
});
