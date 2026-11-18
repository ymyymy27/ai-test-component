import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";

import IssuesView from "@/views/IssuesView.vue";
import {
  ISSUES,
  PROJECT,
  RUN_SUMMARIES,
  installHost,
  resetWorkspace,
  workspaceRoutes,
} from "./fixtures";
import { workspace } from "@/state/workspace";

describe("问题台账", () => {
  beforeEach(() => {
    resetWorkspace();
    workspace.project = PROJECT;
    workspace.issues = ISSUES;
    workspace.history = RUN_SUMMARIES;
    installHost(workspaceRoutes());
  });

  it("列表显示发生次数、最近时间和疑似不稳定", async () => {
    const wrapper = mount(IssuesView);
    await flushPromises();
    const text = wrapper.text();
    expect(text).toContain("重复提交生成两条订单");
    expect(text).toContain("2 次发生");
    expect(text).toContain("待确认");
    expect(text).toContain("严重");
  });

  it("详情用检查项名称代替内部用例 ID", async () => {
    const wrapper = mount(IssuesView);
    await flushPromises();
    await wrapper.find(".issue-row").trigger("click");
    await flushPromises();
    const text = wrapper.text();
    expect(text).toContain("失败原因");
    expect(text).toContain("重复提交同一订单");
    expect(text).toContain("发生记录");
    expect(text).not.toContain("case_1");
  });

  it("待确认问题可以开始处理", async () => {
    const harness = installHost(workspaceRoutes());
    const wrapper = mount(IssuesView);
    await flushPromises();
    await wrapper.find(".issue-row").trigger("click");
    await flushPromises();
    const button = wrapper.findAll("button").find((item) => item.text() === "开始处理");
    await button!.trigger("click");
    await flushPromises();
    const call = harness.calls.find((item) => item.command === "issue.update");
    expect(call?.payload["status"]).toBe("in_progress");
  });
});
