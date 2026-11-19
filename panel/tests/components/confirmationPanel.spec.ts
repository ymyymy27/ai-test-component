import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ConfirmationPanel from "@/components/ConfirmationPanel.vue";
import { currentPlan } from "@/domain/derive";
import { buildEnvironmentChecks } from "@/services/environmentService";
import { seedPlannedTask } from "../fixtures";

describe("ConfirmationPanel", () => {
  it("requires an explicit human checkbox before start", async () => {
    const seeded = await seedPlannedTask(["01"]);
    const plan = currentPlan(seeded.task);
    if (!plan) {
      throw new Error("plan missing");
    }
    const wrapper = mount(ConfirmationPanel, {
      props: {
        plan,
        task: seeded.task,
        project: seeded.project,
        environment: buildEnvironmentChecks(),
        checked: false,
        busy: false,
        startDisabled: false,
      },
    });
    expect(wrapper.text()).toContain("执行前确认");
    expect(wrapper.text()).toContain("依赖");
    expect(wrapper.text()).toContain("副作用");
    await wrapper.get("#confirm-execution").setValue(true);
    expect(wrapper.emitted("update:checked")?.[0]).toEqual([true]);
  });

  it("disables confirmation and explains a stale plan", () => {
    const seeded = seedPlannedTask(["01"]);
    return seeded.then((value) => {
      const plan = currentPlan(value.task);
      if (!plan) {
        throw new Error("plan missing");
      }
      const stale = { ...plan, stale: true, staleReason: "契约变化" };
      const wrapper = mount(ConfirmationPanel, {
        props: {
          plan: stale,
          task: value.task,
          project: value.project,
          environment: buildEnvironmentChecks(),
          checked: false,
          busy: false,
          startDisabled: true,
        },
      });
      expect(wrapper.text()).toContain("契约变化");
      expect(wrapper.get("#confirm-execution").attributes("disabled")).toBeDefined();
    });
  });
});
