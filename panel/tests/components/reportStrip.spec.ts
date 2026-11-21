import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ReportStrip from "@/components/ReportStrip.vue";
import type { Report } from "@/domain/types";

const report = {
  id: "rep_1",
  version: 2,
  generatedAt: "2026-11-21T10:00:00+08:00",
  opinion: null,
} as Report;

describe("ReportStrip", () => {
  it("disables report actions while an export or regenerate is busy", () => {
    const wrapper = mount(ReportStrip, {
      props: {
        report,
        versionCount: 1,
        runStatus: "completed",
        postprocess: "done",
        busy: true,
      },
    });
    const buttons = wrapper.findAll("button");
    expect(buttons).toHaveLength(2);
    buttons.forEach((button) => {
      expect(button.attributes("disabled")).toBeDefined();
      expect(button.attributes("aria-busy")).toBe("true");
    });
  });

  it("does not promise a report for an execution error", () => {
    const wrapper = mount(ReportStrip, {
      props: {
        report: null,
        versionCount: 0,
        runStatus: "execution_error",
        postprocess: "error",
      },
    });
    expect(wrapper.text()).toContain("不会生成报告");
    expect(wrapper.text()).not.toContain("运行结束后会生成报告");
  });
});
