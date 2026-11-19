import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import GoalComposer from "@/components/GoalComposer.vue";

function mountComposer(value = "") {
  return mount(GoalComposer, {
    props: {
      modelValue: value,
      busy: false,
      analysisBusy: false,
      ready: true,
      catalogCount: 4,
      featureCount: 3,
    },
  });
}

describe("GoalComposer", () => {
  it("asks for a goal instead of doing nothing", async () => {
    const wrapper = mountComposer();
    await wrapper.get("button.btn-primary").trigger("click");
    expect(wrapper.text()).toContain("先写一句要验证的目标");
    expect(wrapper.emitted("start")).toBeUndefined();
  });

  it("emits the typed goal and supports Ctrl+Enter", async () => {
    const wrapper = mountComposer("验证删除标记后批次仍然可读");
    await wrapper.get("textarea").trigger("keydown", { key: "Enter", ctrlKey: true });
    expect(wrapper.emitted("start")?.[0]).toEqual(["验证删除标记后批次仍然可读"]);
  });

  it("exposes analyze and quick check without creating a task", async () => {
    const wrapper = mountComposer("验证主流程");
    const buttons = wrapper.findAll("button");
    await buttons[1]?.trigger("click");
    await buttons[2]?.trigger("click");
    expect(wrapper.emitted("analyze")).toHaveLength(1);
    expect(wrapper.emitted("quickCheck")).toHaveLength(1);
  });

  it("disables start while analysis is not ready", () => {
    const wrapper = mount(GoalComposer, {
      props: {
        modelValue: "验证主流程",
        busy: false,
        analysisBusy: true,
        ready: false,
        catalogCount: 0,
        featureCount: 0,
      },
    });
    const primary = wrapper.get("button.btn-primary");
    expect(primary.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("分析完成后才能创建");
  });
});
