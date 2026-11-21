import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ResultSummary from "@/components/ResultSummary.vue";
import type { Run, Task } from "@/domain/types";

function run(overrides: Partial<Run> = {}): Run {
  return {
    id: "run_1",
    status: "completed",
    verdict: "incomplete",
    counts: { selected: 0, applicable: 0, covered: 0, passed: 0, failed: 0, incomplete: 0 },
    items: [],
    cases: [],
    l1: [],
    scope: { preset: "selected", moduleIds: [], acceptanceItemIds: [], coverage: { selected: 0, applicable: 0 } },
    conclusion: "",
    ...overrides,
  } as Run;
}

const task = {} as Task;

describe("ResultSummary", () => {
  it("does not invent a conclusion when no report core conclusion is available", () => {
    const wrapper = mount(ResultSummary, { props: { run: run(), task } });
    expect(wrapper.text()).toContain("结论未生成");
  });

  it("explains not_applicable instead of telling the user to rerun", () => {
    const wrapper = mount(ResultSummary, {
      props: { run: run({ verdict: "not_applicable" }), task },
    });
    expect(wrapper.text()).toContain("本次没有纳入验收项");
    expect(wrapper.text()).not.toContain("补齐检查条件后重跑");
  });

  it("uses execution_error wording and the report conclusion", () => {
    const wrapper = mount(ResultSummary, {
      props: {
        run: run({ status: "execution_error" }),
        task,
        conclusion: "运行执行出错，未形成结论。",
      },
    });
    expect(wrapper.text()).toContain("运行执行出错");
    expect(wrapper.text()).toContain("运行执行出错，未形成结论。");
  });
});
