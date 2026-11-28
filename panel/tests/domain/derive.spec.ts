import { describe, expect, it } from "vitest";

import {
  conclusionForRun,
  displayItemOutcome,
  itemOutcome,
  layersForRun,
  normalizeReport,
  problemRunItems,
  projectStatus,
  recountRun,
  sideEffectsFor,
  sortedRunItems,
  unselectedItems,
  verdictForRun,
  worstResult,
} from "@/domain/derive";
import { buildReport } from "@/domain/factory";
import type { Run } from "@/domain/types";
import { completedRun } from "../fixtures";

describe("derived results", () => {
  it("marks host list summaries as not loaded instead of empty", () => {
    expect(
      projectStatus({
        id: "p",
        name: "摘要",
        root: "D:/work/p",
        goal: "",
        createdAt: "",
        updatedAt: "",
        analysisStatus: "ready",
        analysisStartedAt: "",
        analysisError: "",
        candidates: [],
        modules: [],
        map: null,
        catalog: [],
        tasks: [],
        activeTaskId: null,
        config: {},
        github: null,
        quickChecks: [],
        issues: [],
        reports: [],
        archived: false,
        summaryOnly: true,
      }),
    ).toEqual({ tone: "neutral", label: "未加载" });
  });

  it("fills missing legacy report fields and keeps the L1 layer from project checks", async () => {
    const legacy = normalizeReport({
      id: "rep_old",
      projectId: "p",
      taskId: "t",
      runId: "r",
      version: 1,
      generatedAt: "",
      verdict: "incomplete",
    } as never);
    expect(legacy.itemDetails).toEqual([]);
    expect(legacy.planWarnings).toEqual([]);
    expect(legacy.layers.L1.total).toBe(0);
    expect(legacy.coverage.selected).toBe(0);

    const seeded = await completedRun(["01"]);
    const run = seeded.task.runs[0] as Run;
    run.l1 = [
      {
        key: "build",
        name: "构建",
        method: "npm run build",
        result: "passed",
        detail: "",
        time: "",
      },
      {
        key: "smoke",
        name: "最小测试",
        method: "npm test",
        result: "failed",
        detail: "",
        time: "",
      },
    ];
    const report = buildReport(seeded.project, seeded.task, run, 1);
    expect(report.layers.L1).toEqual({ total: 2, passed: 1, failed: 1, incomplete: 0 });
  });

  it("ranks the worst case result deterministically", () => {
    expect(worstResult([])).toBe("not_run");
    expect(worstResult(["passed", "blocked", "failed"])).toBe("failed");
    expect(worstResult(["passed", "manual_pending"])).toBe("manual_pending");
    expect(worstResult(["passed", "passed"])).toBe("passed");
  });

  it("keeps S/M visible and avoids 全部通过 when scope is narrower", async () => {
    const seeded = await completedRun(["01"]);
    const run = seeded.task.runs[0] as Run;
    expect(run.counts.selected).toBe(1);
    expect(run.counts.applicable).toBe(4);
    expect(run.verdict).toBe("passed");
    const conclusion = conclusionForRun(run);
    expect(conclusion).toContain("本次范围通过");
    expect(conclusion).not.toContain("全部通过");
    expect(conclusion).toContain("未纳入 3 项");
    expect(unselectedItems(seeded.task, run.scope.acceptanceItemIds)).toHaveLength(3);
  });

  it("keeps an L1 failure in the fallback conclusion", async () => {
    const seeded = await completedRun(["01"]);
    const run = seeded.task.runs[0] as Run;
    run.l1 = [
      {
        key: "build",
        name: "构建",
        method: "python -m compileall",
        result: "failed",
        detail: "",
        time: "",
        reason: "构建失败",
      },
    ];
    expect(conclusionForRun(run)).toContain("项目级检查未通过");
    expect(conclusionForRun(run)).toContain("结论为失败");
  });

  it("does not aggregate a failed first run into passed", async () => {
    const seeded = await completedRun(["03"]);
    const run = seeded.task.runs[0] as Run;
    expect(run.counts.failed).toBe(1);
    expect(run.counts.passed).toBe(0);
    expect(verdictForRun(run)).toBe("failed");
    expect(problemRunItems(run)).toHaveLength(1);
    expect(itemOutcome(run, run.scope.acceptanceItemIds[0] as string)).toBe("failed");
  });

  it("turns a passed case with unverified expectation into blocked", async () => {
    const seeded = await completedRun(["01"]);
    const run = seeded.task.runs[0] as Run;
    const mutated: Run = {
      ...run,
      cases: run.cases.map((testCase) => ({
        ...testCase,
        result: "passed",
        expectationVerified: false,
      })),
    };
    const recounted = recountRun(mutated);
    expect(recounted.items[0]?.outcome).toBe("blocked");
    expect(recounted.counts.passed).toBe(0);
    expect(recounted.counts.incomplete).toBe(1);
    expect(recounted.verdict).toBe("incomplete");
    expect(sortedRunItems(recounted)[0]?.outcome).toBe("blocked");
  });

  it("counts layers separately from acceptance coverage", async () => {
    const seeded = await completedRun(["03"]);
    const run = seeded.task.runs[0] as Run;
    const layers = layersForRun(run);
    expect(layers.L2.total).toBe(1);
    expect(layers.L2.failed).toBe(1);
    expect(layers.L3.total).toBe(1);
    expect(layers.L3.incomplete).toBe(1);
  });

  it("lists side effects from config without inventing credentials", async () => {
    const seeded = await completedRun(["01"]);
    const effects = sideEffectsFor(seeded.project);
    expect(effects.some((effect) => effect.includes("只读取项目文件"))).toBe(true);
    expect(effects.join(" ")).not.toContain("password");
  });

  it("prefers the core item outcome for host runs", async () => {
    const seeded = await completedRun(["01"]);
    const run = seeded.task.runs[0] as Run;
    const itemId = run.scope.acceptanceItemIds[0] as string;
    const hostRun: Run = {
      ...run,
      simulated: false,
      items: run.items.map((item) => ({ ...item, outcome: "failed" })),
    };
    expect(displayItemOutcome(hostRun, itemId)).toBe("failed");
    expect(displayItemOutcome(run, itemId)).toBe("passed");
  });

  it("counts a case against every bound acceptance item", async () => {
    const seeded = await completedRun(["01"]);
    const run = seeded.task.runs[0] as Run;
    const itemId = run.scope.acceptanceItemIds[0] as string;
    const extraId = "extra_item";
    const bound: Run = {
      ...run,
      cases: run.cases.map((testCase) => ({
        ...testCase,
        itemId,
        caseItemIds: [itemId, extraId],
        result: "failed",
      })),
    };
    expect(itemOutcome(bound, extraId)).toBe("failed");
  });

  it("does not report passed for a cancelled or interrupted run", async () => {
    const seeded = await completedRun(["01"]);
    const run = seeded.task.runs[0] as Run;
    expect(verdictForRun({ ...run, status: "cancelled" })).toBe("incomplete");
    expect(recountRun({ ...run, status: "interrupted" }).verdict).toBe("incomplete");
  });

  it("recounts item caseIds with caseItemIds instead of the primary item", async () => {
    const seeded = await completedRun(["01", "02"]);
    const run = seeded.task.runs[0] as Run;
    const [first, second] = run.scope.acceptanceItemIds;
    const bound: Run = {
      ...run,
      cases: run.cases.map((testCase) =>
        testCase.itemId === first
          ? { ...testCase, caseItemIds: [first, second] as string[], result: "passed" }
          : testCase,
      ),
    };
    const recounted = recountRun(bound);
    const secondItem = recounted.items.find((item) => item.itemId === second);
    expect(secondItem?.caseIds).toContain(bound.cases.find((entry) => entry.itemId === first)?.id);
  });

  it("uses the latest run for project status instead of any historical failure", async () => {
    const seeded = await completedRun(["01"]);
    const project = seeded.project;
    const task = project.tasks[0];
    if (!task) {
      throw new Error("task missing");
    }
    const current = task.runs[0] as Run;
    const oldFailed: Run = {
      ...current,
      id: "run_old_failed",
      startedAt: "2020-01-01T00:00:00.000Z",
      verdict: "failed",
    };
    const nowPassed: Run = {
      ...current,
      verdict: "passed",
      startedAt: "2026-11-21T00:00:00.000Z",
    };
    const updated = {
      ...project,
      issues: [],
      tasks: [{ ...task, runs: [nowPassed, oldFailed] }],
    };
    expect(projectStatus(updated)).toEqual({ tone: "ok", label: "已就绪" });
  });
});
