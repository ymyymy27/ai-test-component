/** 有类型的业务调用。每个方法对应一条 CLI 命令，字段名与契约一致。 */
import { callHost } from "./host";
import type {
  AddTaskResult,
  DoctorFacts,
  GeneratedPlan,
  IssueSummary,
  Opinion,
  Plan,
  Project,
  ProjectSummary,
  ReportSummary,
  RunDetail,
  RunSummary,
  SuggestResult,
  Task,
} from "./types";

export const api = {
  doctor(repair = false): Promise<DoctorFacts> {
    return callHost<DoctorFacts>("doctor", repair ? { repair: true } : {});
  },

  listProjects(): Promise<{ projects: ProjectSummary[] }> {
    return callHost("project.list", {});
  },

  addProject(root: string, name?: string): Promise<{ project_id: string; revision: number; existing: boolean }> {
    return callHost("project.add", { root, ...(name ? { name } : {}) });
  },

  showProject(projectId: string): Promise<{ project: Project }> {
    return callHost("project.show", { project_id: projectId });
  },

  suggestTargets(projectId: string): Promise<SuggestResult> {
    return callHost("task.suggest", { project_id: projectId });
  },

  addTask(input: {
    projectId: string;
    title: string;
    goal?: string;
    requirement?: string;
    acceptanceItems?: { text: string; observable: boolean; source?: string }[];
  }): Promise<AddTaskResult> {
    const payload: Record<string, unknown> = {
      project_id: input.projectId,
      title: input.title,
    };
    if (input.goal) {
      payload["goal"] = input.goal;
    }
    if (input.requirement) {
      payload["requirement"] = input.requirement;
    }
    if (input.acceptanceItems && input.acceptanceItems.length > 0) {
      payload["acceptance_items"] = input.acceptanceItems;
    }
    return callHost("task.add", payload);
  },

  showTask(projectId: string, taskId: string): Promise<{ task: Task }> {
    return callHost("task.show", { project_id: projectId, task_id: taskId });
  },

  generatePlan(input: {
    projectId: string;
    taskId: string;
    preset: "all_applicable" | "selected";
    moduleIds: string[];
    acceptanceItemIds: string[];
  }): Promise<GeneratedPlan> {
    return callHost("plan.generate", {
      project_id: input.projectId,
      task_id: input.taskId,
      scope: {
        preset: input.preset,
        module_ids: input.moduleIds,
        acceptance_item_ids: input.preset === "selected" ? input.acceptanceItemIds : [],
      },
    });
  },

  showPlan(projectId: string, planId: string): Promise<{ plan: Plan }> {
    return callHost("plan.show", { project_id: projectId, plan_id: planId });
  },

  startRun(
    projectId: string,
    planId: string,
    acceptanceItemIds?: string[],
  ): Promise<{
    run_id: string;
    status: string;
    scope: { selected: number; applicable: number };
  }> {
    return callHost(
      "run.start",
      {
        project_id: projectId,
        plan_id: planId,
        ...(acceptanceItemIds && acceptanceItemIds.length
          ? { acceptance_item_ids: acceptanceItemIds }
          : {}),
      },
      { confirm: true },
    );
  },

  runStatus(projectId: string, runId: string): Promise<RunDetail> {
    return callHost("run.status", { project_id: projectId, run_id: runId });
  },

  listRuns(projectId: string, limit = 20): Promise<{ runs: RunSummary[] }> {
    return callHost("run.list", { project_id: projectId, limit });
  },

  cancelRun(projectId: string, runId: string): Promise<{ cancel_requested: boolean }> {
    return callHost("run.cancel", { project_id: projectId, run_id: runId });
  },

  runAnalysis(projectId: string, runId: string): Promise<{ status: string; analysis: unknown }> {
    return callHost("analysis.run", { project_id: projectId, run_id: runId });
  },

  generateReport(projectId: string, runId: string): Promise<ReportSummary> {
    return callHost("report.generate", { project_id: projectId, run_id: runId });
  },

  exportReport(
    projectId: string,
    reportId: string,
    path: string,
  ): Promise<{ path: string; bytes: number }> {
    return callHost("report.export", { project_id: projectId, report_id: reportId, path });
  },

  saveOpinion(input: {
    projectId: string;
    reportId: string;
    by: string;
    text: string;
  }): Promise<{ opinion: Opinion }> {
    return callHost("report.opinion", {
      project_id: input.projectId,
      report_id: input.reportId,
      by: input.by,
      text: input.text,
    });
  },

  listIssues(projectId: string): Promise<{ issues: IssueSummary[] }> {
    return callHost("issue.list", { project_id: projectId });
  },
};

export type Api = typeof api;
