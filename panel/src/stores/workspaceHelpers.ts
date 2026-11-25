import { HostError } from "@/host/bridge";
import { describeError } from "@/host/errors";
import type { Project, Run, Task } from "@/domain/types";
import { RepositoryError } from "@/repositories/errors";
import type { ProjectRepository } from "@/repositories/ProjectRepository";
import { simulateOutcome } from "@/services/execution";
import { RunSimulator } from "@/services/runSimulator";

/** 将存储或宿主错误转为对用户友好的中文提示。 */
export function errorMessage(error: unknown): string {
  if (error instanceof RepositoryError || error instanceof HostError) {
    const friendly = describeError(error);
    const body = friendly.detail || friendly.title;
    return friendly.next ? `${body} ${friendly.next}` : body;
  }
  return error instanceof Error ? error.message : "操作失败，请重试。";
}

export interface SimulationDriverDeps {
  repo: () => ProjectRepository;
  getProject: (projectId: string) => Project | undefined;
  refreshProject: (projectId: string) => Promise<Project | null>;
  onError: (cause: unknown) => void;
  onToast: (message: string, tone: "info" | "success") => void;
}

/** 独立管理本地演示模式的模拟运行生命周期。 */
export function createSimulationDriver(deps: SimulationDriverDeps) {
  let simulator: RunSimulator | null = null;
  let simulatorRunId = "";
  let simulatorProjectId = "";

  function stop(): void {
    simulator?.stop();
    simulator = null;
    simulatorRunId = "";
    simulatorProjectId = "";
  }

  async function finish(projectId: string, runId: string): Promise<void> {
    try {
      await deps.repo().completeRun(projectId, runId);
      await deps.refreshProject(projectId);
      stop();
      const project = deps.getProject(projectId);
      const run = project
        ? project.tasks.flatMap((task) => task.runs).find((entry) => entry.id === runId)
        : null;
      deps.onToast(
        run ? `运行结束：${run.verdict === "passed" ? "通过" : "已生成结果"}。` : "运行结束。",
        run?.verdict === "passed" ? "success" : "info",
      );
    } catch (cause) {
      deps.onError(cause);
      simulator = null;
      simulatorRunId = "";
    }
  }

  async function start(projectId: string, task: Task, run: Run): Promise<void> {
    if (simulator && simulatorRunId === run.id) {
      return;
    }
    if (simulator) {
      const previousRun = simulatorRunId;
      const previousProject = simulatorProjectId;
      simulator.stop();
      // 被替换的旧运行必须落为中断，不能永久停在 running。
      if (previousRun && previousProject) {
        void deps
          .repo()
          .markRunInterrupted(previousProject, previousRun)
          .catch((cause) => deps.onError(cause));
      }
    }
    const instance = new RunSimulator();
    simulator = instance;
    simulatorRunId = run.id;
    simulatorProjectId = projectId;
    const taskRunCount = task.runs.length;
    const project = deps.getProject(projectId);
    if (!project) {
      return;
    }
    const manualCases = run.cases.filter(
      (testCase) => testCase.layer === "L3" && testCase.result === "not_run",
    );
    await Promise.all(
      manualCases.map((testCase) =>
        deps.repo().recordRunCase(projectId, run.id, testCase.id, {
          result: "manual_pending",
          actual: "等待人工记录",
          message: "运行结束后由人记录步骤结果。",
          errorType: "",
          stackKey: "",
          evidence: [],
        }),
      ),
    );
    if (manualCases.length > 0) {
      await deps.refreshProject(projectId);
    }
    const autoCases = run.cases.filter(
      (testCase) => testCase.layer !== "L3" && testCase.result === "not_run",
    );
    if (autoCases.length === 0) {
      await finish(projectId, run.id);
      return;
    }
    instance.start(
      autoCases.map((testCase) => () => {
        const outcome = simulateOutcome(project, task, run, testCase, taskRunCount);
        void deps
          .repo()
          .recordRunCase(projectId, run.id, testCase.id, outcome)
          .then(() => deps.refreshProject(projectId))
          .catch((cause) => {
            instance.stop();
            if (simulator === instance) {
              stop();
            }
            deps.onError(cause);
          });
      }),
      () => {
        void finish(projectId, run.id);
      },
    );
  }

  return { start, stop };
}
