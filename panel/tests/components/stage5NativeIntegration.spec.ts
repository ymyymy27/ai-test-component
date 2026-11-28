import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CaseDetail from "@/components/CaseDetail.vue";
import CaseLedger from "@/components/CaseLedger.vue";
import PlanLedger from "@/components/PlanLedger.vue";
import type { Issue, Plan, Run, RunCase, RunItem, Task } from "@/domain/types";

function makeRunCase(overrides: Partial<RunCase> = {}): RunCase {
  return {
    id: "case_1",
    caseId: "case_1",
    itemId: "item_1",
    caseItemIds: ["item_1"],
    title: "测试订单创建",
    method: "script:check.py",
    layer: "L2",
    expected: "返回 201",
    expectationVerified: true,
    dependsOn: null,
    actual: "AssertionError",
    result: "failed",
    evidence: [],
    message: "失败",
    errorType: "",
    stackKey: "",
    attempts: 1,
    manualSteps: [],
    ...overrides,
  };
}

describe("第 5 步原生融合组件回归", () => {
  it("CaseLedger 置顶 confirmed 缺陷并支持 regressed/fixed 增量筛选", () => {
    const items: RunItem[] = [
      { itemId: "item_blk", no: "01", text: "环境阻塞项", module: "", feature: "", outcome: "blocked", caseIds: ["c_blk"] },
      { itemId: "item_fix", no: "02", text: "已修复项", module: "", feature: "", outcome: "passed", caseIds: ["c_fix"] },
      { itemId: "item_conf", no: "03", text: "真缺陷项", module: "", feature: "", outcome: "failed", caseIds: ["c_conf"] },
    ];
    const baseRun: Run = {
      id: "run_prev",
      projectId: "p1",
      taskId: "t1",
      planId: "pl1",
      kind: "task",
      status: "completed",
      startedAt: "2026-11-26T00:00:00Z",
      finishedAt: "2026-11-26T00:01:00Z",
      elapsedMs: 1000,
      confirmedBy: "u",
      confirmationId: "",
      confirmationFingerprint: "",
      scope: { preset: "all_applicable", moduleIds: [], acceptanceItemIds: ["item_blk", "item_fix", "item_conf"], coverage: { selected: 3, applicable: 3 } },
      cases: [
        makeRunCase({ id: "c_blk", caseId: "c_blk", itemId: "item_blk", caseItemIds: ["item_blk"], result: "blocked", confidence: "blocked" }),
        makeRunCase({ id: "c_fix", caseId: "c_fix", itemId: "item_fix", caseItemIds: ["item_fix"], result: "failed" }),
        makeRunCase({ id: "c_conf", caseId: "c_conf", itemId: "item_conf", caseItemIds: ["item_conf"], result: "passed" }),
      ],
      l1: [],
      items: [
        { ...items[0], outcome: "blocked" },
        { ...items[1], outcome: "failed" },
        { ...items[2], outcome: "passed" },
      ],
      evidence: [],
      link: null,
      verdict: "failed",
      counts: { selected: 3, applicable: 3, covered: 3, passed: 1, failed: 1, incomplete: 1 },
      conclusion: "",
      reportIds: [],
      currentCaseId: "",
      simulated: false,
      heartbeatAt: "",
      cancelRequested: false,
    };
    const currRun: Run = {
      ...baseRun,
      id: "run_curr",
      cases: [
        makeRunCase({ id: "c_blk", caseId: "c_blk", itemId: "item_blk", caseItemIds: ["item_blk"], result: "blocked", confidence: "blocked" }),
        makeRunCase({ id: "c_fix", caseId: "c_fix", itemId: "item_fix", caseItemIds: ["item_fix"], result: "passed" }),
        makeRunCase({ id: "c_conf", caseId: "c_conf", itemId: "item_conf", caseItemIds: ["item_conf"], result: "failed", confidence: "confirmed" }),
      ],
      items,
    };

    const wrapperAll = mount(CaseLedger, {
      props: { run: currRun, previousRun: baseRun, items, filter: "all" },
    });
    const texts = wrapperAll.findAll(".ledger-text").map((n) => n.text());
    // confirmed 真缺陷置顶，blocked 隔离在最底
    expect(texts[0]).toBe("真缺陷项");
    expect(texts[texts.length - 1]).toBe("环境阻塞项");

    const wrapperReg = mount(CaseLedger, {
      props: { run: currRun, previousRun: baseRun, items, filter: "regressed" },
    });
    expect(wrapperReg.findAll(".ledger-text").map((n) => n.text())).toEqual(["真缺陷项"]);

    const wrapperFix = mount(CaseLedger, {
      props: { run: currRun, previousRun: baseRun, items, filter: "fixed" },
    });
    expect(wrapperFix.findAll(".ledger-text").map((n) => n.text())).toEqual(["已修复项"]);
  });

  it("CaseDetail 展示运行期修复 diff，通过行提供关闭关联历史问题一行确认框", async () => {
    const failedCase = makeRunCase({
      scriptRepair: {
        status: "applied",
        reason: "导入路径修正",
        unifiedDiff: "--- a/check.py\n+++ b/check.py\n-val = 1\n+val = 2",
        guardBlocked: [],
        scriptName: "check.py",
        at: "2026-11-26T00:00:00Z",
      },
    });
    const wFail = mount(CaseDetail, {
      props: { testCase: failedCase, result: "failed", allowScriptRepair: true },
    });
    expect(wFail.text()).toContain("运行期脚本自动修复记录");
    expect(wFail.text()).toContain("预览 AI 修复");

    const openIssue: Issue = {
      id: "iss_1",
      projectId: "p1",
      taskId: "t1",
      planId: "pl1",
      runId: "run_old",
      itemId: "item_1",
      caseId: "case_1",
      signature: "sig",
      title: "历史失败问题",
      expected: "201",
      actual: "500",
      steps: [],
      severity: "P1",
      status: "pending",
      disposition: null,
      reason: "",
      fixVersion: "",
      source: "auto",
      occurrences: [],
      evidenceRefs: [],
      verificationRunId: "",
      unstable: false,
      createdAt: "",
      updatedAt: "",
    };
    const wPass = mount(CaseDetail, {
      props: {
        testCase: makeRunCase({ result: "passed" }),
        result: "passed",
        linkedOpenIssue: openIssue,
      },
    });
    const closeBtn = wPass.findAll("button").find((b) => b.text().includes("关闭关联历史问题"));
    expect(closeBtn).toBeDefined();
    await closeBtn!.trigger("click");
    const confirmBtn = wPass.findAll("button").find((b) => b.text().includes("确认关闭"));
    expect(confirmBtn).toBeDefined();
    await confirmBtn!.trigger("click");
    const emitted = wPass.emitted("closeLinkedIssue");
    expect(emitted).toBeTruthy();
    expect(emitted![0][0]).toMatchObject({ issue: openIssue });
  });

  it("PlanLedger 展示用例来源、生成器版本与断言强度", () => {
    const task: Task = {
      id: "t1",
      projectId: "p1",
      title: "T",
      goal: "",
      intent: "business",
      origin: "manual",
      sourceMapRevision: 0,
      scope: "",
      inputs: "",
      outputs: "",
      preconditions: [],
      executor: "",
      reviewer: "",
      acceptanceItems: [{ id: "ai_1", no: "01", text: "创建成功", observable: true, status: "active", source: "", moduleId: null, moduleName: "", featureName: "" }],
      recommendedItemIds: ["ai_1"],
      selectedItemIds: ["ai_1"],
      stage: "planned",
      previousStage: null,
      revision: 1,
      createdAt: "",
      updatedAt: "",
      plans: [],
      currentPlanId: "pl1",
      runs: [],
      currentRunId: null,
      delivery: null,
    };
    const plan: Plan = {
      id: "pl1",
      projectId: "p1",
      taskId: "t1",
      no: 1,
      revision: 1,
      status: "published",
      taskRevision: 1,
      taskContract: "",
      createdAt: "",
      scope: { preset: "all_applicable", moduleIds: [], acceptanceItemIds: ["ai_1"], coverage: { selected: 1, applicable: 1 } },
      applicableCount: 1,
      cases: [
        {
          id: "c1",
          itemId: "ai_1",
          extraItemIds: [],
          layer: "L2",
          title: "确定性 CRUD",
          preconditions: ["ready"],
          steps: [],
          expected: "ok",
          expectationSource: "创建成功",
          expectationVerified: true,
          verifyMethod: "http:[]",
          dependencies: [],
          mockAllowed: true,
          importance: "high",
          dependsOn: "service",
          scriptName: null,
          scriptContent: null,
          manual: false,
          generation: { source: "invariant", generatorId: "rest-crud", generatorVersion: "1.0.0" },
          assertionStrength: "strong",
        },
      ],
      unselectedByModule: [],
      warnings: [],
      sourceSignature: "",
      confirmation: null,
      stale: false,
      staleReason: "",
    };
    const w = mount(PlanLedger, { props: { plan, task, groupByFlow: true } });
    expect(w.text()).toContain("确定性·rest-crud v1.0.0");
    expect(w.text()).toContain("强断言");
  });
});
