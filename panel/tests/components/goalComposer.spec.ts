import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import GoalComposer from "@/components/GoalComposer.vue";

function mountComposer(value = "", busy = false) {
  return mount(GoalComposer, {
    props: {
      modelValue: value,
      busy,
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

  it("keeps only the input on the composer", () => {
    const wrapper = mountComposer("验证主流程");
    expect(wrapper.findAll("button")).toHaveLength(0);
    expect(wrapper.text()).toContain("开始测试");
  });

  it("disables the input while busy", () => {
    const wrapper = mountComposer("验证主流程", true);
    expect(wrapper.get("textarea").attributes("disabled")).toBeDefined();
  });
});
