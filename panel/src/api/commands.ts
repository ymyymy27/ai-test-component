/** 有类型的业务调用。每个方法对应一条 CLI 命令，字段名与契约一致。 */
import { callHost } from "./host";
import type {
  AddTaskResult,
  DeliveryDraft,
  DoctorFacts,
  EvidenceItem,
  GeneratedPlan,
  IssueSummary,
  ModuleSuggestion,
  Opinion,
  Plan,
  Project,
  ProjectConfig,
  ProjectSummary,
  ReportSummary,
  RunDetail,
  RunSummary,
  SuggestResult,
  Task,
  TaskSummary,
  TemplateResult,
  TemplateSummary,
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

  getProjectConfig(projectId: string): Promise<{ config: ProjectConfig }> {
    return callHost("project.config", { project_id: projectId, action: "get" });
  },

  saveProjectConfig(
    projectId: string,
    patch: ProjectConfig,
  ): Promise<{ config: ProjectConfig }> {
    return callHost("project.config", {
      project_id: projectId,
      action: "update",
      patch,
    });
  },

  suggestTargets(projectId: string): Promise<SuggestResult> {
    return callHost("task.suggest", { project_id: projectId });
  },

  listTasks(projectId: string): Promise<{ tasks: TaskSummary[] }> {
    return callHost("task.list", { project_id: projectId });
  },

  addModule(
    projectId: string,
    input: { name: string; responsibility?: string; owner?: string; deps?: unknown[] },
  ): Promise<{ module_id: string; revision: number }> {
    return callHost("module.add", {
      project_id: projectId,
      name: input.name,
      responsibility: input.responsibility ?? "",
      owner: input.owner ?? "",
      deps: input.deps ?? [],
    });
  },

  suggestModules(projectId: string): Promise<{ modules: ModuleSuggestion[]; registered: string[] }> {
    return callHost("module.suggest", { project_id: projectId });
  },

  addTask(input: {
    projectId: string;
    title: string;
    goal?: string;
    requirement?: string;
    intent?: "smoke" | "business";
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
    if (input.intent) {
      payload["intent"] = input.intent;
    }
    if (input.acceptanceItems && input.acceptanceItems.length > 0) {
      payload["acceptance_items"] = input.acceptanceItems;
    }
    return callHost("task.add", payload);
  },

  showTask(projectId: string, taskId: string): Promise<{ task: Task }> {
    return callHost("task.show", { project_id: projectId, task_id: taskId });
  },

  /** 验收项增删改：后端以整份验收项替换并递增修订号。 */
  updateTask(input: {
    projectId: string;
    taskId: string;
    revision: number;
    items: {
      item_id?: string;
      text: string;
      observable: boolean;
      status: "active" | "pending";
      source?: string;
    }[];
  }): Promise<AddTaskResult> {
    return callHost("task.update", {
      project_id: input.projectId,
      task_id: input.taskId,
      expected_revision: input.revision,
      patch: { acceptance_items: input.items },
    });
  },

  draftDelivery(
    projectId: string,
    taskId: string,
    commit?: string,
  ): Promise<{ draft: DeliveryDraft; sources: { readme: string; manifest: string; commit_stat: boolean } }> {
    return callHost("delivery.draft", {
      project_id: projectId,
      task_id: taskId,
      ...(commit ? { commit } : {}),
    });
  },

  saveDelivery(input: {
    projectId: string;
    taskId: string;
    commit: string;
    done: string[];
    not_done: string[];
    changed_modules: string[];
    interface_changes: string[];
    run_commands: string[];
    test_data: string;
    dependencies: string[];
    mocks: string[];
    self_test_evidence: string[];
  }): Promise<{ delivery_id: string; revision: number; complete: boolean }> {
    return callHost("delivery.save", {
      project_id: input.projectId,
      task_id: input.taskId,
      commit: input.commit,
      done: input.done,
      not_done: input.not_done,
      changed_modules: input.changed_modules,
      interface_changes: input.interface_changes,
      run_commands: input.run_commands,
      test_data: input.test_data,
      dependencies: input.dependencies,
      mocks: input.mocks,
      self_test_evidence: input.self_test_evidence,
    });
  },

  listTemplates(): Promise<{ templates: TemplateSummary[] }> {
    return callHost("template.list", {});
  },

  applyTemplate(input: {
    projectId: string;
    taskId: string;
    templateName: string;
    planId?: string;
  }): Promise<{
    template: TemplateResult;
    path: string;
    plan_id: string;
    plan_revision: number | null;
  }> {
    return callHost("template.apply", {
      project_id: input.projectId,
      task_id: input.taskId,
      template_name: input.templateName,
      ...(input.planId ? { plan_id: input.planId } : {}),
    });
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
        module_ids: [...input.moduleIds],
        acceptance_item_ids:
          input.preset === "selected" ? [...input.acceptanceItemIds] : [],
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

  updateIssue(
    projectId: string,
    issueId: string,
    patch: { status?: string; severity?: string; disposition?: string | null; fix_version?: string | null },
  ): Promise<{ issue_id: string; status: string; revision: number }> {
    return callHost("issue.update", { project_id: projectId, issue_id: issueId, ...patch });
  },

  closeIssue(
    projectId: string,
    issueId: string,
    runId: string,
  ): Promise<{ issue_id: string; status: string }> {
    return callHost("issue.close", {
      project_id: projectId,
      issue_id: issueId,
      run_id: runId,
    });
  },

  listEvidence(projectId: string, runId: string): Promise<{ evidence: EvidenceItem[]; run_id?: string }> {
    return callHost("evidence.list", { project_id: projectId, run_id: runId });
  },
};

export type Api = typeof api;
