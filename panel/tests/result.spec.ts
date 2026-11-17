import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ResultSummary from "@/components/ResultSummary.vue";
import ResultPanel from "@/components/ResultPanel.vue";
import { session } from "@/state/session";

import {
  ISSUES,
  PLAN,
  PROJECT,
  RUN_DETAIL,
  TASK,
  TEMPLATE_RESULT,
  installHost,
  resetSession,
  sessionRoutes,
  type Route,
} from "./fixtures";

async function settle(times = 3): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await flushPromises();
  }
}

function prepare(detail = RUN_DETAIL, overrides: Record<string, Route> = {}) {
  const harness = installHost(sessionRoutes(overrides));
  resetSession();
  session.project = PROJECT;
  session.phase = "result";
  session.result = detail;
  session.plan = PLAN;
  session.task = TASK;
  session.issues = ISSUES;
  session.activeRunId = detail.run.run_id;
  session.activePlanId = PLAN.plan_id;
  session.activeTaskId = TASK.task_id;
  return harness;
}

describe("结果相位：结论与失败用例", () => {
  it("用一句话结论、色块与 X/Y 覆盖展示结果", async () => {
    prepare();
    const wrapper = mount(ResultPanel);
    await settle();

    expect(wrapper.find('[data-testid="verdict-word"]').text()).toBe("失败");
    expect(wrapper.text()).toContain("所选范围内存在有效失败");
    expect(wrapper.find('[data-testid="coverage"]').text().replace(/\s+/g, "")).toContain("2/3");
    expect(wrapper.text()).toContain("还有 1 条验收项未纳入本次范围");
    expect(wrapper.text()).toContain("已完成");
  });

  it("全部通过的运行不再显示“分析未生成”", async () => {
    const passed = {
      outcome: "passed" as const,
      run: {
        ...RUN_DETAIL.run,
        status: "completed" as const,
        analysis: [],
        cases: RUN_DETAIL.run.cases.map((item) => ({ ...item, result: "passed" as const })),
      },
      uncovered_items: [],
    };
    prepare(passed);
    const wrapper = mount(ResultPanel);
    await settle();

    expect(wrapper.text()).toContain("本次没有失败，不需要生成分析。");
    expect(wrapper.text()).not.toContain("分析未生成。");
  });

  it("失败用例内联证据缩略卡与执行输出，AI 分析紧随其后", async () => {
    prepare();
    const wrapper = mount(ResultPanel);
    await settle();

    const card = wrapper.find('[data-testid="failed-case"]');
    expect(card.text()).toContain("重复提交同一订单");
    expect(card.text()).toContain("订单列表只出现一条");
    expect(card.text()).toContain("AssertionError");

    const tiles = card.findAll(".tile");
    expect(tiles).toHaveLength(1);
    expect(tiles[0].text()).toContain("order-list.png");
    expect(tiles[0].text()).toContain("2.0 KB");

    expect(wrapper.text()).toContain("观察到的事实");
    expect(wrapper.text()).toContain("候选原因");
    expect(wrapper.text()).toContain("生产环境是否同样");
  });

  it("点内联证据会用宿主打开已保存的文件", async () => {
    const harness = prepare();
    const wrapper = mount(ResultPanel);
    await settle();

    await wrapper.find('[data-testid="failed-case"] .tile').trigger("click");
    await flushPromises();

    expect(harness.opened).toEqual(["C:/data/ws/evidence/run_1/order-list.png"]);
    expect(wrapper.text()).toContain("已请求用系统程序打开");
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
    const wrapper = mount(ResultPanel);
    await settle();

    expect(wrapper.text()).toContain("分析不合格，已隐藏");
    expect(wrapper.text()).not.toContain("半截内容");
  });
});

describe("结果相位：内联问题", () => {
  it("问题标题是主信息，更新状态调用 issue.update", async () => {
    const harness = prepare();
    const wrapper = mount(ResultPanel);
    await settle();

    const block = wrapper.find('[data-testid="issue-inline"]');
    expect(block.text()).toContain("重复提交生成两条订单");
    expect(block.text()).not.toContain("issue_abc123");

    await block.find(".status-label select").setValue("in_progress");
    await flushPromises();

    const update = harness.calls.find((call) => call.command === "issue.update");
    expect(update?.payload["issue_id"]).toBe("issue_abc123");
    expect(update?.payload["status"]).toBe("in_progress");
    expect(block.text()).toContain("状态已更新为“处理中”");
  });

  it("关闭问题引用当前运行作为证据", async () => {
    const harness = prepare();
    const wrapper = mount(ResultPanel);
    await settle();

    await wrapper
      .findAll("button")
      .find((button) => button.text() === "关闭问题")
      ?.trigger("click");
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
    prepare(running);
    const wrapper = mount(ResultPanel);
    await settle();

    const close = wrapper.findAll("button").find((button) => button.text() === "关闭问题");
    expect(close?.attributes("disabled")).toBeDefined();
  });
});

describe("结果相位：覆盖、链路与模板", () => {
  it("展示未选中的适用验收项，最多 5 条并给出剩余数量", async () => {
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
    const wrapper = mount(ResultPanel);
    await settle();

    const list = wrapper.find('[data-testid="uncovered-list"]');
    expect(list.exists()).toBe(true);
    const text = list.text();
    expect(text).toContain("未覆盖（6）");
    expect(text).toContain("ai_2：库存扣减一次");
    expect(text).toContain("ai_7：超时订单自动关闭");
    expect(text).not.toContain("ai_8：退款后订单不可发货");
    expect(text).toContain("还有 1 条未显示");
  });

  it("链路折叠块展示首错节点、上下游与代码位置", async () => {
    prepare({
      ...RUN_DETAIL,
      link: {
        first_anomaly: { node_id: "log:12", status: "error" },
        upstream: [{ node_id: "log:1", status: "passed" }],
        downstream: [{ node_id: "log:14", status: "unknown" }],
        location: { known: true, path: "app.log", line: 12 },
      },
    });
    const wrapper = mount(ResultPanel);
    await settle();

    const block = wrapper.find('[data-testid="link-block"]');
    expect(block.text()).toContain("首错节点");
    expect(block.text()).toContain("log:12");
    expect(block.text()).toContain("log:1");
    expect(block.text()).toContain("log:14");
    expect(block.text()).toContain("app.log:12");
  });

  it("计划里的模板处置只读展示，不进入必测分母", async () => {
    prepare();
    session.plan = { ...PLAN, templates: [TEMPLATE_RESULT] };
    const wrapper = mount(ResultPanel);
    await settle();

    const block = wrapper.find('[data-testid="template-block"]');
    expect(block.text()).toContain("模板检查项");
    expect(block.text()).toContain("服务能启动");
    expect(block.text()).toContain("适用");
    expect(block.text()).toContain("状态可核对");
    expect(block.text()).toContain("未知");
    expect(block.text()).toContain("不进入必测分母");
  });
});

describe("结果相位：报告与意见", () => {
  it("导出报告先生成再按路径保存", async () => {
    const harness = prepare();
    const wrapper = mount(ResultPanel);
    await settle();

    await wrapper.findAll("button").find((button) => button.text() === "导出报告")?.trigger("click");
    const input = wrapper.find("input#export-path");
    expect(input.exists()).toBe(true);
    await input.setValue("E:\\reports\\run.md");
    await wrapper.findAll("button").find((button) => button.text() === "导出")?.trigger("click");
    await settle();

    const generate = harness.calls.find((call) => call.command === "report.generate");
    expect(generate?.payload).toMatchObject({ run_id: RUN_DETAIL.run.run_id });
    const exported = harness.calls.find((call) => call.command === "report.export");
    expect(exported?.payload).toMatchObject({ path: "E:\\reports\\run.md" });
    expect(wrapper.text()).toContain("已导出到");
  });

  it("保存验收意见并显示回执", async () => {
    const harness = prepare();
    const wrapper = mount(ResultPanel);
    await settle();

    await wrapper.find("input#opinion-by").setValue("李四");
    await wrapper.find("textarea#opinion-text").setValue("复测通过");
    await wrapper.findAll("button").find((button) => button.text() === "保存意见")?.trigger("click");
    await settle();

    const opinion = harness.calls.find((call) => call.command === "report.opinion");
    expect(opinion?.payload).toMatchObject({ by: "李四", text: "复测通过" });
    expect(wrapper.text()).toContain("已记录");
  });
});

describe("项目级冒烟检查", () => {
  const base = {
    verdict: "passed" as const,
    selected: 0,
    applicable: 0,
    projectMode: true,
    projectPassed: 3,
    projectTotal: 3,
    caseTotal: 3,
    casePassed: 3,
    caseFailed: 0,
    caseIncomplete: 0,
    caseManual: 0,
    pendingItems: 0,
    assumptions: 0,
    ticks: [
      { tone: "passed" as const, label: "检查 1" },
      { tone: "passed" as const, label: "检查 2" },
      { tone: "passed" as const, label: "检查 3" },
    ],
  };

  it("没有验收项时展示检查通过数而不是 0/0 覆盖", () => {
    const wrapper = mount(ResultSummary, { props: base });
    expect(wrapper.text()).toContain("项目检查");
    expect(wrapper.text().replace(/\s+/g, "")).toContain("3/3");
    expect(wrapper.text()).toContain("项目检查 3 / 3 通过");
  });

  it("有失败检查时按通过数展示进度", () => {
    const wrapper = mount(ResultSummary, {
      props: {
        ...base,
        verdict: "failed",
        projectPassed: 2,
        casePassed: 2,
        caseFailed: 1,
        ticks: [
          { tone: "passed", label: "检查 1" },
          { tone: "passed", label: "检查 2" },
          { tone: "failed", label: "检查 3" },
        ],
      },
    });
    expect(wrapper.text().replace(/\s+/g, "")).toContain("2/3");
    expect(wrapper.text()).toContain("项目检查 2 / 3 通过");
  });
});
