import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import GoalComposer from "@/components/GoalComposer.vue";

function mountComposer(value = "", busy = false, canStart = true, blockedReason = "") {
  return mount(GoalComposer, {
    props: {
      modelValue: value,
      busy,
      canStart,
      blockedReason,
      catalogCount: 4,
      featureCount: 3,
    },
  });
}

describe("GoalComposer", () => {
  it("asks for a goal when submitting an empty value", async () => {
    const wrapper = mountComposer();
    await wrapper.get("textarea").trigger("keydown", { key: "Enter", ctrlKey: true });
    expect(wrapper.text()).toContain("先写一句要验证的目标");
    expect(wrapper.emitted("start")).toBeUndefined();
  });

  it("emits the typed goal and supports Ctrl+Enter", async () => {
    const wrapper = mountComposer("验证删除标记后批次仍然可读");
    await wrapper.get("textarea").trigger("keydown", { key: "Enter", ctrlKey: true });
    expect(wrapper.emitted("start")?.[0]).toEqual(["验证删除标记后批次仍然可读"]);
  });

  it("keeps the goal limit and a live counter", () => {
    const wrapper = mountComposer("验证主流程");
    expect(wrapper.get("textarea").attributes("maxlength")).toBe("200");
    expect(wrapper.text()).toContain("5 / 200");
    expect(wrapper.text()).toContain("创建任务并生成计划");
  });

  it("explains why submission is blocked", async () => {
    const wrapper = mountComposer("验证主流程", false, false, "正在分析项目，完成后才能创建任务。");
    await wrapper.get("textarea").trigger("keydown", { key: "Enter", ctrlKey: true });
    expect(wrapper.text()).toContain("正在分析项目");
    expect(wrapper.emitted("start")).toBeUndefined();
    expect(wrapper.get("button").attributes("disabled")).toBeDefined();
  });

  it("disables the input while busy", () => {
    const wrapper = mountComposer("验证主流程", true);
    expect(wrapper.get("textarea").attributes("disabled")).toBeDefined();
  });

  it("clears a stale error when a candidate fills the goal from outside", async () => {
    const wrapper = mountComposer();
    await wrapper.get("textarea").trigger("keydown", { key: "Enter", ctrlKey: true });
    expect(wrapper.text()).toContain("先写一句要验证的目标");
    await wrapper.setProps({ modelValue: "验证候选项的目标" });
    expect(wrapper.text()).not.toContain("先写一句要验证的目标");
    expect(wrapper.get("textarea").attributes("aria-invalid")).toBeUndefined();
  });
});
