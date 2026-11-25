import { describe, expect, it } from "vitest";

import { buildCaseFixPrompt, buildIssueFixPrompt } from "@/domain/aiPrompt";
import { itemDiffTag } from "@/domain/derive";
import type { Issue, Run } from "@/domain/types";
import { completedRun } from "../fixtures";

function makeRun(id: string, outcomes: Record<string, "passed" | "failed" | "blocked">): Run {
  const cases = Object.entries(outcomes).map(([itemId, result], idx) => ({
    id: `c_${idx}`,
    caseId: `pc_${idx}`,
    itemId,
    title: `用例 ${itemId}`,
    method: "pytest",
    layer: "L2" as const,
    expected: "成功",
    expectationVerified: true,
    dependsOn: null,
    actual: result,
    result,
    evidence: [],
    message: "",
    errorType: result === "passed" ? "" : "AssertionError",
    stackKey: "",
    attempts: 1,
    manualSteps: [],
  }));
  const items = Object.entries(outcomes).map(([itemId, outcome], idx) => ({
    itemId,
    no: `0${idx + 1}`,
    text: `验收项 ${itemId}`,
    module: "",
    feature: "",
    outcome,
    caseIds: [`c_${idx}`],
  }));
  return {
    id,
    projectId: "p1",
    taskId: "t1",
    planId: "pl1",
    kind: "task",
    status: "completed",
    verdict: "failed",
    confirmedBy: "tester",
    confirmationId: "conf_1",
    confirmationFingerprint: "fp_1",
    startedAt: "2026-11-24T00:00:00Z",
    finishedAt: "2026-11-24T00:01:00Z",
    elapsedMs: 60000,
    heartbeatAt: "2026-11-24T00:01:00Z",
    environment: {},
    l1: [],
    cases,
    items,
    evidence: [],
    link: null,
    conclusion: "",
    reportIds: [],
    currentCaseId: "",
    cancelRequested: false,
    scope: {
      preset: "all_applicable",
      moduleIds: [],
      acceptanceItemIds: Object.keys(outcomes),
      coverage: { selected: items.length, applicable: items.length },
    },
    counts: {
      selected: items.length,
      applicable: items.length,
      covered: items.length,
      passed: 0,
      failed: 0,
      incomplete: 0,
    },
    simulated: false,
  };
}

describe("aiPrompt and stage4 helpers", () => {
  it("builds structured case fix context prompt", () => {
    const prompt = buildCaseFixPrompt({
      projectName: "订单系统",
      projectRoot: "D:/repo/orders",
      taskTitle: "验收退款流程",
      item: { no: "02", text: "重复退款必须返回 409" },
      testCase: {
        id: "case_dup_refund",
        title: "重复提交退款请求",
        method: "pytest -k test_dup_refund",
        expected: "第二次请求返回 HTTP 409",
        actual: "HTTP 200",
        message: "AssertionError: expected 409 got 200",
        errorType: "AssertionError",
        stackKey: "orders/service.py:88",
        testNodes: ["tests/test_refund.py::test_dup_refund"],
        evidence: [
          {
            id: "ev1",
            name: "refund.log",
            mediaType: "LOG",
            bytes: 120,
            sha256: "a".repeat(64),
            note: "",
            missing: false,
            truncated: false,
            addedAt: "",
            sourcePath: "runs/r1/evidence/refund.log",
          },
        ],
      },
    });
    expect(prompt).toContain("订单系统");
    expect(prompt).toContain("D:/repo/orders");
    expect(prompt).toContain("02 重复退款必须返回 409");
    expect(prompt).toContain("tests/test_refund.py::test_dup_refund");
    expect(prompt).toContain("orders/service.py:88");
    expect(prompt).toContain("runs/r1/evidence/refund.log");
  });

  it("builds structured issue fix context prompt", () => {
    const issue: Issue = {
      id: "iss_1",
      projectId: "p1",
      taskId: "t1",
      planId: "pl1",
      runId: "r1",
      itemId: "it1",
      itemNo: "03",
      itemText: "校验金额非负",
      caseId: "c1",
      signature: "it1::AssertionError::billing.py:42",
      title: "负数金额未拒绝",
      expected: "返回 422",
      actual: "写入成功",
      steps: ["POST /pay amount=-1"],
      severity: "P1",
      status: "pending",
      disposition: null,
      reason: "",
      fixVersion: "",
      source: "auto",
      occurrences: [],
      evidenceRefs: ["evidence/pay.log"],
      verificationRunId: "",
      unstable: false,
      createdAt: "",
      updatedAt: "",
    };
    const prompt = buildIssueFixPrompt({
      projectName: "账单服务",
      projectRoot: "D:/repo/billing",
      issue,
    });
    expect(prompt).toContain("[P1] 负数金额未拒绝");
    expect(prompt).toContain("POST /pay amount=-1");
    expect(prompt).toContain("it1::AssertionError::billing.py:42");
    expect(prompt).toContain("evidence/pay.log");
  });

  it("computes itemDiffTag across consecutive runs accurately", () => {
    const prev = makeRun("r1", { a: "passed", b: "failed", c: "failed", d: "passed" });
    const curr = makeRun("r2", { a: "failed", b: "passed", c: "failed", d: "passed" });

    expect(itemDiffTag(prev, curr, "a")).toBe("regressed");
    expect(itemDiffTag(prev, curr, "b")).toBe("fixed");
    expect(itemDiffTag(prev, curr, "c")).toBe("persisted");
    expect(itemDiffTag(prev, curr, "d")).toBeNull();
    expect(itemDiffTag(null, curr, "a")).toBeNull();
    expect(itemDiffTag(curr, curr, "a")).toBeNull();
  });

  it("starts targeted rerun reusing existing plan without regeneration", async () => {
    const seeded = await completedRun(["01", "03"]);
    const planCountBefore = seeded.task.plans.length;
    const plan = seeded.task.plans[0];
    expect(plan).toBeDefined();

    const item03 = seeded.task.acceptanceItems.find((entry) => entry.no === "03");
    expect(item03).toBeDefined();

    const nextRun = await seeded.repo.startTargetedRerun(
      seeded.projectId,
      seeded.task.id,
      plan!.id,
      [item03!.id],
      "测试用户",
    );
    expect(nextRun.planId).toBe(plan!.id);
    expect(nextRun.confirmedBy).toBe("测试用户");
    expect(nextRun.scope.acceptanceItemIds).toEqual([item03!.id]);
    expect(nextRun.scope.coverage.selected).toBe(1);
    const updated = await seeded.repo.getProject(seeded.projectId);
    const updatedTask = updated?.tasks.find((entry) => entry.id === seeded.task.id);
    expect(updatedTask?.plans.length).toBe(planCountBefore);
  });
});
