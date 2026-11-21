import { describe, expect, it } from "vitest";

import { applyIssueUpdate, canTransitionIssue, issueTransitionNeedsReason } from "@/domain/issues";
import type { Issue } from "@/domain/types";

function issue(overrides: Partial<Issue> = {}): Issue {
  return {
    id: "issue_1",
    projectId: "p",
    taskId: "t",
    planId: "plan",
    runId: "run",
    itemId: "item",
    caseId: "case",
    signature: "sig",
    title: "问题",
    expected: "预期",
    actual: "实际",
    steps: [],
    severity: "P2",
    status: "in_progress",
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
    ...overrides,
  };
}

describe("issue transitions", () => {
  it("allows retest back to pending, matching the CLI", () => {
    expect(canTransitionIssue("retest", "pending")).toBe(true);
    const updated = applyIssueUpdate(issue({ status: "retest", disposition: "fixed" }), {
      status: "pending",
    });
    expect(updated.status).toBe("pending");
  });

  it("only requires a reason when reopening a closed issue", () => {
    expect(issueTransitionNeedsReason("in_progress", "retest")).toBe(false);
    expect(issueTransitionNeedsReason("closed", "in_progress")).toBe(true);
    expect(() => applyIssueUpdate(issue({ status: "closed" }), { status: "in_progress" })).toThrow(
      "理由",
    );
    const updated = applyIssueUpdate(issue({ status: "closed", disposition: "fixed" }), {
      status: "in_progress",
      reason: "再次复现",
    });
    expect(updated.status).toBe("in_progress");
    expect(updated.disposition).toBeNull();
  });

  it("requires a reason for non-fixed dispositions", () => {
    expect(() =>
      applyIssueUpdate(issue(), { status: "retest", disposition: "deferred" }),
    ).toThrow("理由");
    expect(
      applyIssueUpdate(issue(), { status: "retest", disposition: "deferred", reason: "排期" })
        .disposition,
    ).toBe("deferred");
  });
});
