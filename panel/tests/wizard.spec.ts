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

async function answerGoal(wrapper: VueWrapper, text = "验证下单流程") {
  await wrapper.find(".writer-link").trigger("click");
  await wrapper.find("#goal-input").setValue(text);
  await wrapper.find('[data-testid="wizard-next"]').trigger("click");
  await flushPromises();
}

async function reachScope(wrapper: VueWrapper) {
  await answerGoal(wrapper);
  await wrapper.find('[data-testid="wizard-next"]').trigger("click");
  await flushPromises();
}

describe("新建测试向导", () => {
  it("第一步只有候选卡片，不出现常驻输入框", async () => {
    const { wrapper } = await mountWizard();
    expect(wrapper.find('[data-testid="wizard-step-1"]').exists()).toBe(true);

    expect(wrapper.findAll('[data-testid="candidate"]')).toHaveLength(2);
    expect(wrapper.findAll('input[type="text"]')).toHaveLength(0);
    expect(wrapper.findAll("textarea")).toHaveLength(0);
    expect(wrapper.find(".writer-link").text()).toContain("或自己写一句");
  });

  it("点候选卡片即选中并直接进入下一步", async () => {
    const { wrapper, harness } = await mountWizard();
    await wrapper.findAll('[data-testid="candidate"]')[0].trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="wizard-step-2"]').exists()).toBe(true);
    const addCall = harness.calls.find((call) => call.command === "task.add");
    expect(addCall?.payload).toMatchObject({
      project_id: PROJECT.project_id,
      title: "验证下单成功后订单只出现一次",
      requirement: "验证下单成功后订单只出现一次",
    });
  });

  it("点“自己写一句”后只出现一个输入框，空目标不能继续", async () => {
    const { wrapper } = await mountWizard();
    expect(wrapper.find('[data-testid="wizard-next"]').exists()).toBe(false);

    await wrapper.find(".writer-link").trigger("click");
    expect(wrapper.findAll('input[type="text"]')).toHaveLength(1);
    expect(wrapper.findAll("textarea")).toHaveLength(0);
    expect(wrapper.find('[data-testid="wizard-next"]').attributes("disabled")).toBeDefined();

    await wrapper.find("#goal-input").setValue("验证下单流程");
    expect(wrapper.find('[data-testid="wizard-next"]').attributes("disabled")).toBeUndefined();
  });

  it("第二步是开关列表，默认全部启用，补一条才出现输入框", async () => {
    const { wrapper } = await mountWizard();
    await answerGoal(wrapper);

    expect(wrapper.find('[data-testid="wizard-step-2"]').exists()).toBe(true);
    const rows = wrapper.findAll('[data-testid="acceptance-list"] li');
    expect(rows).toHaveLength(3);
    expect(rows[2].classes()).toContain("off");
    expect(rows[2].text()).toContain("不可观察");

    // 正常路径没有常驻文本框。
    expect(wrapper.findAll('[data-testid="acceptance-list"] input[type="text"]')).toHaveLength(0);
    expect(wrapper.findAll(".add-input")).toHaveLength(0);

    const firstSwitch = rows[0].find('[role="switch"]');
    expect(firstSwitch.attributes("aria-checked")).toBe("true");
    await firstSwitch.trigger("click");
    expect(firstSwitch.attributes("aria-checked")).toBe("false");
    expect(rows[0].text()).toContain("订单列表出现该订单");

    const disabledSwitch = rows[2].find('[role="switch"]');
    expect(disabledSwitch.attributes("disabled")).toBeDefined();

    await wrapper.find(".add-link").trigger("click");
    expect(wrapper.findAll(".add-input")).toHaveLength(1);
    await wrapper.find(".add-input").setValue("库存只扣减一次");
    await wrapper.find(".add-item").trigger("submit");
    expect(wrapper.findAll('[data-testid="acceptance-list"] li')).toHaveLength(4);
    expect(wrapper.findAll(".add-input")).toHaveLength(0);
  });

  it("第三步默认全部适用且不显示勾选列表，自选后才展开", async () => {
    const { wrapper } = await mountWizard();
    await reachScope(wrapper);

    expect(wrapper.find('[data-testid="wizard-step-3"]').exists()).toBe(true);
    const count = wrapper.find('[data-testid="range-count"]');
    expect(count.text().replace(/\s+/g, "")).toContain("已选2/适用2条");
    expect(wrapper.find('[data-testid="scope-list"]').exists()).toBe(false);
    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(0);

    const presetButtons = wrapper.findAll(".segmented button");
    await presetButtons[1].trigger("click");
    expect(wrapper.find('[data-testid="scope-list"]').exists()).toBe(true);

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

    // 明细默认折叠，但内容仍可展开查看。
    expect(wrapper.find('[data-testid="case-details"]').exists()).toBe(true);
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
    const { wrapper } = await mountWizard({
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
    await wrapper.find(".add-link").trigger("click");
    expect(wrapper.find(".add-input").exists()).toBe(true);
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

describe("确认页未覆盖清单", () => {
  it("自选范围只勾一部分时，确认页列出未勾选的适用验收项", async () => {
    const { wrapper } = await mountWizard();
    await reachScope(wrapper);

    const presetButtons = wrapper.findAll(".segmented button");
    await presetButtons[1].trigger("click");
    const checkboxes = wrapper.findAll('[data-testid="scope-list"] input[type="checkbox"]');
    await checkboxes[1].setValue(false);
    await wrapper.find('[data-testid="wizard-next"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="wizard-step-4"]').exists()).toBe(true);
    const list = wrapper.find('[data-testid="uncovered-list"]');
    expect(list.exists()).toBe(true);
    expect(list.text()).toContain("未覆盖（1）");
    expect(list.text()).toContain("ai_2：库存扣减一次");
  });
});
