import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import StatusMark from "@/components/StatusMark.vue";
import StatusText from "@/components/StatusText.vue";

describe("status components", () => {
  it("renders different shapes for pass, fail, block, wait and unselected", () => {
    const passed = mount(StatusMark, { props: { result: "passed" } });
    const failed = mount(StatusMark, { props: { result: "failed" } });
    const blocked = mount(StatusMark, { props: { result: "blocked" } });
    const waiting = mount(StatusMark, { props: { result: "not_run" } });
    const unselected = mount(StatusMark, { props: { result: "unselected" } });
    expect(passed.find("path").exists()).toBe(true);
    expect(failed.find("rect").exists()).toBe(true);
    expect(blocked.findAll("path").length).toBeGreaterThan(0);
    expect(waiting.find("circle").exists()).toBe(true);
    expect(unselected.find("path").exists()).toBe(true);
  });

  it("can expose a screen-reader label without duplicating visible text", () => {
    const wrapper = mount(StatusMark, { props: { result: "passed", label: "通过" } });
    expect(wrapper.attributes("aria-hidden")).toBeUndefined();
    expect(wrapper.find(".visually-hidden").text()).toBe("通过");
  });

  it("always pairs a shape with text", () => {
    expect(mount(StatusText, { props: { result: "passed" } }).text()).toBe("通过");
    expect(mount(StatusText, { props: { result: "failed" } }).text()).toBe("失败");
    expect(mount(StatusText, { props: { result: "blocked" } }).text()).toBe("阻塞");
    expect(mount(StatusText, { props: { result: "manual_pending" } }).text()).toBe("待人工");
    expect(mount(StatusText, { props: { result: "unselected" } }).text()).toBe("未纳入");
  });
});
