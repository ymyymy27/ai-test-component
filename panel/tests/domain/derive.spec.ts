import { describe, expect, it } from "vitest";

import {
  conclusionForRun,
  itemOutcome,
  layersForRun,
  problemRunItems,
  recountRun,
  sideEffectsFor,
  sortedRunItems,
  unselectedItems,
  verdictForRun,
  worstResult,
} from "@/domain/derive";
import type { Run } from "@/domain/types";
import { completedRun } from "../fixtures";

describe("derived results", () => {
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
});
