import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";
import { applyTemplate as applyTemplateResult, type ProjectAnalysis } from "@/domain/analysis";
import {
  confirmationFingerprint,
  itemOutcome,
  issueCloseGate,
  recountRun,
  sideEffectsFor,
  sourceSignature,
  taskContract,
} from "@/domain/derive";
import {
  buildConfirmation,
  buildPlan,
  buildReport,
  buildRun,
  confirmDraftTask,
  createDraftTask,
  createIssueFromCase,
  createManualIssue,
  createProjectRecord,
  createRuleVersion,
  draftDelivery as buildDeliveryDraft,
} from "@/domain/factory";
import { isoNow, newId } from "@/domain/ids";
import {
  applyIssueUpdate,
  closeIssueRecord,
  mergeIssueOccurrence,
  signatureForCase,
} from "@/domain/issues";
import { assertTransition } from "@/domain/stateMachine";
import type {
  AcceptanceItem,
  BackupResult,
  Confirmation,
  Delivery,
  EvidenceRef,
  ExportResult,
  GithubSnapshot,
  Issue,
  LinkAssertion,
  LinkData,
  Module,
  Plan,
  Project,
  ProjectConfig,
  QuickCheck,
  Report,
  RuleVersion,
  Run,
  Task,
  WorkspaceEnvelope,
} from "@/domain/types";
import { validateGoal, validateProjectForm } from "@/domain/validation";
import { buildEnvironmentChecks } from "@/services/environmentService";
import { exportMetrics, reportMarkdown } from "@/services/exportService";
import { RepositoryError } from "./errors";
import type {
  BackupPayload,
  ConfirmPlanInput,
  CreateProjectInput,
  CreateTaskInput,
  DeliveryInput,
  EvidenceInput,
  IssueCreateInput,
  IssuePatch,
  LinkAssertInput,
  ManualResultInput,
  ModuleInput,
  ProjectRepository,
  RuleInput,
  RunCaseUpdate,
  TaskPatch,
} from "./ProjectRepository";

const STORAGE_KEY = "aitest.panel.v1";

function clone<T>(value: T): T {
  if (typeof structuredClone === "function") {
    try {
      return structuredClone(value);
    } catch {
      // Vue 响应式代理不能直接 structuredClone，退回 JSON 深拷贝。
    }
  }
  return JSON.parse(JSON.stringify(value)) as T;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function emptyEnvelope(): WorkspaceEnvelope {
  return {
    schemaVersion: 1,
    savedAt: isoNow(),
    lastProjectId: null,
    projects: [],
  };
}

function assertEnvelope(value: unknown): WorkspaceEnvelope {
  if (!isRecord(value) || value.schemaVersion !== 1 || !Array.isArray(value.projects)) {
    throw new RepositoryError(
      "FORMAT",
      "本地数据格式不受支持，无法读取。",
      "清空本地面板数据后重新建立项目；不做静默迁移。",
    );
  }
  const valid = value.projects.every(
    (project) =>
      isRecord(project) &&
      typeof project.id === "string" &&
      typeof project.name === "string" &&
      typeof project.root === "string" &&
      Array.isArray(project.tasks) &&
      Array.isArray(project.issues) &&
      Array.isArray(project.reports),
  );
  if (!valid) {
    throw new RepositoryError(
      "FORMAT",
      "本地项目记录不完整，无法读取。",
      "清空本地面板数据后重新建立项目；不做静默迁移。",
    );
  }
  return value as unknown as WorkspaceEnvelope;
}

function findTask(project: Project, taskId: string): Task {
  const task = project.tasks.find((entry) => entry.id === taskId);
  if (!task) {
    throw new RepositoryError("NOT_FOUND", "任务不存在。", "回项目概览重新建立任务。");
  }
  return task;
}

function findRun(project: Project, runId: string): { task: Task; run: Run } {
  for (const task of project.tasks) {
    const run = task.runs.find((entry) => entry.id === runId);
    if (run) {
      return { task, run };
    }
  }
  throw new RepositoryError("NOT_FOUND", "运行记录不存在。", "回到历史查看现有运行。");
}

function findPlan(task: Task, planId: string): Plan {
  const plan = task.plans.find((entry) => entry.id === planId);
  if (!plan) {
    throw new RepositoryError("NOT_FOUND", "计划不存在。", "重新生成计划。");
  }
  return plan;
}

function currentPlan(task: Task): Plan {
  if (!task.currentPlanId) {
    throw new RepositoryError("NOT_FOUND", "任务还没有计划。", "先选范围并生成计划。");
  }
  return findPlan(task, task.currentPlanId);
}

function markPlanStale(task: Task, reason: string): void {
  const plan = task.currentPlanId
    ? task.plans.find((entry) => entry.id === task.currentPlanId)
    : null;
  if (plan) {
    plan.stale = true;
    plan.staleReason = reason;
  }
}

function hasActiveRun(project: Project): Run | null {
  for (const task of project.tasks) {
    const run = task.runs.find((entry) => entry.status === "running");
    if (run) {
      return run;
    }
  }
  return null;
}

function normaliseItems(
  project: Project,
  task: Task,
  incoming: AcceptanceItem[],
): AcceptanceItem[] {
  const existing = new Map(task.acceptanceItems.map((item) => [item.id, item]));
  const usedIds = new Set<string>();
  return incoming.map((item, index) => {
    const base = existing.get(item.id);
    const id = item.id && !usedIds.has(item.id) ? item.id : newId("ai");
    usedIds.add(id);
    const module = item.moduleId
      ? project.modules.find((entry) => entry.id === item.moduleId)
      : null;
    const observable = Boolean(item.observable);
    return {
      id,
      no: item.no || base?.no || String(index + 1).padStart(2, "0"),
      text: item.text.trim(),
      observable,
      status: observable ? item.status : "pending",
      source: item.source,
      moduleId: item.moduleId,
      moduleName: module?.name ?? item.moduleName,
      featureName: item.featureName || base?.featureName || "未归类",
    };
  });
}

function nextReportVersion(project: Project, runId: string): number {
  const versions = project.reports
    .filter((report) => report.runId === runId)
    .map((report) => report.version);
  return Math.max(0, ...versions) + 1;
}

function attachReport(project: Project, task: Task, run: Run): Report {
  project.reports.forEach((report) => {
    if (report.runId === run.id) {
      report.status = "superseded";
    }
  });
  const report = buildReport(project, task, run, nextReportVersion(project, run.id));
  project.reports.push(report);
  run.reportIds = [...run.reportIds, report.id];
  return report;
}

function applyAutoIssues(project: Project, task: Task, run: Run): void {
  run.cases
    .filter((testCase) => testCase.result === "failed")
    .forEach((testCase) => {
      const signature = signatureForCase(testCase);
      const existing = project.issues.find((issue) => issue.signature === signature);
      if (existing) {
        const already = existing.occurrences.some(
          (occurrence) => occurrence.runId === run.id && occurrence.caseId === testCase.caseId,
        );
        if (already) {
          return;
        }
        const merged = mergeIssueOccurrence(existing, run, testCase, testCase.actual || "运行记录");
        const index = project.issues.findIndex((issue) => issue.id === existing.id);
        project.issues[index] = merged;
        return;
      }
      project.issues.push(createIssueFromCase(project, task, run, testCase));
    });
}

function finishRunInPlace(
  project: Project,
  task: Task,
  run: Run,
  status: "completed" | "cancelled" | "interrupted",
): void {
  run.status = status;
  run.finishedAt = isoNow();
  run.elapsedMs = Math.max(
    0,
    new Date(run.finishedAt).getTime() - new Date(run.startedAt).getTime(),
  );
  run.currentCaseId = "";
  run.heartbeatAt = run.finishedAt;
  const recounted = recountRun(run);
  run.items = recounted.items;
  run.counts = recounted.counts;
  run.verdict = recounted.verdict;
  if (status !== "interrupted") {
    applyAutoIssues(project, task, run);
  }
  assertTransition(task.stage, "finishRun", "result");
  task.stage = "result";
  task.updatedAt = isoNow();
  if (status !== "interrupted") {
    attachReport(project, task, run);
  }
}

export class LocalStorageRepository implements ProjectRepository {
  readonly mode = "local" as const;
  readonly capabilities = {
    hostGithub: false,
    evidenceFiles: true,
    browserBackup: true,
    linkJson: true,
  } as const;

  private readonly storage: Storage;
  private readonly key: string;

  constructor(options: { storage?: Storage; key?: string } = {}) {
    const storage = options.storage ?? (typeof localStorage === "undefined" ? null : localStorage);
    if (!storage) {
      throw new RepositoryError(
        "STORAGE",
        "当前环境没有本地存储。",
        "在支持 localStorage 的浏览器或宿主面板中打开。",
      );
    }
    this.storage = storage;
    this.key = options.key ?? STORAGE_KEY;
  }

  private read(): WorkspaceEnvelope {
    const raw = this.storage.getItem(this.key);
    if (!raw) {
      return emptyEnvelope();
    }
    try {
      return assertEnvelope(JSON.parse(raw) as unknown);
    } catch (error) {
      if (error instanceof RepositoryError) {
        throw error;
      }
      throw new RepositoryError(
        "FORMAT",
        "本地数据不是合法 JSON，无法读取。",
        "清空本地面板数据后重新建立项目；不做静默迁移。",
      );
    }
  }

  private write(envelope: WorkspaceEnvelope): void {
    envelope.savedAt = isoNow();
    try {
      this.storage.setItem(this.key, JSON.stringify(envelope));
    } catch {
      throw new RepositoryError(
        "STORAGE",
        "本地存储写入失败，可能是空间不足。",
        "清理浏览器存储后重试；当前修改未保存。",
      );
    }
  }

  private project(envelope: WorkspaceEnvelope, projectId: string): Project {
    const project = envelope.projects.find((entry) => entry.id === projectId);
    if (!project) {
      throw new RepositoryError("NOT_FOUND", "项目不存在。", "回到项目列表重新选择。");
    }
    return project;
  }

  private mutate<T>(
    projectId: string,
    callback: (project: Project, envelope: WorkspaceEnvelope) => T,
  ): T {
    const envelope = this.read();
    const source = this.project(envelope, projectId);
    const project = clone(source);
    const result = callback(project, envelope);
    project.updatedAt = isoNow();
    const index = envelope.projects.findIndex((entry) => entry.id === projectId);
    envelope.projects[index] = project;
    this.write(envelope);
    return clone(result);
  }

  async listProjects(): Promise<Project[]> {
    return clone(this.read().projects);
  }

  async getProject(projectId: string): Promise<Project | null> {
    const found = this.read().projects.find((entry) => entry.id === projectId);
    return found ? clone(found) : null;
  }

  async createProject(input: CreateProjectInput): Promise<Project> {
    const envelope = this.read();
    const form = validateProjectForm(input, envelope.projects);
    if (!form.ok) {
      throw new RepositoryError(
        "VALIDATION",
        form.errors.root ?? form.errors.name ?? "项目信息不完整。",
        "修正后重试。",
      );
    }
    const project = createProjectRecord({
      name: form.name,
      root: form.root,
      goal: input.goal,
    });
    envelope.projects.push(project);
    envelope.lastProjectId = project.id;
    this.write(envelope);
    return clone(project);
  }

  async updateProject(
    projectId: string,
    patch: Partial<Pick<Project, "name" | "goal" | "archived">>,
  ): Promise<Project> {
    return this.mutate(projectId, (project) => {
      if (patch.name !== undefined) {
        const name = patch.name.trim();
        if (!name || name.length > 40) {
          throw new RepositoryError("VALIDATION", "项目名不能为空，且最多 40 个字。");
        }
        project.name = name;
      }
      if (patch.goal !== undefined) {
        project.goal = patch.goal.trim();
      }
      if (patch.archived !== undefined) {
        project.archived = patch.archived;
      }
      return project;
    });
  }

  async saveAnalysis(projectId: string, analysis: ProjectAnalysis): Promise<Project> {
    return this.mutate(projectId, (project) => {
      project.modules = analysis.modules;
      project.map = {
        ...analysis.map,
        revision: (project.map?.revision ?? 0) + 1,
        ignoredFeatures: project.map?.ignoredFeatures ?? [],
        stale: false,
        staleReason: "",
      };
      project.candidates = analysis.candidates;
      project.catalog = analysis.catalog;
      project.analysisStatus = "ready";
      project.analysisStartedAt = "";
      project.analysisError = "";
      return project;
    });
  }

  async setFeatureIgnored(
    projectId: string,
    featureName: string,
    ignored: boolean,
  ): Promise<Project> {
    return this.mutate(projectId, (project) => {
      if (!project.map) {
        throw new RepositoryError("NOT_FOUND", "项目地图还没有生成。", "先分析项目。");
      }
      const set = new Set(project.map.ignoredFeatures);
      if (ignored) {
        set.add(featureName);
      } else {
        set.delete(featureName);
      }
      project.map.ignoredFeatures = [...set];
      return project;
    });
  }

  async beginAnalysis(projectId: string): Promise<Project> {
    return this.mutate(projectId, (project) => {
      project.analysisStatus = "analyzing";
      project.analysisStartedAt = isoNow();
      project.analysisError = "";
      return project;
    });
  }

  async failAnalysis(projectId: string, message: string): Promise<Project> {
    return this.mutate(projectId, (project) => {
      project.analysisStatus = "failed";
      project.analysisError = message;
      project.analysisStartedAt = "";
      return project;
    });
  }

  async ensureDraftTask(projectId: string): Promise<Task> {
    return this.mutate(projectId, (project) => {
      const existing = project.tasks.find((task) => task.stage === "fresh");
      if (existing) {
        return existing;
      }
      const draft = createDraftTask(project);
      project.tasks.push(draft);
      project.activeTaskId = draft.id;
      return draft;
    });
  }

  async createTask(projectId: string, input: CreateTaskInput): Promise<Task> {
    const goalError = validateGoal(input.goal);
    if (goalError) {
      throw new RepositoryError("VALIDATION", goalError, "修改目标后重试。");
    }
    return this.mutate(projectId, (project) => {
      if (project.analysisStatus !== "ready" || project.catalog.length === 0) {
        throw new RepositoryError(
          "CONFLICT",
          "项目分析还没有完成，暂时不能创建任务。",
          "等待分析完成或重新分析项目。",
        );
      }
      const candidate = input.candidateId
        ? (project.candidates.find((entry) => entry.id === input.candidateId) ?? null)
        : null;
      if (input.candidateId && !candidate) {
        throw new RepositoryError("NOT_FOUND", "目标候选不存在。", "刷新候选后重试。");
      }
      let draft = project.tasks.find((task) => task.stage === "fresh") ?? null;
      if (!draft) {
        draft = createDraftTask(project);
        project.tasks.push(draft);
      }
      assertTransition(draft.stage, "confirmGoal", "scope");
      const confirmed = confirmDraftTask(project, draft, {
        goal: input.goal,
        candidate,
      });
      const index = project.tasks.findIndex((task) => task.id === draft?.id);
      project.tasks[index] = confirmed;
      project.activeTaskId = confirmed.id;
      if (!project.goal && input.writeProjectGoal !== false) {
        project.goal = input.goal.trim();
      }
      return confirmed;
    });
  }

  async updateTask(projectId: string, taskId: string, patch: TaskPatch): Promise<Task> {
    return this.mutate(projectId, (project) => {
      const task = findTask(project, taskId);
      if (task.stage === "running") {
        throw new RepositoryError("CONFLICT", "运行中不能修改任务资料。", "等待运行结束后再改。");
      }
      const before = taskContract(task);
      if (patch.title !== undefined) task.title = patch.title.trim();
      if (patch.goal !== undefined) task.goal = patch.goal.trim();
      if (patch.intent !== undefined) task.intent = patch.intent;
      if (patch.origin !== undefined) task.origin = patch.origin;
      if (patch.scope !== undefined) task.scope = patch.scope.trim();
      if (patch.inputs !== undefined) task.inputs = patch.inputs.trim();
      if (patch.outputs !== undefined) task.outputs = patch.outputs.trim();
      if (patch.preconditions !== undefined) task.preconditions = [...patch.preconditions];
      if (patch.executor !== undefined) task.executor = patch.executor.trim();
      if (patch.reviewer !== undefined) task.reviewer = patch.reviewer.trim();
      if (patch.acceptanceItems !== undefined) {
        task.acceptanceItems = normaliseItems(project, task, patch.acceptanceItems);
        const ids = new Set(task.acceptanceItems.map((item) => item.id));
        task.selectedItemIds = task.selectedItemIds.filter((id) => ids.has(id));
        task.recommendedItemIds = task.recommendedItemIds.filter((id) => ids.has(id));
      }
      task.revision += 1;
      task.updatedAt = isoNow();
      if (before !== taskContract(task)) {
        markPlanStale(task, "验收项、可观察性、所属模块或验证意图已变化，需要生成新计划。");
      }
      return task;
    });
  }

  async deleteTask(projectId: string, taskId: string): Promise<void> {
    this.mutate(projectId, (project) => {
      const index = project.tasks.findIndex((entry) => entry.id === taskId);
      if (index < 0) {
        throw new RepositoryError("NOT_FOUND", "任务不存在。", "刷新后重试。");
      }
      const task = project.tasks[index];
      if (task.stage === "running") {
        throw new RepositoryError("CONFLICT", "运行中不能删除任务。", "先停止运行。");
      }
      project.tasks.splice(index, 1);
      project.reports = project.reports.filter((report) => report.taskId !== taskId);
      project.issues = project.issues.filter((issue) => issue.taskId !== taskId);
      if (project.activeTaskId === taskId) {
        project.activeTaskId = project.tasks[0]?.id ?? null;
      }
    });
  }

  async selectScope(projectId: string, taskId: string, itemIds: string[]): Promise<Task> {
    return this.mutate(projectId, (project) => {
      const task = findTask(project, taskId);
      if (task.stage !== "scope") {
        throw new RepositoryError("STAGE", "当前阶段不能修改范围。", "先进入选范围阶段。");
      }
      const allowed = new Set(
        task.acceptanceItems
          .filter((item) => item.observable && item.status === "active")
          .map((item) => item.id),
      );
      const invalid = itemIds.find((id) => !allowed.has(id));
      if (invalid) {
        throw new RepositoryError("VALIDATION", "选择里包含不适用或待补充的验收项。");
      }
      task.selectedItemIds = [...new Set(itemIds)];
      task.updatedAt = isoNow();
      return task;
    });
  }

  async reviseScope(projectId: string, taskId: string): Promise<Task> {
    return this.mutate(projectId, (project) => {
      const task = findTask(project, taskId);
      if (task.stage === "running") {
        throw new RepositoryError("CONFLICT", "运行中不能调整范围。", "先停止或等待运行结束。");
      }
      if (task.stage === "fresh" || task.stage === "scope") {
        return task;
      }
      const previousStage = task.stage;
      assertTransition(task.stage, "reviseScope", "scope");
      task.previousStage = previousStage;
      task.stage = "scope";
      task.updatedAt = isoNow();
      return task;
    });
  }

  async cancelRevision(projectId: string, taskId: string): Promise<Task> {
    return this.mutate(projectId, (project) => {
      const task = findTask(project, taskId);
      if (task.stage !== "scope" || !task.previousStage) {
        throw new RepositoryError("STAGE", "当前没有可以取消的范围调整。");
      }
      const target = task.previousStage;
      assertTransition("scope", "cancelRevise", target);
      task.stage = target;
      task.previousStage = null;
      task.updatedAt = isoNow();
      return task;
    });
  }

  async prepareRerun(projectId: string, taskId: string, itemIds?: string[]): Promise<Plan> {
    return this.mutate(projectId, (project) => {
      const task = findTask(project, taskId);
      if (task.stage === "running") {
        throw new RepositoryError("CONFLICT", "运行中不能准备重跑。", "等待运行结束。");
      }
      const run = task.currentRunId
        ? (task.runs.find((entry) => entry.id === task.currentRunId) ?? null)
        : null;
      const fallback = run
        ? run.scope.acceptanceItemIds.filter((itemId) => itemOutcome(run, itemId) !== "passed")
        : [];
      const target = itemIds && itemIds.length > 0 ? itemIds : fallback;
      const selected =
        target.length > 0 ? target : (currentPlan(task).scope.acceptanceItemIds ?? []);
      if (task.stage === "result") {
        assertTransition(task.stage, "reviseScope", "scope");
        task.stage = "scope";
      }
      if (task.stage !== "scope") {
        throw new RepositoryError("STAGE", "当前阶段不能准备重跑。", "先回到选范围阶段。");
      }
      task.selectedItemIds = [...selected];
      const plan = buildPlan(project, task, selected, project.rulesVersionId);
      task.plans.push(plan);
      task.currentPlanId = plan.id;
      assertTransition(task.stage, "generatePlan", "planned");
      task.stage = "planned";
      task.previousStage = null;
      task.updatedAt = isoNow();
      return plan;
    });
  }

  async setActiveTask(projectId: string, taskId: string): Promise<Project> {
    return this.mutate(projectId, (project) => {
      findTask(project, taskId);
      const busy = hasActiveRun(project);
      if (busy && busy.taskId !== taskId) {
        throw new RepositoryError(
          "RUN_ACTIVE",
          "同一项目有任务正在运行，不能切换任务。",
          "等待运行结束或先停止运行。",
        );
      }
      project.activeTaskId = taskId;
      return project;
    });
  }

  async generatePlan(projectId: string, taskId: string): Promise<Plan> {
    return this.mutate(projectId, (project) => {
      const task = findTask(project, taskId);
      if (task.stage !== "scope") {
        throw new RepositoryError("STAGE", "当前阶段不能生成计划。", "先选本次范围。");
      }
      if (task.selectedItemIds.length === 0) {
        throw new RepositoryError(
          "SCOPE_EMPTY",
          "至少选择 1 项才能生成计划。",
          "回到范围页勾选验收项。",
        );
      }
      const plan = buildPlan(project, task, task.selectedItemIds, project.rulesVersionId);
      task.plans.push(plan);
      task.currentPlanId = plan.id;
      assertTransition(task.stage, "generatePlan", "planned");
      task.stage = "planned";
      task.previousStage = null;
      task.updatedAt = isoNow();
      return plan;
    });
  }

  async confirmPlan(
    projectId: string,
    taskId: string,
    planId: string,
    input: ConfirmPlanInput,
  ): Promise<Confirmation> {
    return this.mutate(projectId, (project) => {
      const task = findTask(project, taskId);
      if (task.stage !== "planned") {
        throw new RepositoryError("STAGE", "当前阶段不能确认计划。", "先生成计划。");
      }
      const plan = findPlan(task, planId);
      if (plan.id !== task.currentPlanId) {
        throw new RepositoryError("CONFLICT", "这不是当前计划。", "刷新后重新确认。");
      }
      if (plan.stale || plan.taskContract !== taskContract(task)) {
        plan.stale = true;
        plan.staleReason = plan.staleReason || "任务契约已变化，需要重新生成计划。";
        throw new RepositoryError("PLAN_STALE", "计划已过期，不能确认。", "重新生成计划。");
      }
      if (plan.sourceSignature !== sourceSignature(project, task)) {
        plan.stale = true;
        plan.staleReason = "项目配置、交付或规则已变化，需要重新生成计划。";
        throw new RepositoryError("PLAN_STALE", "计划来源已变化，不能确认。", "重新生成计划。");
      }
      const confirmation = buildConfirmation(
        project,
        plan,
        input.environment,
        input.sideEffects,
        input.operator,
      );
      plan.confirmation = confirmation;
      task.updatedAt = isoNow();
      return confirmation;
    });
  }

  async startRun(projectId: string, taskId: string, planId: string): Promise<Run> {
    return this.mutate(projectId, (project) => {
      const active = hasActiveRun(project);
      if (active) {
        throw new RepositoryError(
          "RUN_ACTIVE",
          `同一项目已有运行：${active.id}。`,
          "等待、查询或先停止现有运行。",
        );
      }
      const task = findTask(project, taskId);
      if (task.stage !== "planned") {
        throw new RepositoryError("STAGE", "当前阶段不能启动运行。", "先生成计划并确认。");
      }
      const plan = findPlan(task, planId);
      if (plan.id !== task.currentPlanId) {
        throw new RepositoryError("CONFLICT", "这不是当前计划。", "刷新后重试。");
      }
      if (plan.stale || plan.taskContract !== taskContract(task)) {
        plan.stale = true;
        plan.staleReason = plan.staleReason || "任务契约已变化，需要重新生成计划。";
        throw new RepositoryError("PLAN_STALE", "计划已过期，不能启动。", "重新生成计划。");
      }
      if (plan.sourceSignature !== sourceSignature(project, task)) {
        plan.stale = true;
        plan.staleReason = "项目配置、交付、规则或项目根目录已变化，需要重新生成计划。";
        throw new RepositoryError("PLAN_STALE", "计划来源已变化，不能启动。", "重新生成计划。");
      }
      if (!plan.confirmation) {
        throw new RepositoryError(
          "CONFIRMATION_REQUIRED",
          "执行前必须由人勾选确认。",
          "确认计划版本、范围、依赖和副作用后再启动。",
        );
      }
      const environment = buildEnvironmentChecks();
      const effects = sideEffectsFor(project);
      const dependencies = plan.cases.flatMap((testCase) => testCase.dependencies);
      const environmentValues: Record<string, string> = {};
      environment.forEach((check) => {
        environmentValues[check.key] = check.ok ? "ok" : "missing";
      });
      const fingerprint = confirmationFingerprint(
        plan,
        plan.scope.acceptanceItemIds,
        environmentValues,
        effects,
        dependencies,
      );
      if (fingerprint !== plan.confirmation.fingerprint) {
        plan.confirmation = null;
        throw new RepositoryError(
          "CONFIRMATION_REQUIRED",
          "范围、环境、凭据或副作用已变化，需要重新确认。",
          "重新勾选确认后再启动。",
        );
      }
      const run = buildRun(project, task, plan, plan.confirmation);
      task.runs.push(run);
      task.currentRunId = run.id;
      assertTransition(task.stage, "startRun", "running");
      task.stage = "running";
      task.previousStage = null;
      task.updatedAt = isoNow();
      return run;
    });
  }

  async recordRunCase(
    projectId: string,
    runId: string,
    caseId: string,
    update: RunCaseUpdate,
  ): Promise<Run> {
    return this.mutate(projectId, (project) => {
      const { run } = findRun(project, runId);
      if (run.status !== "running") {
        throw new RepositoryError("CONFLICT", "运行已结束，不能再写入用例结果。");
      }
      const runCase =
        run.cases.find((entry) => entry.id === caseId) ??
        run.cases.find((entry) => entry.caseId === caseId);
      if (!runCase) {
        throw new RepositoryError("NOT_FOUND", "运行用例不存在。");
      }
      runCase.result = update.result;
      runCase.actual = update.actual;
      runCase.message = update.message;
      runCase.errorType = update.errorType;
      runCase.stackKey = update.stackKey;
      runCase.evidence = update.evidence;
      runCase.attempts += 1;
      run.currentCaseId = runCase.id;
      run.heartbeatAt = isoNow();
      run.elapsedMs = Math.max(0, Date.now() - new Date(run.startedAt).getTime());
      return run;
    });
  }

  async completeRun(projectId: string, runId: string): Promise<Run> {
    return this.mutate(projectId, (project) => {
      const { task, run } = findRun(project, runId);
      if (run.status !== "running") {
        throw new RepositoryError("CONFLICT", "运行已经结束。");
      }
      run.currentCaseId = "";
      finishRunInPlace(project, task, run, "completed");
      return run;
    });
  }

  async cancelRun(projectId: string, runId: string): Promise<Run> {
    return this.mutate(projectId, (project) => {
      const { task, run } = findRun(project, runId);
      if (run.status !== "running") {
        throw new RepositoryError("CONFLICT", "运行已经结束。");
      }
      run.cancelRequested = true;
      const current = run.cases.find((testCase) => testCase.id === run.currentCaseId);
      if (current && current.result === "not_run") {
        current.result = "blocked";
        current.message = "已取消：检查执行中被停止。";
      }
      finishRunInPlace(project, task, run, "cancelled");
      return run;
    });
  }

  async markRunInterrupted(projectId: string, runId: string): Promise<Run> {
    return this.mutate(projectId, (project) => {
      const { task, run } = findRun(project, runId);
      if (run.status !== "running") {
        return run;
      }
      const current = run.cases.find((testCase) => testCase.id === run.currentCaseId);
      if (current && current.result === "not_run") {
        current.result = "blocked";
        current.message = "运行中断：没有收到后续心跳。";
      }
      finishRunInPlace(project, task, run, "interrupted");
      return run;
    });
  }

  async recordManual(
    projectId: string,
    runId: string,
    caseId: string,
    input: ManualResultInput,
  ): Promise<Run> {
    return this.mutate(projectId, (project) => {
      const { task, run } = findRun(project, runId);
      if (run.status !== "completed") {
        throw new RepositoryError("CONFLICT", "只有已完成的运行可以记录人工结果。");
      }
      const runCase = run.cases.find((entry) => entry.id === caseId || entry.caseId === caseId);
      if (!runCase) {
        throw new RepositoryError("NOT_FOUND", "用例不存在。");
      }
      if (runCase.layer !== "L3") {
        throw new RepositoryError("CONFLICT", "自动用例不能人工修改结果。");
      }
      runCase.result = input.result;
      runCase.actual = input.note || "人工记录";
      runCase.evidence = input.evidence;
      runCase.attempts += 1;
      runCase.manualSteps.push({
        step: "人工记录结果",
        result: input.result,
        note: input.note,
        at: isoNow(),
      });
      const recounted = recountRun(run);
      run.items = recounted.items;
      run.counts = recounted.counts;
      run.verdict = recounted.verdict;
      task.updatedAt = isoNow();
      attachReport(project, task, run);
      return run;
    });
  }

  async addEvidence(projectId: string, runId: string, input: EvidenceInput): Promise<Run> {
    return this.mutate(projectId, (project) => {
      const { run } = findRun(project, runId);
      if (run.status === "running") {
        throw new RepositoryError("RUN_ACTIVE", "运行中不能补充证据。", "运行结束后再登记。");
      }
      const evidence: EvidenceRef = {
        id: newId("ev"),
        name: input.name,
        mediaType: input.mediaType,
        bytes: input.bytes,
        sha256: input.sha256,
        note: input.note,
        missing: false,
        truncated: false,
        addedAt: isoNow(),
        sourcePath: `runs/${run.id}/evidence/${input.name}`,
      };
      run.evidence.push(evidence);
      if (input.caseId) {
        const runCase = run.cases.find(
          (entry) => entry.id === input.caseId || entry.caseId === input.caseId,
        );
        if (!runCase) {
          throw new RepositoryError("NOT_FOUND", "用例不存在。");
        }
        runCase.evidence.push(evidence);
      }
      return run;
    });
  }

  async createIssue(projectId: string, input: IssueCreateInput): Promise<Issue> {
    return this.mutate(projectId, (project) => {
      findTask(project, input.taskId);
      const issue = createManualIssue(project, input);
      const existing = project.issues.find((entry) => entry.signature === issue.signature);
      if (existing) {
        return existing;
      }
      project.issues.push(issue);
      return issue;
    });
  }

  async updateIssue(projectId: string, issueId: string, patch: IssuePatch): Promise<Issue> {
    return this.mutate(projectId, (project) => {
      const issue = project.issues.find((entry) => entry.id === issueId);
      if (!issue) {
        throw new RepositoryError("NOT_FOUND", "问题不存在。");
      }
      const updated = applyIssueUpdate(issue, patch);
      const index = project.issues.findIndex((entry) => entry.id === issueId);
      project.issues[index] = updated;
      return updated;
    });
  }

  async closeIssue(
    projectId: string,
    issueId: string,
    runId: string,
    reason: string,
  ): Promise<Issue> {
    return this.mutate(projectId, (project) => {
      const issue = project.issues.find((entry) => entry.id === issueId);
      if (!issue) {
        throw new RepositoryError("NOT_FOUND", "问题不存在。");
      }
      const gate = issueCloseGate(project, issue);
      if (!gate.ok) {
        throw new RepositoryError("ISSUE_GATE", gate.reason, "先完成一次包含该用例的新运行。");
      }
      if (gate.run.id !== runId) {
        throw new RepositoryError(
          "ISSUE_GATE",
          "指定的运行不是满足条件的复测运行。",
          `使用运行 ${gate.run.id}。`,
        );
      }
      const closed = closeIssueRecord(issue, gate.run, gate.runCase, reason);
      const index = project.issues.findIndex((entry) => entry.id === issueId);
      project.issues[index] = closed;
      return closed;
    });
  }

  async saveReport(projectId: string, report: Report): Promise<Report> {
    return this.mutate(projectId, (project) => {
      project.reports.forEach((entry) => {
        if (entry.runId === report.runId && entry.id !== report.id) {
          entry.status = "superseded";
        }
      });
      const existing = project.reports.find((entry) => entry.id === report.id);
      if (existing) {
        Object.assign(existing, report);
      } else {
        project.reports.push(report);
      }
      const { run } = findRun(project, report.runId);
      if (!run.reportIds.includes(report.id)) {
        run.reportIds.push(report.id);
      }
      return report;
    });
  }

  async regenerateReport(projectId: string, runId: string): Promise<Report> {
    return this.mutate(projectId, (project) => {
      const { task, run } = findRun(project, runId);
      if (run.status === "running") {
        throw new RepositoryError("RUN_ACTIVE", "运行中不能重新生成报告。");
      }
      return attachReport(project, task, run);
    });
  }

  async saveOpinion(
    projectId: string,
    reportId: string,
    by: string,
    text: string,
  ): Promise<Report> {
    return this.mutate(projectId, (project) => {
      const report = project.reports.find((entry) => entry.id === reportId);
      if (!report) {
        throw new RepositoryError("NOT_FOUND", "报告不存在。");
      }
      const latest = project.reports
        .filter((entry) => entry.runId === report.runId)
        .sort((first, second) => second.version - first.version)[0];
      if (latest && latest.id !== report.id) {
        throw new RepositoryError("CONFLICT", "旧版本报告只读，不能在旧版本上填写意见。");
      }
      if (!text.trim()) {
        throw new RepositoryError("VALIDATION", "先填写验收意见。");
      }
      report.opinion = {
        by: by.trim() || "未署名",
        text: text.trim(),
        at: isoNow(),
      };
      return report;
    });
  }

  async exportReport(projectId: string, reportId: string): Promise<ExportResult> {
    const envelope = this.read();
    const project = this.project(envelope, projectId);
    const report = project.reports.find((entry) => entry.id === reportId);
    if (!report) {
      throw new RepositoryError("NOT_FOUND", "报告不存在。");
    }
    const task = findTask(project, report.taskId);
    const markdown = reportMarkdown(project, task, report);
    const filename = `${project.name}-测试报告-第${report.version}版.md`;
    return exportMetrics(filename, markdown);
  }

  async saveDelivery(projectId: string, taskId: string, input: DeliveryInput): Promise<Task> {
    return this.mutate(projectId, (project) => {
      const task = findTask(project, taskId);
      if (task.stage === "running") {
        throw new RepositoryError("CONFLICT", "运行中不能修改交付说明。");
      }
      const previous = task.delivery;
      const delivery: Delivery = {
        id: previous?.id ?? newId("dlv"),
        taskId,
        done: [...input.done],
        notDone: [...input.notDone],
        changedModules: [...input.changedModules],
        interfaceChanges: [...input.interfaceChanges],
        runCommands: [...input.runCommands],
        testData: input.testData.trim(),
        dependencies: [...input.dependencies],
        mocks: [...input.mocks],
        selfTestEvidence: [...input.selfTestEvidence],
        commit: input.commit.trim(),
        complete: input.runCommands.length > 0 && input.commit.trim() !== "",
        revision: (previous?.revision ?? 0) + 1,
        updatedAt: isoNow(),
      };
      task.delivery = delivery;
      task.updatedAt = isoNow();
      return task;
    });
  }

  async draftDelivery(projectId: string, taskId: string): Promise<Delivery> {
    const envelope = this.read();
    const project = this.cloneProject(envelope, projectId);
    const task = findTask(project, taskId);
    return buildDeliveryDraft(project, task);
  }

  async applyTemplate(projectId: string, taskId: string, templateId: string): Promise<Task> {
    return this.mutate(projectId, (project) => {
      const task = findTask(project, taskId);
      if (task.stage === "running") {
        throw new RepositoryError("CONFLICT", "运行中不能应用检查模板。");
      }
      const result = applyTemplateResult(project, templateId);
      task.templates = [
        ...task.templates.filter((entry) => entry.templateId !== templateId),
        result,
      ];
      task.updatedAt = isoNow();
      return task;
    });
  }

  async saveRule(projectId: string, input: RuleInput): Promise<RuleVersion> {
    return this.mutate(projectId, (project) => {
      if (!input.name.trim() || !input.version.trim()) {
        throw new RepositoryError("VALIDATION", "规则名称和版本不能为空。");
      }
      const rule = createRuleVersion(projectId, input);
      project.rules.push(rule);
      return rule;
    });
  }

  async bindRule(projectId: string, ruleId: string): Promise<Project> {
    return this.mutate(projectId, (project) => {
      const rule = project.rules.find((entry) => entry.id === ruleId);
      if (!rule) {
        throw new RepositoryError("NOT_FOUND", "规则版本不存在。");
      }
      project.rulesVersionId = rule.id;
      return project;
    });
  }

  async saveConfig(projectId: string, config: ProjectConfig): Promise<Project> {
    return this.mutate(projectId, (project) => {
      project.config = clone(config);
      return project;
    });
  }

  async saveGithubSnapshot(projectId: string, snapshot: GithubSnapshot | null): Promise<Project> {
    return this.mutate(projectId, (project) => {
      project.github = snapshot ? clone(snapshot) : null;
      return project;
    });
  }

  async addModule(projectId: string, input: ModuleInput): Promise<Module> {
    return this.mutate(projectId, (project) => {
      if (!input.name.trim()) {
        throw new RepositoryError("VALIDATION", "模块名称不能为空。");
      }
      const module: Module = {
        id: newId("mod"),
        name: input.name.trim(),
        responsibility: input.responsibility.trim(),
        owner: input.owner.trim(),
        status: input.status.trim() || "开发中",
        dependencies: [...input.dependencies],
      };
      project.modules.push(module);
      return module;
    });
  }

  async updateModule(
    projectId: string,
    moduleId: string,
    patch: Partial<ModuleInput>,
  ): Promise<Module> {
    return this.mutate(projectId, (project) => {
      const module = project.modules.find((entry) => entry.id === moduleId);
      if (!module) {
        throw new RepositoryError("NOT_FOUND", "模块不存在。");
      }
      if (patch.name !== undefined) module.name = patch.name.trim();
      if (patch.responsibility !== undefined) module.responsibility = patch.responsibility.trim();
      if (patch.owner !== undefined) module.owner = patch.owner.trim();
      if (patch.status !== undefined) module.status = patch.status.trim();
      if (patch.dependencies !== undefined) module.dependencies = [...patch.dependencies];
      return module;
    });
  }

  async importLink(projectId: string, runId: string, link: LinkData): Promise<Run> {
    return this.mutate(projectId, (project) => {
      const { run } = findRun(project, runId);
      if (run.status === "running") {
        throw new RepositoryError("RUN_ACTIVE", "运行中不能导入链路。");
      }
      run.link = link;
      return run;
    });
  }

  async assertLink(
    projectId: string,
    runId: string,
    input: LinkAssertInput,
  ): Promise<LinkAssertion> {
    return this.mutate(projectId, (project) => {
      const { run } = findRun(project, runId);
      if (!run.link) {
        throw new RepositoryError("CONFLICT", "这次运行还没有导入链路。", "先导入链路 JSON。");
      }
      const node = run.link.nodes.find((entry) => entry.id === input.nodeId);
      if (!node) {
        throw new RepositoryError("NOT_FOUND", "链路中没有这个节点。");
      }
      const operator = input.operator || "equals";
      let passed = false;
      if (operator === "contains") {
        passed = String(input.actual).includes(String(input.expected));
      } else if (operator === "not_equals") {
        passed = String(input.actual) !== String(input.expected);
      } else {
        passed = String(input.actual) === String(input.expected);
      }
      const assertion: LinkAssertion = {
        nodeId: input.nodeId,
        expected: input.expected,
        actual: input.actual,
        result: passed ? "passed" : "failed",
        operator,
      };
      run.link.assertions = [
        ...run.link.assertions.filter((entry) => entry.nodeId !== input.nodeId),
        assertion,
      ];
      if (!passed) {
        node.state = "failed";
      }
      const anomaly =
        run.link.nodes.find((entry) => entry.state !== "passed") ?? (passed ? null : node);
      if (anomaly) {
        const match = /^(.*):(\d+)$/.exec(anomaly.evidenceRef);
        run.link.location = match
          ? {
              known: true,
              path: match[1] ?? "",
              line: Number(match[2] ?? 0),
              reason: "",
            }
          : {
              known: false,
              path: "",
              line: 0,
              reason: "没有可解析的 file:line 证据引用。",
            };
      } else {
        run.link.location = {
          known: false,
          path: "",
          line: 0,
          reason: "没有可定位的异常节点。",
        };
      }
      return assertion;
    });
  }

  async recordQuickCheck(projectId: string, quick: QuickCheck): Promise<Project> {
    return this.mutate(projectId, (project) => {
      project.quickChecks = [quick, ...project.quickChecks].slice(0, 10);
      return project;
    });
  }

  async backup(): Promise<BackupPayload> {
    const envelope = this.read();
    const json = JSON.stringify(envelope, null, 2);
    const manifest = {
      schema_version: 1,
      format: "aitest.panel.backup",
      created_at: isoNow(),
      projects: envelope.projects.length,
      includes_evidence: false,
    };
    const data = zipSync({
      "workspace.json": strToU8(json),
      "manifest.json": strToU8(JSON.stringify(manifest, null, 2)),
    });
    const result: BackupResult = {
      filename: `aitest-backup-${new Date().toISOString().slice(0, 10)}.zip`,
      bytes: data.length,
      projects: envelope.projects.length,
      includesEvidence: false,
      format: "zip",
    };
    return { data, result };
  }

  async restore(data: string | Uint8Array | ArrayBuffer): Promise<Project[]> {
    let raw = "";
    if (typeof data === "string") {
      raw = data;
    } else {
      const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
      const isZip = bytes.length >= 2 && bytes[0] === 0x50 && bytes[1] === 0x4b;
      if (isZip) {
        try {
          const files = unzipSync(bytes);
          const workspace = files["workspace.json"];
          if (!workspace) {
            throw new Error("missing workspace.json");
          }
          raw = strFromU8(workspace);
        } catch {
          throw new RepositoryError(
            "FORMAT",
            "备份 ZIP 无法读取或缺少 workspace.json。",
            "换一个备份文件。",
          );
        }
      } else {
        raw = new TextDecoder().decode(bytes);
      }
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw) as unknown;
    } catch {
      throw new RepositoryError("FORMAT", "备份文件不是合法 JSON 或 ZIP。", "换一个备份文件。");
    }
    const envelope = assertEnvelope(parsed);
    this.write(envelope);
    return clone(envelope.projects);
  }

  async clear(): Promise<void> {
    this.storage.removeItem(this.key);
  }

  async getLastProjectId(): Promise<string | null> {
    return this.read().lastProjectId;
  }

  async setLastProjectId(projectId: string): Promise<void> {
    const envelope = this.read();
    envelope.lastProjectId = projectId;
    this.write(envelope);
  }

  private cloneProject(envelope: WorkspaceEnvelope, projectId: string): Project {
    return clone(this.project(envelope, projectId));
  }
}
