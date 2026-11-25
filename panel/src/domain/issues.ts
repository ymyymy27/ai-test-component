import { hashText, isoNow } from "./ids";
import type { Issue, IssueDisposition, IssueOccurrence, IssueStatus, Run, RunCase } from "./types";

const TRANSITIONS: Record<IssueStatus, IssueStatus[]> = {
  pending: ["in_progress"],
  in_progress: ["pending", "retest"],
  retest: ["in_progress", "pending"],
  closed: ["in_progress"],
};

export class IssueTransitionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "IssueTransitionError";
  }
}

export function canTransitionIssue(from: IssueStatus, to: IssueStatus): boolean {
  return TRANSITIONS[from].includes(to);
}

export function issueTransitionNeedsReason(from: IssueStatus, to: IssueStatus): boolean {
  // 与 CLI 对齐：只有从已关闭重新打开必须写原因；处置值另有理由要求。
  return from === "closed" && to !== "closed";
}

export function applyIssueUpdate(
  issue: Issue,
  patch: {
    status?: IssueStatus;
    reason?: string;
    disposition?: IssueDisposition | null;
    fixVersion?: string;
    severity?: Issue["severity"];
  },
): Issue {
  const status = patch.status ?? issue.status;
  const reason = (patch.reason ?? "").trim();
  if (status !== issue.status) {
    if (status === "closed") {
      throw new IssueTransitionError(
        "关闭问题必须绑定一次满足条件的新运行，不能在状态更新里直接关闭。",
      );
    }
    if (!canTransitionIssue(issue.status, status)) {
      throw new IssueTransitionError(`不能从「${issue.status}」直接变为「${status}」。`);
    }
    if (issueTransitionNeedsReason(issue.status, status) && !reason) {
      throw new IssueTransitionError("这个状态变化需要写理由。");
    }
    if (
      status === "retest" &&
      patch.disposition &&
      patch.disposition !== "fixed" &&
      !reason
    ) {
      throw new IssueTransitionError("非缺陷、重复或延期必须写理由。");
    }
  } else if (
    patch.disposition !== undefined &&
    patch.disposition !== issue.disposition &&
    patch.disposition !== null &&
    patch.disposition !== "fixed" &&
    !reason
  ) {
    throw new IssueTransitionError("非缺陷、重复或延期必须写理由。");
  }
  const reopening = issue.status === "closed" && status === "in_progress";
  return {
    ...issue,
    status,
    severity: patch.severity ?? issue.severity,
    disposition:
      status === "retest"
        ? (patch.disposition ?? null)
        : reopening
          ? null
          : patch.disposition === undefined
            ? issue.disposition
            : patch.disposition,
    reason: reason || issue.reason,
    fixVersion: patch.fixVersion ?? issue.fixVersion,
    verificationRunId: reopening ? "" : issue.verificationRunId,
    updatedAt: isoNow(),
  };
}

export function closeIssueRecord(issue: Issue, run: Run, runCase: RunCase, reason: string): Issue {
  const text = reason.trim();
  if (issue.status !== "retest") {
    throw new IssueTransitionError("问题需要先提交复测，才能关闭。");
  }
  if (!text) {
    throw new IssueTransitionError("关闭问题需要写理由。");
  }
  const now = isoNow();
  return {
    ...issue,
    status: "closed",
    disposition: "fixed",
    reason: text,
    verificationRunId: run.id,
    updatedAt: now,
    occurrences: [
      ...issue.occurrences,
      {
        runId: run.id,
        caseId: runCase.caseId,
        time: now,
        result: "passed",
        note: "关闭前复测通过",
        evidenceRefs: runCase.evidence.map((entry) => entry.name),
      },
    ],
  };
}

export function signatureForCase(runCase: RunCase): string {
  return hashText(`${runCase.caseId}|${runCase.errorType}|${runCase.stackKey}`);
}

export function mergeIssueOccurrence(
  issue: Issue,
  run: Run,
  runCase: RunCase,
  note: string,
): Issue {
  const now = isoNow();
  const occurrence: IssueOccurrence = {
    runId: run.id,
    caseId: runCase.caseId,
    time: now,
    result: runCase.result,
    note: note || runCase.actual || "运行记录",
    evidenceRefs: runCase.evidence.map((entry) => entry.name),
  };
  const occurrences = [...issue.occurrences, occurrence];
  const results = new Set(occurrences.map((entry) => entry.result));
  const reopened = issue.status === "closed";
  return {
    ...issue,
    runId: run.id,
    planId: run.planId,
    status: reopened ? "pending" : issue.status,
    reason: reopened ? "同一失败签名再次出现，自动回到待确认。" : issue.reason,
    verificationRunId: reopened ? "" : issue.verificationRunId,
    occurrences,
    unstable: results.size > 1,
    updatedAt: now,
  };
}

export function canDeduplicate(issue: Issue, runCase: RunCase): boolean {
  return issue.signature === signatureForCase(runCase);
}
