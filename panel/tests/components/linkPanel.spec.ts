import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import LinkPanel from "@/components/LinkPanel.vue";
import type { LinkData } from "@/domain/types";

const link: LinkData = {
  nodes: [
    { id: "node_1", name: "请求到达", state: "failed", detail: "500", evidenceRef: "" },
  ],
  assertions: [],
  gaps: [],
  location: { known: false, path: "", line: 0, reason: "无可定位节点" },
  unknown: "",
};

describe("LinkPanel", () => {
  it("does not prefill expectation from the current state", async () => {
    const wrapper = mount(LinkPanel, {
      props: { link, busy: false },
    });
    await wrapper.find("#link-node").setValue("node_1");
    const expected = wrapper.find("#link-expected").element as HTMLInputElement;
    const actual = wrapper.find("#link-actual").element as HTMLInputElement;
    expect(expected.value).toBe("");
    expect(actual.value).toBe("failed");
    const button = wrapper
      .findAll("button")
      .find((entry) => entry.text().includes("写入断言"));
    expect(button?.attributes("disabled")).toBeDefined();
  });

  it("does not offer manual JSON import", () => {
    const wrapper = mount(LinkPanel, {
      props: { link: null, busy: false },
    });
    expect(wrapper.find("#link-json").exists()).toBe(false);
    expect(wrapper.text()).toContain("没有自动生成调用链");
  });
});
