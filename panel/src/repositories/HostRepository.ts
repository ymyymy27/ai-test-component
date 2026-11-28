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
  ProjectConvention,
  QuickCheck,
  Report,
  Run,
  Task,
} from "@/domain/types";
import { RepositoryError } from "@/repositories/errors";
import type {
  BackupPayload,
  ConfirmPlanInput,
  ConventionInput,
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
  TaskPatch,
} from "@/repositories/ProjectRepository";
import { HostError, readHostContext } from "@/host/bridge";
import { api, type Api } from "@/host/commands";
import type {
  DeliveryDraft,
  EvidencePreview,
  PlanDetail,
  RunDetail,
  RunLogTailEntry,
  Task as TaskDto,
} from "@/host/dto";
import {
  buildCatalogFromFeatures,
  mapCandidate,
  mapConfig,
  mapConfirmation,
  mapConventions,
  mapEvidence,
  mapGithub,
  mapProject,
  mapProjectSummary,
  mapQuickCheck,
  mapReport,
  mapRun,
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
  private readonly projectCache = new Map<string, Project>();

  constructor(private readonly client: Api = api) {}

  cachedProject(projectId: string): Project | null {
    return this.projectCache.get(projectId) ?? null;
  }

  private async cachedOrLoad(projectId: string): Promise<Project> {
    const cached = this.projectCache.get(projectId);
    if (cached) {
      return cached;
    }
    const loaded = await this.getProject(projectId);
    if (!loaded) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return loaded;
  }

  private updateCachedTask(projectId: string, project: Project, updatedTask: Task): Project {
    const tasks = project.tasks.map((entry) => (entry.id === updatedTask.id ? updatedTask : entry));
    const next: Project = { ...project, tasks };
    this.projectCache.set(projectId, next);
    return next;
  }

  /** 当前 CLI 工作空间；面板顶部据此说明“项目为何不在这里”。 */
  workspacePath(): string {
    return readHostContext()?.workspacePath?.trim() ?? "";
  }

  async listProjects(): Promise<Project[]> {
    const data = await this.client.listProjects();
    const projects = data.projects.map((project) => mapProjectSummary(project, this.sidecar));
    this.sidecar.pruneProjects(projects.map((project) => project.id));
    return projects;
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
    const knownTaskIds = new Set(snapshot.tasks.map((bundle) => bundle.task.task_id));
    const taskRuns = new Map<string, RunDetail[]>();
    const orphanRuns: RunDetail[] = [];
    runDetails.forEach((detail) => {
      if (detail.run.kind === "project_check") {
        return;
      }
      const taskId = detail.task?.task_id ?? detail.plan?.task_id ?? "";
      if (!taskId || !knownTaskIds.has(taskId)) {
        orphanRuns.push(detail);
        return;
      }
      const list = taskRuns.get(taskId) ?? [];
      list.push(detail);
      taskRuns.set(taskId, list);
    });
    const taskBundles = snapshot.tasks.map((bundle) => ({
      ...bundle,
      runs: taskRuns.get(bundle.task.task_id) ?? [],
    }));
    if (orphanRuns.length > 0) {
      const archivedPlans = new Map<string, PlanDetail>();
      const archivedItems = new Map<string, TaskDto["acceptance_items"][number]>();
      orphanRuns.forEach((detail) => {
        if (detail.plan && !archivedPlans.has(detail.plan.plan_id)) {
          archivedPlans.set(detail.plan.plan_id, {
            plan: detail.plan,
            coverage: {
              selected: detail.plan.scope.acceptance_item_ids.length,
              applicable: detail.plan.applicable_count,
            },
            stale: false,
            stale_reason: "",
          });
        }
        detail.task?.acceptance_items.forEach((item) => {
          archivedItems.set(item.item_id, item);
        });
        Object.entries(detail.plan?.item_texts ?? {}).forEach(([itemId, text]) => {
          if (!archivedItems.has(itemId)) {
            archivedItems.set(itemId, {
              item_id: itemId,
              text,
              observable: true,
              status: "active",
            });
          }
        });
        detail.run.scope.acceptance_item_ids.forEach((itemId) => {
          if (!archivedItems.has(itemId)) {
            archivedItems.set(itemId, {
              item_id: itemId,
              text: itemId,
              observable: true,
              status: "active",
            });
          }
        });
      });
      taskBundles.push({
        task: {
          task_id: "__archived_runs__",
          project_id: projectId,
          title: "历史归档运行（原任务已移除）",
          goal: "原任务或计划快照已缺失的历史运行记录，仅保留只读回溯。",
          intent: "business",
          origin: "manual",
          acceptance_items: [...archivedItems.values()],
          stage: "result",
          revision: 1,
        },
        plans: [...archivedPlans.values()],
        delivery: null,
        runs: orphanRuns,
      });
    }
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
      localGit: snapshot.local_git ?? null,
      codeGraph: snapshot.code_graph ?? null,
      capabilities: snapshot.capabilities ?? null,
      healthSummary: snapshot.health_summary ?? null,
      config: snapshot.config,
      tasks: taskBundles,
      reports: snapshot.reports,
      issues: snapshot.issues,
      runDetails: runDetailMap,
      evidence,
      github: snapshot.github ?? null,
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
    this.sidecar.prune(
      projectId,
      project.tasks.map((task) => task.id),
      project.tasks.flatMap((task) => task.plans.map((plan) => plan.id)),
    );
    this.projectCache.set(projectId, project);
    return project;
  }

  async pickDirectory(): Promise<string | null> {
    return this.client.pickDirectory();
  }

  async createProject(input: CreateProjectInput): Promise<Project> {
    const created = await this.client.addProject(input.root, input.name, input.goal);
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
      ...(patch.archived !== undefined ? { archived: patch.archived } : {}),
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
    } catch (error) {
      const message = error instanceof Error ? error.message : "分析失败";
      this.sidecar.setAnalysis(projectId, {
        status: "failed",
        error: message,
        candidates: [],
      });
      throw new RepositoryError(
        "MODEL_UNAVAILABLE",
        `项目分析失败：${message}`,
        "检查模型配置后重新分析；不会用通用候选代替。",
      );
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
    this.projectCache.set(projectId, project);
    return project;
  }

  async beginAnalysis(projectId: string): Promise<Project> {
    const current = await this.cachedOrLoad(projectId);
    const existingCandidates = this.sidecar.analysis[projectId]?.candidates ?? current.candidates;
    this.sidecar.setAnalysis(projectId, {
      status: "analyzing",
      error: "",
      candidates: existingCandidates,
    });
    const next: Project = {
      ...current,
      analysisStatus: "analyzing",
      analysisError: "",
      candidates: [...existingCandidates],
    };
    this.projectCache.set(projectId, next);
    return next;
  }

  async failAnalysis(projectId: string, message: string): Promise<Project> {
    const current = await this.cachedOrLoad(projectId);
    this.sidecar.setAnalysis(projectId, {
      status: "failed",
      error: message,
      candidates: [],
    });
    const next: Project = {
      ...current,
      analysisStatus: "failed",
      analysisError: message,
      candidates: [],
    };
    this.projectCache.set(projectId, next);
    return next;
  }

  async ensureDraftTask(): Promise<Task> {
    throw new RepositoryError(
      "CONFLICT",
      "宿主模式没有草稿任务；确认目标后会直接调用 CLI 创建任务。",
    );
  }

  async createTask(projectId: string, input: CreateTaskInput): Promise<Task> {
    const project = await this.cachedOrLoad(projectId);
    const candidate = input.candidateId
      ? (this.sidecar.analysis[projectId]?.candidates.find(
          (entry) => entry.id === input.candidateId,
        ) ?? null)
      : null;
    if (input.candidateId && !candidate) {
      throw new RepositoryError(
        "NOT_FOUND",
        "目标候选已失效。",
        "重新分析项目或改用自定义目标；不要静默退回全部验收项。",
      );
    }
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
    const featureSet = new Set(
      input.featureNames && input.featureNames.length > 0
        ? input.featureNames
        : candidate?.featureNames ?? [],
    );
    const selected = featureSet.size
      ? fallbackCatalog.filter((item) => featureSet.has(item.featureName))
      : fallbackCatalog;
    const chosen = selected.length > 0 ? selected : fallbackCatalog;
    const requirementParts: string[] = [];
    if (input.flowSteps?.length) {
      requirementParts.push(`业务链路：${input.flowSteps.join(" -> ")}`);
    }
    if (input.featureNames?.length) {
      requirementParts.push(`涉及功能：${input.featureNames.join("、")}`);
    }
    if (input.requirement?.trim()) {
      requirementParts.push(input.requirement.trim());
    }
    const requirementText = requirementParts.join("\n").trim();
    const created = await this.client.addTask({
      projectId,
      title: input.goal,
      goal: input.goal,
      requirement: requirementText || undefined,
      intent: candidate?.intent ?? "business",
      origin: input.origin ?? "goal",
      sourceMapRevision: project.map?.revision ?? 0,
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
    const project = await this.cachedOrLoad(projectId);
    const task = project.tasks.find((entry) => entry.id === taskId);
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
    if (this.sidecar.scopes[taskId] !== undefined) {
      const ids = new Set(updated.acceptanceItems.map((item) => item.id));
      const filtered = this.sidecar.scopes[taskId].filter((id) => ids.has(id));
      const applicableIds = updated.acceptanceItems
        .filter((item) => item.observable && item.status === "active")
        .map((item) => item.id);
      if (filtered.length === 0 && applicableIds.length > 0) {
        this.sidecar.clearScope(taskId);
        const latestPlanIds = updated.plans[0]?.scope.acceptanceItemIds.filter((id) =>
          applicableIds.includes(id),
        );
        const fallbackSelected =
          latestPlanIds && latestPlanIds.length > 0 ? latestPlanIds : applicableIds;
        const syncedTask: Task = { ...updated, selectedItemIds: fallbackSelected };
        this.updateCachedTask(projectId, refreshed!, syncedTask);
        return syncedTask;
      }
      this.sidecar.setScope(taskId, filtered);
      const syncedTask: Task = { ...updated, selectedItemIds: filtered };
      this.updateCachedTask(projectId, refreshed!, syncedTask);
      return syncedTask;
    }
    return updated;
  }

  async deleteTask(projectId: string, taskId: string): Promise<void> {
    await this.client.deleteTask(projectId, taskId);
    this.sidecar.setActiveTask(projectId, "");
    await this.getProject(projectId);
  }

  async selectScope(projectId: string, taskId: string, itemIds: string[]): Promise<Task> {
    const project = await this.cachedOrLoad(projectId);
    const task = project.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    const applicable = new Set(
      task.acceptanceItems
        .filter((item) => item.observable && item.status === "active")
        .map((item) => item.id),
    );
    const filtered = itemIds.filter((id) => applicable.has(id));
    this.sidecar.setScope(taskId, filtered);
    const updatedTask: Task = { ...task, selectedItemIds: filtered };
    this.updateCachedTask(projectId, project, updatedTask);
    return updatedTask;
  }

  async reviseScope(projectId: string, taskId: string): Promise<Task> {
    const project = await this.cachedOrLoad(projectId);
    const task = project.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    if (task.stage === "planned" || task.stage === "result") {
      const previousStage = task.stage;
      this.sidecar.setStage(taskId, {
        stage: "scope",
        previousStage,
      });
      const updatedTask: Task = { ...task, stage: "scope", previousStage };
      this.updateCachedTask(projectId, project, updatedTask);
      return updatedTask;
    }
    return task;
  }

  async cancelRevision(projectId: string, taskId: string): Promise<Task> {
    const project = await this.cachedOrLoad(projectId);
    const task = project.tasks.find((entry) => entry.id === taskId);
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。");
    }
    this.sidecar.setStage(taskId, null);
    const latestPlan = task.plans[0] ?? null;
    const latestRun = task.runs[0] ?? null;
    const hasRunning = task.runs.some((run) => run.status === "running");
    const restoredStage: Task["stage"] = hasRunning
      ? "running"
      : !latestPlan
        ? "scope"
        : !latestRun
          ? "planned"
          : latestPlan.createdAt > latestRun.startedAt
            ? "planned"
            : "result";
    const updatedTask: Task = {
      ...task,
      stage: restoredStage,
      previousStage: null,
    };
    this.updateCachedTask(projectId, project, updatedTask);
    return updatedTask;
  }

  async repairRunDependencies(
    projectId: string,
    runId: string,
    packages: string[],
    confirmedBy: string,
  ): Promise<Run> {
    await this.client.repairRunDependencies(projectId, runId, packages, confirmedBy);
    const project = await this.getProject(projectId);
    const run = project?.tasks
      .flatMap((entry) => entry.runs)
      .find((entry) => entry.id === runId);
    if (!run) {
      throw new RepositoryError("NOT_FOUND", "修复后无法读取运行记录。");
    }
    return run;
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
        : (() => {
            // 重跑必须针对正在查看的运行，而不是固定取最新运行。
            const current =
              task.runs.find((run) => run.id === task.currentRunId) ?? task.runs[0] ?? null;
            return task.selectedItemIds.filter((itemId) => {
              const outcome = current?.items.find((item) => item.itemId === itemId)?.outcome;
              return outcome !== "passed";
            });
          })();
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
    const project = await this.cachedOrLoad(projectId);
    this.sidecar.setActiveTask(projectId, taskId);
    const nextId = taskId && project.tasks.some((entry) => entry.id === taskId) ? taskId : null;
    const next: Project = { ...project, activeTaskId: nextId };
    this.projectCache.set(projectId, next);
    return next;
  }

  async generatePlan(projectId: string, taskId: string): Promise<Plan> {
    const project = await this.cachedOrLoad(projectId);
    const task = project.tasks.find((entry) => entry.id === taskId);
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
    const project = await this.cachedOrLoad(projectId);
    const plan = project.tasks
      .find((task) => task.id === taskId)
      ?.plans.find((entry) => entry.id === planId);
    if (!plan) {
      throw new RepositoryError("NOT_FOUND", "计划不存在。");
    }
    const confirmation: Confirmation = {
      id: `conf_ui_${plan.id}`,
      planId: plan.id,
      planRevision: plan.revision,
      acceptanceItemIds: [...plan.scope.acceptanceItemIds],
      // 这里只是确认前的界面状态，不冒充 CLI 记录：真实 environment/credentials
      // 由 run.start 生成，启动后用 CLI 回执覆盖。
      environment: {},
      credentials: {},
      sideEffects: input.sideEffects,
      dependencies: [...new Set(plan.cases.flatMap((testCase) => testCase.dependencies))],
      fingerprint: `ui:${plan.sourceSignature}`,
      confirmedBy: input.operator,
      confirmedAt: isoNow(),
    };
    this.sidecar.setConfirmation(plan.id, confirmation);
    return confirmation;
  }

  async startRun(
    projectId: string,
    taskId: string,
    planId: string,
    confirmedBy = "本机用户",
  ): Promise<Run> {
    return this.startRunScoped(projectId, taskId, planId, confirmedBy);
  }

  async startTargetedRerun(
    projectId: string,
    taskId: string,
    planId: string,
    itemIds: string[],
    confirmedBy = "本机用户",
  ): Promise<Run> {
    if (itemIds.length === 0) {
      throw new RepositoryError("SCOPE_EMPTY", "至少选择 1 项才能定向复测。");
    }
    return this.startRunScoped(projectId, taskId, planId, confirmedBy, itemIds);
  }

  private async startRunScoped(
    projectId: string,
    taskId: string,
    planId: string,
    confirmedBy = "本机用户",
    scopedItemIds?: string[],
  ): Promise<Run> {
    const project = await this.cachedOrLoad(projectId);
    const plan = project.tasks
      .find((task) => task.id === taskId)
      ?.plans.find((entry) => entry.id === planId);
    if (!plan) {
      throw new RepositoryError("NOT_FOUND", "计划不存在。");
    }
    const targetItems =
      scopedItemIds && scopedItemIds.length > 0 ? scopedItemIds : plan.scope.acceptanceItemIds;
    const started = await this.client.startRun(
      projectId,
      planId,
      targetItems,
      confirmedBy,
    );
    if (started.confirmation) {
      // 启动后以 CLI 生成的确认记录为审计事实，替换确认前的界面状态。
      this.sidecar.setConfirmation(plan.id, mapConfirmation(started.confirmation, plan));
    }
    this.sidecar.setStage(taskId, null);
    try {
      const run = await this.getRun(projectId, started.run_id);
      if (run) {
        return run;
      }
    } catch {
      // 启动已成功，读回失败不能算启动失败，否则重试会创建第二个运行。
    }
    const selected = targetItems;
    const confirmation = started.confirmation;
    return {
      id: started.run_id,
      projectId,
      taskId,
      taskTitle: project?.tasks.find((entry) => entry.id === taskId)?.title ?? "",
      planId: plan.id,
      kind: "task",
      status: "running",
      postprocess: "pending",
      startedAt: isoNow(),
      finishedAt: "",
      elapsedMs: 0,
      confirmedBy: confirmation?.confirmed_by ?? confirmedBy,
      confirmationId: confirmation?.confirmation_id ?? "",
      confirmationFingerprint: confirmation?.fingerprint ?? "",
      scope: { ...plan.scope, acceptanceItemIds: [...selected] },
      cases: [],
      l1: [],
      items: selected.map((itemId) => ({
        itemId,
        no: "",
        text: itemId,
        module: "",
        feature: "",
        outcome: "not_run",
        caseIds: [],
      })),
      evidence: [],
      link: null,
      verdict: "incomplete",
      counts: {
        selected: selected.length,
        applicable: plan.scope.coverage.applicable,
        covered: 0,
        passed: 0,
        failed: 0,
        incomplete: selected.length,
      },
      conclusion: "",
      reportIds: [],
      currentCaseId: "",
      simulated: false,
      heartbeatAt: isoNow(),
      cancelRequested: false,
      environment: {},
      dependencySuggestions: [],
      dependencyRepairs: [],
    };
  }

  async detectProjectConfig(projectId: string): Promise<ProjectConfig> {
    const res = await this.client.detectProjectConfig(projectId);
    return mapConfig(res.draft);
  }

  async previewEvidence(
    projectId: string,
    runId: string,
    ref: string,
  ): Promise<EvidencePreview | null> {
    const res = await this.client.previewEvidence(projectId, runId, ref);
    return res.preview ?? null;
  }

  async fetchRunLogTail(projectId: string, runId: string): Promise<RunLogTailEntry[]> {
    const detail = await this.client.runStatus(projectId, runId, true);
    return detail.log_tail ?? [];
  }

  async getRun(
    projectId: string,
    runId: string,
    includeEvidence = true,
  ): Promise<Run | null> {
    const detail = await this.client.runStatus(projectId, runId);
    const evidence = includeEvidence
      ? (
          await this.client.listEvidence(projectId, runId).catch(() => ({ evidence: [] }))
        ).evidence.map(mapEvidence)
      : [];
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
    if (typeof this.client.cancelRun === "function") {
      try {
        await this.client.cancelRun(projectId, runId, { markInterrupted: true });
      } catch (error) {
        if (!(error instanceof HostError && error.code === "RUN_STATE_CONFLICT")) {
          throw error;
        }
      }
    }
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
    const cachedProject = this.projectCache.get(projectId);
    const cachedTask = cachedProject?.tasks.find((task) =>
      task.runs.some((entry) => entry.id === runId),
    );
    const cachedRun = cachedTask?.runs.find((entry) => entry.id === runId);
    const cachedPlan = cachedTask?.plans.find((plan) => plan.id === cachedRun?.planId);
    let planSteps =
      cachedPlan?.cases.find((testCase) => testCase.id === caseId)?.steps ?? [];
    if (planSteps.length === 0) {
      const statusDetail = await this.client.runStatus(projectId, runId).catch(() => null);
      planSteps =
        statusDetail?.plan?.cases.find((testCase) => testCase.case_id === caseId)?.steps ?? [];
    }
    const evidencePaths = input.evidence
      .map((entry) => entry.sourcePath?.trim() ?? "")
      .filter(Boolean)
      .map((raw) => {
        const normalized = raw.replace(/\\/g, "/");
        const prefix = `runs/${runId}/`;
        return normalized.startsWith(prefix) ? normalized.slice(prefix.length) : raw;
      });
    const steps = planSteps.map((step) => step.trim()).filter(Boolean);
    const batchSteps =
      steps.length > 0
        ? steps.map((step, index) => ({
            step,
            result: input.result,
            note: input.note,
            ...(index === 0 && evidencePaths.length ? { evidence: evidencePaths } : {}),
          }))
        : [
            {
              step: "面板记录人工结果",
              result: input.result,
              note: input.note,
              ...(evidencePaths.length ? { evidence: evidencePaths } : {}),
            },
          ];
    await this.client.recordManualBatch(projectId, runId, caseId, batchSteps);
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
    if (!task) {
      throw new RepositoryError("NOT_FOUND", "任务不存在。", "刷新后重新选择任务。");
    }
    const runs = [...task.runs].sort((first, second) =>
      second.startedAt.localeCompare(first.startedAt),
    );
    const run = runs.find((entry) => entry.id === task.currentRunId) ?? runs[0] ?? null;
    if (!run) {
      throw new RepositoryError(
        "VALIDATION",
        "这个任务还没有运行记录，不能登记问题。",
        "先完成一次运行，再从结果页登记问题。",
      );
    }
    const runCase = input.itemId
      ? (run.cases.find((testCase) =>
          (testCase.caseItemIds ?? [testCase.itemId]).includes(input.itemId),
        ) ?? null)
      : (run.cases[0] ?? null);
    if (!runCase) {
      throw new RepositoryError(
        "VALIDATION",
        input.itemId
          ? "这次运行没有覆盖所选验收项，不能绑定问题。"
          : "这次运行没有用例记录，不能登记问题。",
        "选择覆盖该验收项的运行，或先重跑。",
      );
    }
    const created = await this.client.addIssue({
      projectId,
      title: input.title,
      severity: input.severity,
      runId: run.id,
      caseId: runCase.caseId,
      expected: input.expected,
      actual: input.actual,
      steps: input.steps,
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
      ...(patch.fixVersion !== undefined ? { fix_version: patch.fixVersion } : {}),
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
    reason: string,
  ): Promise<Issue> {
    await this.client.closeIssue(projectId, issueId, runId, reason);
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
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const path = `${workspace}/exports/${reportId}-${stamp}.md`;
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

  async saveConfig(projectId: string, config: ProjectConfig): Promise<Project> {
    await this.client.saveProjectConfig(projectId, toHostConfig(config));
    const project = await this.getProject(projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。");
    }
    return project;
  }

  async addConvention(projectId: string, input: ConventionInput): Promise<ProjectConvention[]> {
    const res = await this.client.addProjectConvention(projectId, {
      pattern: input.pattern,
      reason: input.reason,
      ...(input.matchType ? { match_type: input.matchType } : {}),
      ...(input.scope ? { scope: input.scope } : {}),
    });
    const list = mapConventions(res.conventions);
    const cached = this.projectCache.get(projectId);
    if (cached) {
      this.projectCache.set(projectId, {
        ...cached,
        config: { ...cached.config, conventions: list },
      });
    }
    return list;
  }

  async removeConvention(projectId: string, conventionId: string): Promise<ProjectConvention[]> {
    const res = await this.client.removeProjectConvention(projectId, conventionId);
    const list = mapConventions(res.conventions);
    const cached = this.projectCache.get(projectId);
    if (cached) {
      this.projectCache.set(projectId, {
        ...cached,
        config: { ...cached.config, conventions: list },
      });
    }
    return list;
  }

  async saveGithubSnapshot(
    projectId: string,
    snapshot: GithubSnapshot | null,
  ): Promise<Project> {
    const current = await this.cachedOrLoad(projectId);
    this.sidecar.setGithub(projectId, snapshot);
    if (snapshot?.commit?.trim() && typeof this.client.saveGithubSnapshot === "function") {
      await this.client.saveGithubSnapshot(projectId, {
        repo: snapshot.repo,
        branch: snapshot.branch,
        commit: snapshot.commit,
        files: snapshot.files,
        prs: snapshot.prs,
        checks: snapshot.checks,
        deployment: snapshot.deployment,
        account: snapshot.account,
        host: snapshot.host,
        fetched_at: snapshot.fetchedAt,
      });
    }
    const next: Project = { ...current, github: snapshot };
    this.projectCache.set(projectId, next);
    return next;
  }

  async addModule(projectId: string, input: ModuleInput): Promise<Module> {
    const created = await this.client.addModule(projectId, {
      name: input.name,
      responsibility: input.responsibility,
      owner: input.owner,
      status: input.status,
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
    const current = await this.cachedOrLoad(projectId);
    const set = new Set(current.map?.ignoredFeatures ?? this.sidecar.ignored[projectId] ?? []);
    if (ignored) {
      set.add(featureName);
    } else {
      set.delete(featureName);
    }
    const ignoredList = [...set];
    this.sidecar.setIgnored(projectId, ignoredList);
    if (typeof this.client.setIgnoredFeatures === "function") {
      await this.client.setIgnoredFeatures(projectId, ignoredList);
    }
    const nextCatalog = current.map
      ? buildCatalogFromFeatures(current.map.features, [], projectId, ignoredList)
      : current.catalog.filter((item) => !set.has(item.featureName));
    const nextCandidates = current.candidates.filter(
      (candidate) =>
        candidate.featureNames.length === 0 ||
        candidate.featureNames.some((name) => !set.has(name)),
    );
    const next: Project = {
      ...current,
      catalog: nextCatalog,
      candidates: nextCandidates,
      map: current.map ? { ...current.map, ignoredFeatures: ignoredList } : null,
    };
    this.projectCache.set(projectId, next);
    return next;
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
    const current = await this.cachedOrLoad(projectId);
    this.sidecar.addQuickCheck(projectId, quick);
    const next: Project = {
      ...current,
      quickChecks: [...(this.sidecar.quickChecks[projectId] ?? [quick])],
    };
    this.projectCache.set(projectId, next);
    return next;
  }

  async runQuickCheck(projectId: string): Promise<QuickCheck> {
    const started = await this.client.runQuick(projectId);
    // 构建/冒烟可能远超 2 分钟；轮询到终态（或 10 分钟后明确报“仍在运行”）。
    const deadline = Date.now() + 600_000;
    let detail: RunDetail | null = null;
    while (Date.now() < deadline) {
      detail = await this.client.runStatus(projectId, started.run_id);
      if (detail.run.status !== "running") {
        break;
      }
      await delay(1500);
    }
    if (!detail) {
      throw new RepositoryError("NOT_FOUND", "快速检查运行不存在。");
    }
    if (detail.run.status === "running") {
      throw new RepositoryError(
        "RUN_ACTIVE",
        "快速检查仍在运行，已停止等待",
        "稍后在项目概览刷新运行状态；不要重复启动",
      );
    }
    const quick = mapQuickCheck(detail, projectId);
    this.sidecar.addQuickCheck(projectId, quick);
    const cached = this.projectCache.get(projectId);
    if (cached) {
      this.projectCache.set(projectId, {
        ...cached,
        quickChecks: [...(this.sidecar.quickChecks[projectId] ?? [quick])],
      });
    }
    return quick;
  }

  async readGithub(projectId: string, repo: string, ref: string): Promise<GithubSnapshot> {
    const data = await this.client.readGithub(projectId, repo, ref);
    const snapshot = mapGithub(data as unknown as Record<string, unknown>);
    this.sidecar.setGithub(projectId, snapshot);
    return snapshot;
  }

  async listReports(projectId: string, runId: string): Promise<Report[]> {
    const cached = this.projectCache.get(projectId);
    const [project, versions] = await Promise.all([
      cached ? Promise.resolve(cached) : this.getProject(projectId),
      this.client.listReports(projectId, runId, true),
    ]);
    const run = project?.tasks
      .flatMap((task) => task.runs)
      .find((entry) => entry.id === runId);
    const task = project?.tasks.find((entry) => entry.id === run?.taskId);
    const plan = task?.plans.find((entry) => entry.id === run?.planId) ?? null;
    const evidence = run?.evidence ?? [];
    const results: Report[] = [];
    for (const version of versions.reports) {
      const reportDto =
        version.report ?? (await this.client.showReport(projectId, version.report_id)).report;
      results.push(
        mapReport(
          reportDto,
          projectId,
          task?.id ?? "",
          plan,
          run ?? null,
          evidence,
        ),
      );
    }
    return results;
  }

  async getReport(projectId: string, reportId: string): Promise<Report | null> {
    const project = await this.cachedOrLoad(projectId);
    const existing = project.reports.find((entry) => entry.id === reportId);
    if (existing) {
      return existing;
    }
    const shown = await this.client.showReport(projectId, reportId);
    const run = project.tasks
      .flatMap((task) => task.runs)
      .find((entry) => entry.id === shown.report.run_id);
    const task = project.tasks.find((entry) => entry.id === run?.taskId);
    const plan = task?.plans.find((entry) => entry.id === run?.planId) ?? null;
    return mapReport(
      shown.report,
      projectId,
      task?.id ?? "",
      plan,
      run ?? null,
      run?.evidence ?? [],
    );
  }

  async backup(): Promise<BackupPayload> {
    const workspace = this.workspacePath();
    if (!workspace) {
      throw new RepositoryError("VALIDATION", "宿主没有提供工作空间路径。");
    }
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const path = `${workspace}/backups/aitest-${stamp}.zip`;
    const result = await this.client.createBackup(path, false);
    const listed = await this.client.listProjects().catch(() => null);
    const info: BackupResult = {
      filename: basename(result.path),
      bytes: result.bytes,
      projects: listed?.projects.length ?? 0,
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
