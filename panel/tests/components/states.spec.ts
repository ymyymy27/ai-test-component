import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import LoadingState from "@/components/LoadingState.vue";

describe("state components", () => {
  it("explains why an empty state is empty and offers a next step", () => {
    const wrapper = mount(EmptyState, {
      props: { title: "还没有任务", text: "从概览新建任务后再开始。" },
      slots: { default: "<button>新建任务</button>" },
    });
    expect(wrapper.text()).toContain("还没有任务");
    expect(wrapper.text()).toContain("从概览新建任务");
    expect(wrapper.find("button").exists()).toBe(true);
    expect(wrapper.find("h2.empty-title").text()).toBe("还没有任务");
  });

  it("renders errors with role alert and retry", async () => {
    const wrapper = mount(ErrorState, { props: { title: "读取失败", text: "检查存储。" } });
    expect(wrapper.attributes("role")).toBe("alert");
    expect(wrapper.find("h2.error-title").text()).toBe("读取失败");
    await wrapper.find("button").trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);
  });

  it("announces loading with aria-busy", () => {
    const wrapper = mount(LoadingState, { props: { text: "正在读取…" } });
    expect(wrapper.attributes("role")).toBe("status");
    expect(wrapper.attributes("aria-busy")).toBe("true");
    expect(wrapper.text()).toContain("正在读取");
  });

  it("can render skeleton lines for long reads", () => {
    const wrapper = mount(LoadingState, { props: { text: "正在读取", lines: 3 } });
    expect(wrapper.findAll(".loading-line")).toHaveLength(3);
  });
});
