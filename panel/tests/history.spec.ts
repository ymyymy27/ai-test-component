import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";

import HistoryView from "@/views/HistoryView.vue";
import { RUN_SUMMARIES, installHost, resetWorkspace, workspaceRoutes } from "./fixtures";
import { workspace } from "@/state/workspace";

describe("项目历史", () => {
  beforeEach(() => {
    resetWorkspace();
    workspace.history = RUN_SUMMARIES;
    installHost(workspaceRoutes());
  });

  it("显示任务、计划、结论与范围", () => {
    const wrapper = mount(HistoryView);
    const text = wrapper.text();
    expect(text).toContain("验证下单成功后订单只出现一次");
    expect(text).toContain("计划 1");
    expect(text).toContain("没通过");
    expect(text).toContain("本次 2 / 共 2 项");
    expect(text).toContain("无任务快照");
  });

  it("点开运行后进入只读历史", async () => {
    const wrapper = mount(HistoryView);
    await wrapper.find(".row").trigger("click");
    await flushPromises();
    expect(workspace.historical).toBe(true);
    expect(workspace.view).toBe("test");
  });
});
