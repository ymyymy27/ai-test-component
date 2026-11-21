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
      props: { link, busy: false, hostMode: false },
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

  it("keeps pasted JSON when import fails or awaits the parent save", async () => {
    const wrapper = mount(LinkPanel, {
      props: { link: null, busy: false, hostMode: false },
    });
    const textarea = wrapper.find("#link-json");
    await textarea.setValue('{"nodes":[]}');
    const button = wrapper
      .findAll("button")
      .find((entry) => entry.text().includes("导入并重新生成报告"));
    await button?.trigger("click");
    expect(wrapper.emitted("load")).toHaveLength(1);
    expect((wrapper.find("#link-json").element as HTMLTextAreaElement).value).toBe(
      '{"nodes":[]}',
    );
  });
});
