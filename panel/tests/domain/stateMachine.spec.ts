import { describe, expect, it } from "vitest";

import { TaskStageError, canTransition, transition } from "@/domain/stateMachine";

describe("task state machine", () => {
  it("allows the forward path fresh → scope → planned → running → result", () => {
    expect(transition("fresh", "confirmGoal", "scope")).toBe("scope");
    expect(transition("scope", "generatePlan", "planned")).toBe("planned");
    expect(transition("planned", "startRun", "running")).toBe("running");
    expect(transition("running", "finishRun", "result")).toBe("result");
  });

  it("allows only explicit revision edges", () => {
    expect(transition("planned", "reviseScope", "scope")).toBe("scope");
    expect(transition("result", "reviseScope", "scope")).toBe("scope");
    expect(transition("scope", "cancelRevise", "planned")).toBe("planned");
    expect(transition("scope", "cancelRevise", "result")).toBe("result");
  });

  it("rejects skipping and running rollbacks", () => {
    expect(canTransition("scope", "startRun", "running")).toBe(false);
    expect(canTransition("planned", "finishRun", "result")).toBe(false);
    expect(canTransition("running", "reviseScope", "scope")).toBe(false);
    expect(canTransition("result", "startRun", "running")).toBe(false);
    expect(() => transition("scope", "startRun", "running")).toThrow(TaskStageError);
    expect(() => transition("running", "reviseScope", "scope")).toThrow(TaskStageError);
  });

  it("does not allow cancelRevise from outside scope", () => {
    expect(canTransition("planned", "cancelRevise", "result")).toBe(false);
    expect(canTransition("scope", "cancelRevise", "running")).toBe(false);
  });
});
