import type { Task, TaskStage } from "./types";

export type StageAction =
  "confirmGoal" | "generatePlan" | "startRun" | "finishRun" | "reviseScope" | "cancelRevise";

export class TaskStageError extends Error {
  readonly from: TaskStage;
  readonly to: TaskStage;
  readonly action: StageAction;

  constructor(from: TaskStage, to: TaskStage, action: StageAction) {
    super(`不能在 ${from} 阶段执行 ${action}（目标阶段 ${to}）`);
    this.name = "TaskStageError";
    this.from = from;
    this.to = to;
    this.action = action;
  }
}

const TRANSITIONS: Record<TaskStage, Partial<Record<StageAction, TaskStage>>> = {
  fresh: { confirmGoal: "scope" },
  scope: { generatePlan: "planned" },
  planned: { startRun: "running", reviseScope: "scope" },
  running: { finishRun: "result" },
  result: { reviseScope: "scope" },
};

export function canTransition(from: TaskStage, action: StageAction, to: TaskStage): boolean {
  if (action === "cancelRevise") {
    return from === "scope" && (to === "planned" || to === "result");
  }
  return TRANSITIONS[from][action] === to;
}

export function assertTransition(from: TaskStage, action: StageAction, to: TaskStage): void {
  if (!canTransition(from, action, to)) {
    throw new TaskStageError(from, to, action);
  }
}

export function transition(from: TaskStage, action: StageAction, to: TaskStage): TaskStage {
  assertTransition(from, action, to);
  return to;
}

export function isRunning(stage: TaskStage): boolean {
  return stage === "running";
}

export function canEditScope(stage: TaskStage): boolean {
  return stage === "fresh" || stage === "scope";
}

export function canConfirmPlan(stage: TaskStage): boolean {
  return stage === "planned";
}

export function stageLabel(stage: TaskStage): string {
  switch (stage) {
    case "fresh":
      return "待确认目标";
    case "scope":
      return "选范围";
    case "planned":
      return "待确认";
    case "running":
      return "运行中";
    case "result":
      return "已出结果";
    default:
      return "未知";
  }
}

/** Goal tasks without a plan failed before the scope step; do not label them 选范围. */
export function taskStageLabel(task: Task): string {
  if (task.stage === "scope" && task.origin === "goal" && task.plans.length === 0) {
    return "计划未生成";
  }
  return stageLabel(task.stage);
}
