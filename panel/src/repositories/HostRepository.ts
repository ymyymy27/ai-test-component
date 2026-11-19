import { isoNow } from "@/domain/ids";
import type {
  AcceptanceItem,
  Candidate,
  BackupResult,
  Confirmation,
  Delivery,
  EvidenceRef,
  ExportResult,
  GithubSnapshot,
  Issue,
  LinkAssertion,
  Module,
  Plan,
  Project,
  ProjectConfig,
  QuickCheck,
  Report,
  RuleVersion,
  Run,
  Task,
} from "@/domain/types";
import { RepositoryError } from "@/repositories/errors";
import type {
  BackupPayload,
  ConfirmPlanInput,
  CreateProjectInput,
  CreateTaskInput,
  DeliveryInput,
  IssueCreateInput,
  IssuePatch,
  LinkAssertInput,
  ManualResultInput,
  ModuleInput,
  ProjectRepository,
  RepositoryCapabilities,
  RuleInput,
  TaskPatch,
} from "@/repositories/ProjectRepository";
import { readHostContext } from "@/host/bridge";
import { api, type Api } from "@/host/commands";
import type { DeliveryDraft, RunDetail, Task as TaskDto } from "@/host/dto";
import {
  mapCandidate,
  mapEvidence,
  mapGithub,
  mapProject,
  mapProjectSummary,
  mapQuickCheck,
  mapReport,
  mapRun,
  mapTemplateResult,
  toHostConfig,
} from "@/host/map";
import { HostSidecar } from "@/host/sidecar";

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function hostTaskItems(task: TaskDto | null | undefined): AcceptanceItem[] {
  if (!task) {
    return [];
  }
  return task.acceptance_items.map((item, index) => ({
    id: item.item_id,
    no: String(index + 1).padStart(2, "0"),
    text: item.text,
    observable: item.observable,
    status: item.status,
    source: item.source ?? "",
    moduleId: item.module_id ?? null,
    moduleName: "未归类",
    featureName: "未归类",
  }));
}

function mapDraft(draft: DeliveryDraft, taskId: string): Delivery {
  return {
    id: `draft_${taskId}`,
    taskId,
    done: [...draft.done],
    notDone: [...draft.not_done],
    changedModules: [...draft.changed_modules],
    interfaceChanges: [...draft.interface_changes],
    runCommands: [...draft.run_commands],
    testData: draft.test_data.join("、"),
    dependencies: [...draft.dependencies],
    mocks: [...draft.mocks],
    selfTestEvidence: [...draft.self_test_evidence],
    commit: "",
    complete: false,
    revision: 1,
    updatedAt: isoNow(),
  };
}

export class HostRepository implements ProjectRepository {
  readonly mode = "host" as const;
  readonly capabilities: RepositoryCapabilities = {
    hostGithub: true,
    evidenceFiles: false,
    browserBackup: false,
    linkJson: false,
  };

  private readonly sidecar = new HostSidecar();
  private readonly pendingProjects = new Map<string, Promise<Project | null>>();

  constructor(private readonly client: Api = api) {}

  private async projectIdList(): Promise<Project[]> {
    const data = await this.client.listProjects();
    return data.projects.map((project) => mapProjectSummary(project, this.sidecar));
  }

  private workspacePath(): string {
    return readHostContext()?.workspacePath?.trim() ?? "";
  }

  async listProjects(): Promise<Project[]> {
    return this.projectIdList();
  }

  async getProject(projectId: string): Promise<Project | null> {
    const pending = this.pendingProjects.get(projectId);
    if (pending) {
      return pending;
    }
    const load = this.loadProject(projectId).finally(() => {
      this.pendingProjects.delete(projectId);
    });
    this.pendingProjects.set(projectId, load);
    return load;
  }

  private async loadProject(projectId: string): Promise<Project | null> {
    // One CLI process builds the whole read model. Calling task.show/plan.show/
    // evidence.list/showReport per entity made navigation take seconds.
    const snapshot = await this.client.projectSnapshot(projectId);
    const runDetails = snapshot.runs;
    const taskRuns = new Map<string, RunDetail[]>();
    runDetails.forEach((detail) => {
      if (detail.run.kind === "project_check") {
        return;
      }
      const taskId = detail.task?.task_id ?? detail.plan?.task_id ?? "";
      if (!taskId) {
        return;
      }
      const list = taskRuns.get(taskId) ?? [];
      list.push(detail);
      taskRuns.set(taskId, list);
    });
    const evidence = new Map<string, EvidenceRef[]>(
      Object.entries(snapshot.evidence).map(([runId, items]) => [
        runId,
        items.map(mapEvidence),
      ]),
    );
    const runDetailMap = new Map<string, RunDetail>(
      runDetails.map((detail) => [detail.run.run_id, detail]),
    );
    const project = mapProject({
      project: snapshot.project,
      modules: snapshot.modules,
      map: snapshot.map,
      mapStale: snapshot.map_stale,
      mapStaleReason: "",
      config: snapshot.config,
      rules: snapshot.rules,
      tasks: snapshot.tasks.map((bundle) => ({
        ...bundle,
        runs: taskRuns.get(bundle.task.task_id) ?? [],
      })),
      reports: snapshot.reports,
      issues: snapshot.issues,
      runDetails: runDetailMap,
      evidence,
      sidecar: this.sidecar,
    });
    if (!this.sidecar.analysis[projectId]) {
      project.analysisStatus = project.map || project.tasks.length ? "ready" : "idle";
    }
    const quickRuns = runDetails
      .filter((detail) => detail.run.kind === "project_check")
      .map((detail) => mapQuickCheck(detail, projectId));
    const known = new Set(project.quickChecks.map((entry) => entry.id));
    project.quickChecks = [
      ...quickRuns.filter((entry) => !known.has(entry.id)),
      ...project.quickChecks,
    ];
    return project;
  }

  async createProject(input: CreateProjectInput): Promise<Project> {
    const created = await this.client.addProject(input.root, input.name);
    this.sidecar.setAnalysis(created.project_id, {
      status: "analyzing",
      error: "",
      candidates: [],
    });
    const project = await this.getProject(created.project_id);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目登记后无法读取。");
    }
    return project;
  }

  async updateProject(
    projectId: string,
    patch: Partial<Pick<Project, "name" | "goal" | "archived">>,
  ): Promise<Project> {
    await this.client.updateProject(projectId, {
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.goal !== undefined ? { goal: patch.goal } : {}),
    });
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return project;
  }

  async saveAnalysis(projectId: string): Promise<Project> {
    const mapData = await this.client.refreshProjectMap(projectId);
    let candidates: Candidate[] = [];
    let fallback: { title: string; intent: "smoke" | "business" } | null = null;
    try {
      const suggested = await this.client.suggestTargets(projectId);
      candidates = suggested.candidates.map((candidate, index) =>
        mapCandidate(candidate, projectId, index),
      );
      if (suggested.fallback) {
        fallback = {
          title: suggested.fallback,
          intent: suggested.fallback_intent ?? "smoke",
        };
      }
    } catch {
      candidates = [];
      fallback = { title: "验证项目能否启动并跑通主流程", intent: "smoke" };
    }
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    if (candidates.length === 0 && fallback) {
      candidates = [
        mapCandidate(
          {
            title: fallback.title,
            reason: "项目信号不足时的通用目标",
            source: "fallback",
            intent: fallback.intent,
            scope_preview: {
              business_functions: [],
              acceptance_items_estimate: project.catalog.filter(
                (item) => item.observable && item.status === "active",
              ).length,
            },
            uncertainties: ["项目信号不足，需要人工补充范围"],
          },
          projectId,
          0,
        ),
      ];
    }
    this.sidecar.setAnalysis(projectId, {
      status: "ready",
      error: "",
      candidates,
    });
    // 当前对象在 sidecar 写入前加载；同步状态，避免调用方继续显示“分析中”。
    project.analysisStatus = "ready";
    project.analysisError = "";
    project.candidates = candidates;
    if (!mapData.map) {
      project.map = null;
    }
    return project;
  }

  async beginAnalysis(projectId: string): Promise<Project> {
    this.sidecar.setAnalysis(projectId, {
      status: "analyzing",
      error: "",
      candidates: this.sidecar.analysis[projectId]?.candidates ?? [],
    });
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return project;
  }

  async failAnalysis(projectId: string, message: string): Promise<Project> {
    this.sidecar.setAnalysis(projectId, {
      status: "failed",
      error: message,
      candidates: [],
    });
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return project;
  }

  async ensureDraftTask(): Promise<Task> {
    throw new RepositoryError(
      "CONFLICT",
      "宿主模式没有草稿任务；确认目标后会直接调用 CLI 创建任务。",
    );
  }

  async createTask(projectId: string, input: CreateTaskInput): Promise<Task> {
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    const candidate = this.sidecar.analysis[projectId]?.candidates.find(
      (entry) => entry.id === input.candidateId,
    );
    const fallbackCatalog: AcceptanceItem[] =
      project.catalog.length > 0
        ? project.catalog
        : [
            {
              id: "ai_goal",
              no: "01",
              text: input.goal,
              observable: true,
              status: "active",
              source: "任务目标",
              moduleId: null,
              moduleName: "未归类",
              featureName: "任务目标",
            },
          ];
    const selected = candidate
      ? fallbackCatalog.filter((item) => candidate.featureNames.includes(item.featureName))
      : fallbackCatalog;
    const chosen = selected.length > 0 ? selected : fallbackCatalog;
    const created = await this.client.addTask({
      projectId,
      title: input.goal,
      goal: input.goal,
      intent: candidate?.intent ?? "business",
      acceptanceItems: chosen.map((item) => ({
        text: item.text,
        observable: item.observable,
        source: item.source,
      })),
    });
    const itemIds = created.acceptance_items
      .filter((item) => item.status === "active")
      .map((item) => item.item_id);
    this.sidecar.setScope(created.task_id, itemIds);
    this.sidecar.setRecommended(created.task_id, itemIds);
    this.sidecar.setActiveTask(projectId, created.task_id);
    const refreshed = await this.getProject(projectId);
    const task = refreshed?.tasks.find((entry) => entry.id === created.task_id);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务创建后无法读取。");
    }
    return task;
  }

  async updateTask(projectId: string, taskId: string, patch: TaskPatch): Promise<Task> {
    const project = await this.getProject(projectId);
    const task = project?.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    const hostPatch: Record<string, unknown> = {};
    if (patch.title !== undefined) hostPatch.title = patch.title;
    if (patch.goal !== undefined) hostPatch.goal = patch.goal;
    if (patch.intent !== undefined) hostPatch.intent = patch.intent;
    if (patch.scope !== undefined) hostPatch.scope = patch.scope;
    if (patch.inputs !== undefined) hostPatch.inputs = patch.inputs;
    if (patch.outputs !== undefined) hostPatch.outputs = patch.outputs;
    if (patch.preconditions !== undefined) hostPatch.preconditions = patch.preconditions;
    if (patch.executor !== undefined) hostPatch.executor = patch.executor;
    if (patch.reviewer !== undefined) hostPatch.reviewer = patch.reviewer;
    if (patch.acceptanceItems !== undefined) {
      hostPatch.acceptance_items = patch.acceptanceItems.map((item) => ({
        ...(item.id ? { item_id: item.id } : {}),
        text: item.text,
        observable: item.observable,
        status: item.status,
        source: item.source,
        module_id: item.moduleId,
      }));
    }
    await this.client.saveTask({
      projectId,
      taskId,
      revision: task.revision,
      patch: hostPatch,
    });
    const refreshed = await this.getProject(projectId);
    const updated = refreshed?.tasks.find((entry) => entry.id === taskId);
    if (!updated) {
      throw new RepositoryError("NOT_FOUND", "任务更新后无法读取。");
    }
    const ids = new Set(updated.acceptanceItems.map((item) => item.id));
    this.sidecar.setScope(
      taskId,
      (this.sidecar.scopes[taskId] ?? []).filter((id) => ids.has(id)),
    );
    return updated;
  }

  async selectScope(projectId: string, taskId: string, itemIds: string[]): Promise<Task> {
    this.sidecar.setScope(taskId, itemIds);
    const project = await this.getProject(projectId);
    const task = project?.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    return task;
  }

  async reviseScope(projectId: string, taskId: string): Promise<Task> {
    const project = await this.getProject(projectId);
    const task = project?.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    if (task.stage === "planned" || task.stage === "result") {
      this.sidecar.setStage(taskId, {
        stage: "scope",
        previousStage: task.stage,
      });
    }
    const refreshed = await this.getProject(projectId);
    const updated = refreshed?.tasks.find((entry) => entry.id === taskId);
    if (!updated) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    return updated;
  }

  async cancelRevision(projectId: string, taskId: string): Promise<Task> {
    this.sidecar.setStage(taskId, null);
    const project = await this.getProject(projectId);
    const task = project?.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    return task;
  }

  async prepareRerun(projectId: string, taskId: string, itemIds?: string[]): Promise<Plan> {
    const project = await this.getProject(projectId);
    const task = project?.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    const target =
      itemIds && itemIds.length > 0
        ? itemIds
        : task.selectedItemIds.filter((itemId) => {
            const outcome = task.runs[0]?.items.find((item) => item.itemId === itemId)?.outcome;
            return outcome !== "passed";
          });
    const selected = target.length > 0 ? target : task.selectedItemIds;
    if (selected.length === 0) {
      throw new RepositoryError("SCOPE_EMPTY", "至少选择 1 项才能准备重跑。");
    }
    this.sidecar.setScope(taskId, selected);
    this.sidecar.setStage(taskId, null);
    const generated = await this.client.generatePlan({
      projectId,
      taskId,
      preset: "selected",
      moduleIds: [],
      acceptanceItemIds: selected,
    });
    const refreshed = await this.getProject(projectId);
    const plan = refreshed?.tasks
      .find((entry) => entry.id === taskId)
      ?.plans.find((entry) => entry.id === generated.plan_id);
    if (!plan) {
      throw new RepositoryError("NOT_FOUND", "计划生成后无法读取。");
    }
    return plan;
  }

  async setActiveTask(projectId: string, taskId: string): Promise<Project> {
    this.sidecar.setActiveTask(projectId, taskId);
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return project;
  }

  async generatePlan(projectId: string, taskId: string): Promise<Plan> {
    const project = await this.getProject(projectId);
    const task = project?.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    const selected = task.selectedItemIds;
    if (selected.length === 0 && task.intent !== "smoke") {
      throw new RepositoryError("SCOPE_EMPTY", "至少选择 1 项才能生成计划。");
    }
    const generated = await this.client.generatePlan({
      projectId,
      taskId,
      preset: selected.length > 0 ? "selected" : "all_applicable",
      moduleIds: [],
      acceptanceItemIds: selected,
    });
    this.sidecar.setStage(taskId, null);
    const refreshed = await this.getProject(projectId);
    const plan = refreshed?.tasks
      .find((entry) => entry.id === taskId)
      ?.plans.find((entry) => entry.id === generated.plan_id);
    if (!plan) {
      throw new RepositoryError("NOT_FOUND", "计划生成后无法读取。");
    }
    return plan;
  }

  async confirmPlan(
    projectId: string,
    taskId: string,
    planId: string,
    input: ConfirmPlanInput,
  ): Promise<Confirmation> {
    const project = await this.getProject(projectId);
    const plan = project?.tasks
      .find((task) => task.id === taskId)
      ?.plans.find((entry) => entry.id === planId);
    if (!plan) {
      throw new RepositoryError("NOT_FOUND", "计划不存在。");
    }
    const confirmation: Confirmation = {
      id: `conf_host_${plan.id}`,
      planId: plan.id,
      planRevision: plan.revision,
      acceptanceItemIds: [...plan.scope.acceptanceItemIds],
      environment: {},
      credentials: { source: "CLI", provided: "host" },
      sideEffects: input.sideEffects,
      dependencies: [...new Set(plan.cases.flatMap((testCase) => testCase.dependencies))],
      fingerprint: `host:${plan.sourceSignature}`,
      confirmedBy: input.operator,
      confirmedAt: isoNow(),
    };
    this.sidecar.setConfirmation(plan.id, confirmation);
    return confirmation;
  }

  async startRun(projectId: string, taskId: string, planId: string): Promise<Run> {
    const project = await this.getProject(projectId);
    const plan = project?.tasks
      .find((task) => task.id === taskId)
      ?.plans.find((entry) => entry.id === planId);
    if (!plan) {
      throw new RepositoryError("NOT_FOUND", "计划不存在。");
    }
    const started = await this.client.startRun(
      projectId,
      planId,
      plan.scope.acceptanceItemIds,
    );
    this.sidecar.setStage(taskId, null);
    const run = await this.getRun(projectId, started.run_id);
    if (!run) {
      throw new RepositoryError("NOT_FOUND", "运行启动后无法读取。");
    }
    return run;
  }

  async getRun(projectId: string, runId: string): Promise<Run | null> {
    const detail = await this.client.runStatus(projectId, runId);
    const evidenceData = await this.client
      .listEvidence(projectId, runId)
      .catch(() => ({ evidence: [] }));
    const evidence = evidenceData.evidence.map(mapEvidence);
    return mapRun(detail, hostTaskItems(detail.task), evidence);
  }

  async recordRunCase(): Promise<Run> {
    throw new RepositoryError(
      "CONFLICT",
      "宿主模式的用例结果由 CLI worker 写入，面板不能直接改。",
    );
  }

  async completeRun(projectId: string, runId: string): Promise<Run> {
    const run = await this.getRun(projectId, runId);
    if (!run) {
      throw new RepositoryError("NOT_FOUND", "运行不存在。");
    }
    return run;
  }

  async cancelRun(projectId: string, runId: string): Promise<Run> {
    await this.client.cancelRun(projectId, runId);
    const run = await this.getRun(projectId, runId);
    if (!run) {
      throw new RepositoryError("NOT_FOUND", "运行不存在。");
    }
    return run;
  }

  async markRunInterrupted(projectId: string, runId: string): Promise<Run> {
    const run = await this.getRun(projectId, runId);
    if (!run) {
      throw new RepositoryError("NOT_FOUND", "运行不存在。");
    }
    return run;
  }

  async recordManual(
    projectId: string,
    runId: string,
    caseId: string,
    input: ManualResultInput,
  ): Promise<Run> {
    await this.client.recordManual(
      projectId,
      runId,
      caseId,
      "面板记录人工结果",
      input.result,
      input.note,
      input.evidence.map((entry) => entry.sourcePath).filter(Boolean),
    );
    const run = await this.getRun(projectId, runId);
    if (!run) {
      throw new RepositoryError("NOT_FOUND", "运行不存在。");
    }
    return run;
  }

  async addEvidence(): Promise<Run> {
    throw new RepositoryError(
      "CONFLICT",
      "宿主模式需要绝对文件路径；浏览器文件选择器不能提供路径。",
      "在 CLI 执行 evidence.add，或使用证据引用列表。",
    );
  }

  async createIssue(projectId: string, input: IssueCreateInput): Promise<Issue> {
    const before = await this.getProject(projectId);
    const task = before?.tasks.find((entry) => entry.id === input.taskId);
    const run =
      task?.runs.find((entry) => entry.id === task.currentRunId) ?? task?.runs[0] ?? null;
    const runCase =
      input.itemId && run
        ? (run.cases.find((testCase) => testCase.itemId === input.itemId) ?? null)
        : null;
    const created = await this.client.addIssue({
      projectId,
      title: input.title,
      severity: input.severity,
      ...(run ? { runId: run.id } : {}),
      ...(runCase ? { caseId: runCase.caseId } : {}),
    });
    const project = await this.getProject(projectId);
    const issue = project?.issues.find((entry) => entry.id === created.issue_id);
    if (!issue) {
      throw new RepositoryError("NOT_FOUND", "问题创建后无法读取。");
    }
    return issue;
  }

  async updateIssue(projectId: string, issueId: string, patch: IssuePatch): Promise<Issue> {
    await this.client.updateIssue(projectId, issueId, {
      ...(patch.status ? { status: patch.status } : {}),
      ...(patch.reason ? { reason: patch.reason } : {}),
      ...(patch.disposition !== undefined ? { disposition: patch.disposition } : {}),
      ...(patch.fixVersion ? { fix_version: patch.fixVersion } : {}),
      ...(patch.severity ? { severity: patch.severity } : {}),
    });
    const project = await this.getProject(projectId);
    const issue = project?.issues.find((entry) => entry.id === issueId);
    if (!issue) {
      throw new RepositoryError("NOT_FOUND", "问题更新后无法读取。");
    }
    return issue;
  }

  async closeIssue(
    projectId: string,
    issueId: string,
    runId: string,
  ): Promise<Issue> {
    await this.client.closeIssue(projectId, issueId, runId);
    const project = await this.getProject(projectId);
    const issue = project?.issues.find((entry) => entry.id === issueId);
    if (!issue) {
      throw new RepositoryError("NOT_FOUND", "问题关闭后无法读取。");
    }
    return issue;
  }

  async saveReport(): Promise<Report> {
    throw new RepositoryError("CONFLICT", "宿主模式请使用重新生成报告。");
  }

  async regenerateReport(projectId: string, runId: string): Promise<Report> {
    const generated = await this.client.generateReport(projectId, runId);
    const project = await this.getProject(projectId);
    const report = project?.reports
      .filter((entry) => entry.runId === runId)
      .find((entry) => entry.id === generated.report_id);
    if (!report) {
      throw new RepositoryError("NOT_FOUND", "报告生成后无法读取。");
    }
    return report;
  }

  async saveOpinion(
    projectId: string,
    reportId: string,
    by: string,
    text: string,
  ): Promise<Report> {
    await this.client.saveOpinion({ projectId, reportId, by, text });
    const project = await this.getProject(projectId);
    const report = project?.reports.find((entry) => entry.id === reportId);
    if (!report) {
      throw new RepositoryError("NOT_FOUND", "报告不存在。");
    }
    return report;
  }

  async exportReport(projectId: string, reportId: string): Promise<ExportResult> {
    const workspace = this.workspacePath();
    if (!workspace) {
      throw new RepositoryError("VALIDATION", "宿主没有提供工作空间路径。");
    }
    const path = `${workspace}/exports/${reportId}.md`;
    const result = await this.client.exportReport(projectId, reportId, path);
    return {
      filename: basename(result.path),
      bytes: result.bytes,
      lines: result.lines,
      sha256: result.sha256,
      markdown: "",
      path: result.path,
    };
  }

  async saveDelivery(
    projectId: string,
    taskId: string,
    input: DeliveryInput,
  ): Promise<Task> {
    await this.client.saveDelivery({
      projectId,
      taskId,
      commit: input.commit,
      done: input.done,
      not_done: input.notDone,
      changed_modules: input.changedModules,
      interface_changes: input.interfaceChanges,
      run_commands: input.runCommands,
      test_data: input.testData,
      dependencies: input.dependencies,
      mocks: input.mocks,
      self_test_evidence: input.selfTestEvidence,
    });
    const project = await this.getProject(projectId);
    const task = project?.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    return task;
  }

  async draftDelivery(projectId: string, taskId: string): Promise<Delivery> {
    const data = await this.client.draftDelivery(projectId, taskId);
    return data.draft
      ? mapDraft(data.draft, taskId)
      : {
          id: `draft_${taskId}`,
          taskId,
          done: [],
          notDone: [],
          changedModules: [],
          interfaceChanges: [],
          runCommands: [],
          testData: "",
          dependencies: [],
          mocks: [],
          selfTestEvidence: [],
          commit: "",
          complete: false,
          revision: 1,
          updatedAt: isoNow(),
        };
  }

  async applyTemplate(projectId: string, taskId: string, templateId: string): Promise<Task> {
    const data = await this.client.applyTemplate({
      projectId,
      taskId,
      templateName: templateId,
    });
    const existing = this.sidecar.templates[taskId] ?? [];
    this.sidecar.setTemplates(taskId, [
      ...existing.filter((entry) => entry.templateId !== data.template.template_id),
      mapTemplateResult(data.template),
    ]);
    const project = await this.getProject(projectId);
    const task = project?.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    return task;
  }

  async saveRule(projectId: string, input: RuleInput): Promise<RuleVersion> {
    const saved = await this.client.saveRule(projectId, {
      name: input.name,
      version: input.version,
      steps_md: input.stepsMd,
      ai_rules: input.aiRules,
      evidence_requirements: input.evidenceRequirements,
    });
    const rule = await this.client.showRule(projectId, saved.rules_version_id);
    return {
      id: rule.rule.rules_version_id,
      projectId,
      name: rule.rule.name,
      version: rule.rule.version,
      stepsMd: rule.rule.steps_md,
      aiRules: [...rule.rule.ai_rules],
      evidenceRequirements: [...rule.rule.evidence_requirements],
      revision: rule.rule.revision,
      createdAt: isoNow(),
    };
  }

  async bindRule(projectId: string, ruleId: string): Promise<Project> {
    await this.client.bindRule(projectId, ruleId);
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return project;
  }

  async saveConfig(projectId: string, config: ProjectConfig): Promise<Project> {
    await this.client.saveProjectConfig(projectId, toHostConfig(config));
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return project;
  }

  async saveGithubSnapshot(
    projectId: string,
    snapshot: GithubSnapshot | null,
  ): Promise<Project> {
    this.sidecar.setGithub(projectId, snapshot);
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return project;
  }

  async addModule(projectId: string, input: ModuleInput): Promise<Module> {
    const created = await this.client.addModule(projectId, {
      name: input.name,
      responsibility: input.responsibility,
      owner: input.owner,
      deps: input.dependencies,
    });
    const project = await this.getProject(projectId);
    const module = project?.modules.find((entry) => entry.id === created.module_id);
    if (!module) {
      throw new RepositoryError("NOT_FOUND", "模块创建后无法读取。");
    }
    return module;
  }

  async updateModule(
    projectId: string,
    moduleId: string,
    patch: Partial<ModuleInput>,
  ): Promise<Module> {
    await this.client.updateModule(projectId, moduleId, {
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.responsibility !== undefined
        ? { responsibility: patch.responsibility }
        : {}),
      ...(patch.owner !== undefined ? { owner: patch.owner } : {}),
      ...(patch.status !== undefined ? { status: patch.status } : {}),
      ...(patch.dependencies !== undefined ? { deps: patch.dependencies } : {}),
    });
    const project = await this.getProject(projectId);
    const module = project?.modules.find((entry) => entry.id === moduleId);
    if (!module) {
      throw new RepositoryError("NOT_FOUND", "模块更新后无法读取。");
    }
    return module;
  }

  async setFeatureIgnored(
    projectId: string,
    featureName: string,
    ignored: boolean,
  ): Promise<Project> {
    const set = new Set(this.sidecar.ignored[projectId] ?? []);
    if (ignored) {
      set.add(featureName);
    } else {
      set.delete(featureName);
    }
    this.sidecar.setIgnored(projectId, [...set]);
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return project;
  }

  async importLink(projectId: string, runId: string): Promise<Run> {
    await this.client.importLink(projectId, runId);
    const run = await this.getRun(projectId, runId);
    if (!run) {
      throw new RepositoryError("NOT_FOUND", "运行不存在。");
    }
    return run;
  }

  async assertLink(
    projectId: string,
    runId: string,
    input: LinkAssertInput,
  ): Promise<LinkAssertion> {
    const result = await this.client.assertLink(
      projectId,
      runId,
      input.nodeId,
      input.expected,
      input.actual,
      input.operator,
    );
    return {
      nodeId: input.nodeId,
      expected: input.expected,
      actual: input.actual,
      result: result.result,
      operator: input.operator,
    };
  }

  async recordQuickCheck(projectId: string, quick: QuickCheck): Promise<Project> {
    this.sidecar.addQuickCheck(projectId, quick);
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return project;
  }

  async runQuickCheck(projectId: string): Promise<QuickCheck> {
    const started = await this.client.runQuick(projectId);
    const deadline = Date.now() + 120_000;
    let detail: RunDetail | null = null;
    while (Date.now() < deadline) {
      detail = await this.client.runStatus(projectId, started.run_id);
      if (detail.run.status !== "running") {
        break;
      }
      await delay(600);
    }
    if (!detail) {
      throw new RepositoryError("NOT_FOUND", "快速检查运行不存在。");
    }
    const quick = mapQuickCheck(detail, projectId);
    this.sidecar.addQuickCheck(projectId, quick);
    return quick;
  }

  async readGithub(projectId: string, repo: string, ref: string): Promise<GithubSnapshot> {
    const data = await this.client.readGithub(projectId, repo, ref);
    const snapshot = mapGithub(data as unknown as Record<string, unknown>);
    this.sidecar.setGithub(projectId, snapshot);
    return snapshot;
  }

  async listReports(projectId: string, runId: string): Promise<Report[]> {
    const [project, versions] = await Promise.all([
      this.getProject(projectId),
      this.client.listReports(projectId, runId),
    ]);
    const run = project?.tasks
      .flatMap((task) => task.runs)
      .find((entry) => entry.id === runId);
    const task = project?.tasks.find((entry) => entry.id === run?.taskId);
    const plan = task?.plans.find((entry) => entry.id === run?.planId) ?? null;
    const evidence = run?.evidence ?? [];
    const rulesLabel =
      project?.rules.find((entry) => entry.id === project.rulesVersionId)?.name ?? "未绑定规则";
    const results: Report[] = [];
    for (const version of versions.reports) {
      const shown = await this.client.showReport(projectId, version.report_id);
      results.push(
        mapReport(
          shown.report,
          projectId,
          task?.id ?? "",
          plan,
          run ?? null,
          evidence,
          rulesLabel,
        ),
      );
    }
    return results;
  }

  async getReport(projectId: string, reportId: string): Promise<Report | null> {
    const project = await this.getProject(projectId);
    const existing = project?.reports.find((entry) => entry.id === reportId);
    if (existing) {
      return existing;
    }
    const shown = await this.client.showReport(projectId, reportId);
    const run = project?.tasks
      .flatMap((task) => task.runs)
      .find((entry) => entry.id === shown.report.run_id);
    const task = project?.tasks.find((entry) => entry.id === run?.taskId);
    const plan = task?.plans.find((entry) => entry.id === run?.planId) ?? null;
    return mapReport(
      shown.report,
      projectId,
      task?.id ?? "",
      plan,
      run ?? null,
      run?.evidence ?? [],
      "未绑定规则",
    );
  }

  async backup(): Promise<BackupPayload> {
    const workspace = this.workspacePath();
    if (!workspace) {
      throw new RepositoryError("VALIDATION", "宿主没有提供工作空间路径。");
    }
    const path = `${workspace}/backups/aitest-${new Date().toISOString().slice(0, 10)}.zip`;
    const result = await this.client.createBackup(path, false);
    const info: BackupResult = {
      filename: basename(result.path),
      bytes: result.bytes,
      projects: 0,
      includesEvidence: result.includes_evidence,
      format: "cli",
      path: result.path,
    };
    return { data: new Uint8Array(), result: info };
  }

  async restore(): Promise<Project[]> {
    throw new RepositoryError(
      "CONFLICT",
      "宿主模式的备份恢复需要绝对路径。",
      "在设置中填写备份路径，或使用 CLI backup.restore。",
    );
  }

  async clear(): Promise<void> {
    throw new RepositoryError(
      "CONFLICT",
      "宿主模式不能从面板清空 CLI 工作空间。",
      "在 CLI 中显式清理工作空间。",
    );
  }

  async getLastProjectId(): Promise<string | null> {
    return this.sidecar.lastProjectId || null;
  }

  async setLastProjectId(projectId: string): Promise<void> {
    this.sidecar.setLastProjectId(projectId);
  }
}
