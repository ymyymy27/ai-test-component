import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import EvidenceView from "@/views/EvidenceView.vue";
import IssuesView from "@/views/IssuesView.vue";
import { session } from "@/state/session";

import {
  PROJECT,
  RUN_DETAIL,
  installHost,
  resetSession,
} from "./fixtures";

describe("证据页", () => {
  it("没有运行时给出空态，不伪造证据", async () => {
    installHost({});
    resetSession();
    session.project = PROJECT;
    const wrapper = mount(EvidenceView);
    await flushPromises();
    expect(wrapper.text()).toContain("还没有运行记录");
  });

  it("列出文件名、大小与缺失状态，并可打开与复制引用", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const harness = installHost({
      "evidence.list": {
        run_id: RUN_DETAIL.run.run_id,
        evidence: [
          {
            ref: "evidence/order-list.png",
            name: "order-list.png",
            path: "C:/data/ws/evidence/run_1/order-list.png",
            bytes: 2048,
            exists: true,
          },
          {
            ref: "evidence/missing.log",
            name: "missing.log",
            path: "",
            bytes: 0,
            exists: false,
            missing_reason: "证据文件不在运行目录中",
          },
        ],
      },
    });
    resetSession();
    session.project = PROJECT;
    session.result = RUN_DETAIL;
    session.activeRunId = RUN_DETAIL.run.run_id;

    const wrapper = mount(EvidenceView);
    await flushPromises();

    expect(wrapper.text()).toContain("order-list.png");
    expect(wrapper.text()).toContain("2.0 KB");
    expect(wrapper.text()).toContain("证据文件不在运行目录中");
    expect(wrapper.text()).toContain("缺失");

    const firstRow = wrapper.findAll(".evidence-row")[0];
    await firstRow.findAll("button").find((button) => button.text() === "打开")?.trigger("click");
    await flushPromises();
    expect(harness.opened).toEqual(["C:/data/ws/evidence/run_1/order-list.png"]);

    await firstRow.findAll("button").find((button) => button.text() === "复制引用")?.trigger("click");
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith("evidence/order-list.png");
    expect(wrapper.text()).toContain("已复制引用");
  });

  it("运行存在但没有证据时明确说明", async () => {
    installHost({ "evidence.list": { evidence: [] } });
    resetSession();
    session.project = PROJECT;
    session.result = RUN_DETAIL;
    session.activeRunId = RUN_DETAIL.run.run_id;
    const wrapper = mount(EvidenceView);
    await flushPromises();
    expect(wrapper.text()).toContain("这次运行没有证据文件");
  });
});

describe("问题处置页", () => {
  const ISSUE = {
    issue_id: "issue_abc123",
    title: "重复提交生成两条订单",
    status: "pending" as const,
    severity: "P1" as const,
    occurrences: 2,
  };

  it("没有问题时给出空态", async () => {
    installHost({ "issue.list": { issues: [] } });
    resetSession();
    session.project = PROJECT;
    session.result = RUN_DETAIL;
    const wrapper = mount(IssuesView);
    await flushPromises();
    expect(wrapper.text()).toContain("还没有登记问题");
  });

  it("问题标题是主信息，ID 只作次要信息", async () => {
    installHost({ "issue.list": { issues: [ISSUE] } });
    resetSession();
    session.project = PROJECT;
    session.result = RUN_DETAIL;
    const wrapper = mount(IssuesView);
    await flushPromises();
    expect(wrapper.find(".title").text()).toBe(ISSUE.title);
    expect(wrapper.find(".title").text()).not.toContain(ISSUE.issue_id);
    expect(wrapper.find(".meta").text()).toContain(ISSUE.issue_id);
  });

  it("更新状态调用 issue.update 并刷新本地状态", async () => {
    const harness = installHost({
      "issue.list": { issues: [ISSUE] },
      "issue.update": { issue_id: ISSUE.issue_id, status: "in_progress", revision: 2 },
    });
    resetSession();
    session.project = PROJECT;
    session.result = RUN_DETAIL;
    const wrapper = mount(IssuesView);
    await flushPromises();

    await wrapper.find(".status-label select").setValue("in_progress");
    await flushPromises();

    const update = harness.calls.find((call) => call.command === "issue.update");
    expect(update?.payload["issue_id"]).toBe(ISSUE.issue_id);
    expect(update?.payload["status"]).toBe("in_progress");
    expect(wrapper.text()).toContain("状态已更新为“处理中”");
  });

  it("关闭问题引用当前运行作为证据", async () => {
    const harness = installHost({
      "issue.list": { issues: [ISSUE] },
      "issue.close": { issue_id: ISSUE.issue_id, status: "closed" },
    });
    resetSession();
    session.project = PROJECT;
    session.result = RUN_DETAIL;
    session.activeRunId = RUN_DETAIL.run.run_id;
    const wrapper = mount(IssuesView);
    await flushPromises();

    await wrapper.findAll("button").find((button) => button.text() === "关闭问题")?.trigger("click");
    await flushPromises();

    const close = harness.calls.find((call) => call.command === "issue.close");
    expect(close?.payload["run_id"]).toBe(RUN_DETAIL.run.run_id);
    expect(wrapper.text()).toContain("问题已关闭");
  });

  it("运行中不能关闭问题", async () => {
    const running = {
      outcome: null,
      run: { ...RUN_DETAIL.run, status: "running" as const },
    };
    const harness = installHost({ "issue.list": { issues: [ISSUE] } });
    resetSession();
    session.project = PROJECT;
    session.result = running;
    const wrapper = mount(IssuesView);
    await flushPromises();

    const close = wrapper.findAll("button").find((button) => button.text() === "关闭问题");
    expect(close?.attributes("disabled")).toBeDefined();
    await close?.trigger("click");
    await flushPromises();
    expect(harness.calls.some((call) => call.command === "issue.close")).toBe(false);
  });
});
