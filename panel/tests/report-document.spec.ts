import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";

import ReportDocument from "@/views/ReportDocument.vue";
import {
  PROJECT,
  REPORT,
  RUN_DETAIL,
  installHost,
  resetWorkspace,
  workspaceRoutes,
} from "./fixtures";
import { workspace } from "@/state/workspace";

describe("报告文档", () => {
  beforeEach(() => {
    resetWorkspace();
    workspace.project = PROJECT;
    workspace.run = RUN_DETAIL;
    workspace.report = REPORT;
    workspace.reports = [
      {
        report_id: "rep_1",
        run_id: "run_1",
        generated_at: REPORT.generated_at ?? "",
        outcome: "failed",
        revision: 1,
        has_opinion: false,
      },
    ];
  });

  it("展示真实报告结论与验收项", () => {
    installHost(workspaceRoutes());
    const wrapper = mount(ReportDocument);
    const text = wrapper.text();
    expect(text).toContain("没通过");
    expect(text).toContain("下单后订单列表出现该订单");
    expect(text).toContain("验收意见");
  });

  it("补充链路断言调用 link.assert", async () => {
    workspace.run = {
      ...RUN_DETAIL,
      link: {
        first_anomaly: { node_id: "log:2", type: "log", status: "failed" },
        upstream: [],
        downstream: [],
        location: { known: false, reason: "没有源码位置" },
      },
    };
    const harness = installHost(workspaceRoutes());
    const wrapper = mount(ReportDocument);
    await flushPromises();
    const byPlaceholder = (value: string) =>
      wrapper.findAll("input").find((item) => item.attributes("placeholder") === value)!;
    await byPlaceholder("log:2").setValue("log:2");
    await byPlaceholder("按场景填写预期值").setValue("order_1");
    await byPlaceholder("填写从链路观察到的值").setValue("order_1");
    await wrapper.findAll("button").find((item) => item.text() === "写入断言")!.trigger("click");
    await flushPromises();
    const call = harness.calls.find((item) => item.command === "link.assert");
    expect(call?.payload["node_id"]).toBe("log:2");
    expect(call?.payload["expected"]).toBe("order_1");
    expect(call?.payload["actual"]).toBe("order_1");
    expect(call?.payload["operator"]).toBe("equals");
  });

  it("导出报告使用当前报告和路径", async () => {
    const harness = installHost(workspaceRoutes());
    const wrapper = mount(ReportDocument);
    await flushPromises();
    const input = wrapper
      .findAll("input")
      .find((item) => (item.attributes("placeholder") ?? "").includes("reports"));
    expect(input).toBeTruthy();
    await input!.setValue("E:/reports/run.md");
    const button = wrapper.findAll("button").find((item) => item.text() === "导出报告");
    await button!.trigger("click");
    await flushPromises();
    const call = harness.calls.find((item) => item.command === "report.export");
    expect(call?.payload["report_id"]).toBe("rep_1");
    expect(call?.payload["path"]).toBe("E:/reports/run.md");
  });
});
