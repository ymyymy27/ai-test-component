import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ResultView from "@/views/ResultView.vue";
import { session } from "@/state/session";

import {
  PLAN,
  PROJECT,
  RUN_DETAIL,
  TASK,
  installHost,
  resetSession,
} from "./fixtures";

function prepare(detail = RUN_DETAIL) {
  installHost({
    "report.generate": { report_id: "rep_1", outcome: "failed", counts: {} },
    "report.export": { path: "E:\\reports\\run.md", bytes: 10 },
    "run.start": { run_id: "run_2", status: "running", scope: { selected: 1, applicable: 3 } },
  });
  resetSession();
  session.project = PROJECT;
  session.result = detail;
  session.plan = PLAN;
  session.task = TASK;
  session.activeRunId = detail.run.run_id;
  session.activePlanId = PLAN.plan_id;
  session.view = "result";
}

describe("结果页", () => {
  it("用一句话结论、色块与 X/Y 覆盖展示结果", async () => {
    prepare();
    const wrapper = mount(ResultView);
    await flushPromises();

    expect(wrapper.find('[data-testid="verdict-word"]').text()).toBe("失败");
    expect(wrapper.text()).toContain("所选范围内存在有效失败");
    expect(wrapper.find('[data-testid="coverage"]').text().replace(/\s+/g, "")).toContain("2/3");
    expect(wrapper.text()).toContain("还有 1 条验收项未纳入本次范围");
    expect(wrapper.text()).toContain("已完成");
  });

  it("失败用例展示标题、预期、证据与 AI 分析四段", async () => {
    prepare();
    const wrapper = mount(ResultView);
    await flushPromises();

    expect(wrapper.text()).toContain("失败用例（1）");
    expect(wrapper.text()).toContain("重复提交同一订单");

    await wrapper.find(".case-head").trigger("click");
    expect(wrapper.text()).toContain("订单列表只出现一条");
    expect(wrapper.text()).toContain("evidence/order-list.png");
    expect(wrapper.text()).toContain("AssertionError");

    expect(wrapper.text()).toContain("观察到的事实");
    expect(wrapper.text()).toContain("候选原因");
    expect(wrapper.text()).toContain("未验证与下一步");
    expect(wrapper.text()).toContain("生产环境是否同样");
  });

  it("缺少未验证范围的分析不展示正文", async () => {
    const detail = {
      ...RUN_DETAIL,
      run: {
        ...RUN_DETAIL.run,
        analysis: [{ status: "generated" as const, observed: "半截内容", unverified: [] }],
      },
    };
    prepare(detail);
    const wrapper = mount(ResultView);
    await flushPromises();

    expect(wrapper.text()).toContain("分析不合格，已隐藏");
    expect(wrapper.text()).not.toContain("半截内容");
  });

  it("按验收项重跑只提交该验收项并新建运行", async () => {
    const harness = installHost({
      "run.start": { run_id: "run_2", status: "running", scope: { selected: 1, applicable: 3 } },
    });
    resetSession();
    session.project = PROJECT;
    session.result = RUN_DETAIL;
    session.plan = PLAN;
    session.task = TASK;
    session.activeRunId = RUN_DETAIL.run.run_id;
    const wrapper = mount(ResultView);
    await flushPromises();

    const rerun = wrapper
      .findAll(".rerun button")
      .find((button) => button.text().includes("下单后订单列表出现该订单"));
    expect(rerun).toBeTruthy();
    await rerun?.trigger("click");
    await flushPromises();

    const start = harness.calls.find((call) => call.command === "run.start");
    expect(start?.options.confirm).toBe(true);
    expect(start?.payload["acceptance_item_ids"]).toEqual(["ai_1"]);
    expect(session.view).toBe("run");
  });
});

describe("未覆盖清单", () => {
  it("结果页展示未选中的适用验收项，最多 5 条并给出剩余数量", async () => {
    const detail = {
      ...RUN_DETAIL,
      uncovered_items: [
        "ai_2：库存扣减一次",
        "ai_4：取消后库存回补",
        "ai_5：订单状态为已支付",
        "ai_6：重复提交只生成一单",
        "ai_7：超时订单自动关闭",
        "ai_8：退款后订单不可发货",
      ],
    };
    prepare(detail);
    const wrapper = mount(ResultView);
    await flushPromises();

    const list = wrapper.find('[data-testid="uncovered-list"]');
    expect(list.exists()).toBe(true);
    const text = list.text();
    expect(text).toContain("未覆盖（6）");
    expect(text).toContain("ai_2：库存扣减一次");
    expect(text).toContain("ai_7：超时订单自动关闭");
    expect(text).not.toContain("ai_8：退款后订单不可发货");
    expect(text).toContain("还有 1 条未显示");
  });

  it("全部覆盖时不显示未覆盖清单", async () => {
    prepare({ ...RUN_DETAIL, uncovered_items: [] });
    const wrapper = mount(ResultView);
    await flushPromises();

    expect(wrapper.find('[data-testid="uncovered-list"]').exists()).toBe(false);
  });
});
