import { describe, expect, it } from "vitest";

import { simulateOutcome } from "@/services/execution";
import type { Project, Run, RunCase, Task } from "@/domain/types";

const project = { id: "p", config: {} } as Project;
const task = { id: "t", acceptanceItems: [] } as unknown as Task;
const run = {
  id: "run_1",
  l1: [],
} as unknown as Run;

function runCase(overrides: Partial<RunCase> = {}): RunCase {
  return {
    id: "rc_1",
    caseId: "case_1",
    itemId: "item_1",
    title: "检查主流程",
    method: "",
    layer: "L2",
    expected: "返回成功",
    expectationVerified: true,
    dependsOn: null,
    actual: "",
    result: "not_run",
    evidence: [],
    message: "",
    errorType: "",
    stackKey: "",
    attempts: 0,
    manualSteps: [],
    ...overrides,
  } as RunCase;
}

describe("local simulation", () => {
  it("records an unverified expectation as blocked, never passed", () => {
    const outcome = simulateOutcome(project, task, run, runCase({ expectationVerified: false }), 1);
    expect(outcome.result).toBe("blocked");
    expect(outcome.errorType).toBe("expectation_unverified");
  });
});
