import { describe, expect, it } from "vitest";

import { taskStageLabel } from "@/domain/stateMachine";
import type { Task } from "@/domain/types";
import { planRedirectFor } from "@/router";

function task(overrides: Partial<Task>): Task {
  return {
    id: "task_1",
    projectId: "proj_1",
    title: "任务",
    goal: "目标",
    intent: "business",
    origin: "goal",
    sourceMapRevision: 0,
    scope: "",
    inputs: "",
    outputs: "",
    preconditions: [],
    executor: "",
    reviewer: "",
    acceptanceItems: [],
    recommendedItemIds: [],
    selectedItemIds: [],
    stage: "scope",
    previousStage: null,
    revision: 1,
    createdAt: "",
    updatedAt: "",
    plans: [],
    currentPlanId: null,
    runs: [],
    currentRunId: null,
    delivery: null,
    templates: [],
    ...overrides,
  };
}

describe("goal and range flow", () => {
  it("keeps a goal task without a plan on the plan page", () => {
    expect(planRedirectFor(task({ origin: "goal" }))).toBeNull();
  });

  it("sends a range task without a plan to the scope page", () => {
    expect(planRedirectFor(task({ origin: "range" }))).toEqual({
      name: "scope",
      params: { projectId: "proj_1", taskId: "task_1" },
    });
  });

  it("labels a goal task without a plan as plan not generated", () => {
    expect(taskStageLabel(task({ origin: "goal" }))).toBe("计划未生成");
    expect(taskStageLabel(task({ origin: "range" }))).toBe("选范围");
  });
});
