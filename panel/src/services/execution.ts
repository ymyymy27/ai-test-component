import { projectCheckBlocks } from "@/domain/derive";
import { hashText, newId } from "@/domain/ids";
import type { CaseResult, EvidenceRef, Project, Run, RunCase, Task } from "@/domain/types";

export interface SimulatedOutcome {
  result: CaseResult;
  actual: string;
  message: string;
  errorType: string;
  stackKey: string;
  evidence: EvidenceRef[];
}

function evidenceFor(run: Run, runCase: RunCase): EvidenceRef {
  const name = `${run.id}-${runCase.caseId}.log`;
  return {
    id: newId("ev"),
    name,
    mediaType: "LOG",
    bytes: 2048,
    sha256: hashText(name),
    note: `用例「${runCase.title}」的执行日志`,
    missing: false,
    truncated: false,
    addedAt: new Date().toISOString(),
    sourcePath: `runs/${run.id}/evidence/${name}`,
  };
}

export function simulateOutcome(
  project: Project,
  task: Task,
  run: Run,
  runCase: RunCase,
  taskRunCount: number,
): SimulatedOutcome {
  if (runCase.layer === "L3") {
    return {
      result: "manual_pending",
      actual: "等待人工记录",
      message: "运行结束后由人记录步骤结果。",
      errorType: "",
      stackKey: "",
      evidence: [],
    };
  }
  if (runCase.dependsOn && projectCheckBlocks(run, runCase.dependsOn)) {
    return {
      result: "blocked",
      actual: "项目级检查未通过",
      message:
        runCase.dependsOn === "build"
          ? "构建未通过，依赖构建的用例按阻塞记录。"
          : "服务未就绪，依赖服务的用例按阻塞记录。",
      errorType: "dependency",
      stackKey: runCase.dependsOn,
      evidence: [evidenceFor(run, runCase)],
    };
  }
  const item = task.acceptanceItems.find((entry) => entry.id === runCase.itemId);
  const firstRunFailure = item?.no === "03" && taskRunCount <= 1;
  if (firstRunFailure) {
    return {
      result: "failed",
      actual: "返回 200 而不是 4xx；错误提示为空。",
      message: "断言失败：异常输入没有被拒绝。",
      errorType: "assertionerror",
      stackKey: "assert status_code == 400",
      evidence: [evidenceFor(run, runCase)],
    };
  }
  if (!runCase.expectationVerified) {
    // 预期没有引用验收项原文：执行结果只能算阻塞，不能以 passed 落盘。
    return {
      result: "blocked",
      actual: "执行返回成功；预期未核实。",
      message: "预期没有引用到验收项原文，按阻塞计。",
      errorType: "expectation_unverified",
      stackKey: "expectation_source",
      evidence: [evidenceFor(run, runCase)],
    };
  }
  void project;
  return {
    result: "passed",
    actual: "符合预期。",
    message: "",
    errorType: "",
    stackKey: "",
    evidence: [evidenceFor(run, runCase)],
  };
}
