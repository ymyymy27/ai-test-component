import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CoverageRuler from "@/components/CoverageRuler.vue";

const items = [
  { itemId: "ai_1", no: "01", text: "启动并完成主流程" },
  { itemId: "ai_2", no: "02", text: "写入后可以查询" },
  { itemId: "ai_3", no: "03", text: "异常输入被拒绝" },
];

describe("CoverageRuler", () => {
  it("renders one tick per item with text labels and aria labels", () => {
    const wrapper = mount(CoverageRuler, {
      props: {
        items,
        selectedIds: ["ai_1", "ai_2"],
        outcomes: { ai_1: "passed", ai_2: "failed" },
      },
    });
    const ticks = wrapper.findAll(".tick");
    expect(ticks).toHaveLength(3);
    expect(ticks[0]?.classes()).toContain("is-passed");
    expect(ticks[1]?.classes()).toContain("is-failed");
    expect(ticks[2]?.classes()).toContain("is-unselected");
    expect(ticks[0]?.attributes("aria-label")).toContain("第 01 项");
    expect(ticks[2]?.attributes("aria-label")).toContain("未纳入");
  });

  it("distinguishes planned not-run from unselected", () => {
    const wrapper = mount(CoverageRuler, {
      props: {
        items,
        selectedIds: ["ai_1"],
        outcomes: {},
        planned: true,
      },
    });
    expect(wrapper.findAll(".tick")[0]?.classes()).toContain("is-not_run");
    expect(wrapper.findAll(".tick")[1]?.classes()).toContain("is-unselected");
    expect(wrapper.text()).toContain("待执行");
  });

  it("emits the selected item", async () => {
    const wrapper = mount(CoverageRuler, {
      props: { items, selectedIds: ["ai_1"], outcomes: {} },
    });
    await wrapper.findAll(".tick")[0]?.trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual(["ai_1"]);
  });

  it("keeps native button semantics and moves focus with arrow keys", async () => {
    const wrapper = mount(CoverageRuler, {
      props: {
        items,
        selectedIds: ["ai_1", "ai_2", "ai_3"],
        outcomes: { ai_1: "passed", ai_2: "failed", ai_3: "blocked" },
      },
      attachTo: document.body,
    });
    const ticks = wrapper.findAll(".tick");
    expect(ticks[0]?.element.tagName).toBe("BUTTON");
    expect(ticks[0]?.attributes("role")).toBeUndefined();
    expect(ticks[0]?.attributes("aria-label")).toContain("通过");
    expect(wrapper.find('[role="listitem"]').exists()).toBe(false);

    await ticks[0]?.trigger("keydown", { key: "ArrowRight" });
    expect(ticks[1]?.attributes("tabindex")).toBe("0");
    expect(document.activeElement).toBe(ticks[1]?.element);
    wrapper.unmount();
  });
});
