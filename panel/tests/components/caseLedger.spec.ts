import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CaseLedger from "@/components/CaseLedger.vue";
import type { Run, RunCase, RunItem } from "@/domain/types";

function runWith(outcome: RunItem["outcome"]): Run {
  const item: RunItem = {
    itemId: "item_1",
    no: "01",
    text: "验收项一",
    module: "",
    feature: "",
    outcome,
    caseIds: ["case_1"],
  };
  const testCase = {
    id: "case_1",
    caseId: "case_1",
    itemId: "item_1",
    caseItemIds: ["item_1"],
    title: "用例一",
    method: "pytest",
    layer: "L2",
    expected: "通过",
    expectationVerified: true,
    dependsOn: null,
    actual: "",
    result: "failed",
    evidence: [],
    message: "",
    errorType: "",
    stackKey: "",
    attempts: 1,
    manualSteps: [],
  } as RunCase;
  return {
    id: "run_1",
    projectId: "p",
    taskId: "t",
    planId: "plan_1",
    kind: "task",
    status: "completed",
    startedAt: "",
    finishedAt: "",
    elapsedMs: 0,
    confirmedBy: "",
    confirmationId: "",
    confirmationFingerprint: "",
    scope: {
      preset: "selected",
      moduleIds: [],
      acceptanceItemIds: ["item_1"],
      coverage: { selected: 1, applicable: 1 },
    },
    cases: [testCase],
    l1: [],
    items: [item],
    evidence: [],
    link: null,
    verdict: "passed",
    counts: {
      selected: 1,
      applicable: 1,
      covered: 1,
      passed: 1,
      failed: 0,
      incomplete: 0,
    },
    conclusion: "",
    reportIds: [],
    currentCaseId: "",
    simulated: false,
    heartbeatAt: "",
    cancelRequested: false,
  };
}

describe("CaseLedger", () => {
  it("uses the CLI item outcome instead of recomputing from case results", () => {
    const wrapper = mount(CaseLedger, { props: { run: runWith("passed"), items: runWith("passed").items } });
    expect(wrapper.find(".status-text").text()).toBe("通过");
  });

  it("keeps the filter on the same source as the counters", () => {
    const run = runWith("passed");
    const wrapper = mount(CaseLedger, {
      props: { run, items: run.items, filter: "problems" },
    });
    expect(wrapper.findAll(".ledger-item")).toHaveLength(0);
  });
});
