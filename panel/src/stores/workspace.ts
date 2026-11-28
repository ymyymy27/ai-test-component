import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { buildProjectAnalysis, type ProjectAnalysis } from "@/domain/analysis";
import {
  activeTask as findActiveTask,
  allReports,
  allRuns,
  currentPlan as readCurrentPlan,
  currentRun as readCurrentRun,
  openIssues as readOpenIssues,
  runningTask as findRunningTask,
  sideEffectsFor,
} from "@/domain/derive";
import { buildQuickCheckRun, completeQuickCheck } from "@/domain/factory";
import { newId } from "@/domain/ids";
import type {
  Delivery,
  EnvironmentCheck,
  Issue,
  Plan,
  PlanReviewItem,
  Project,
  ProjectConfig,
  ProjectConvention,
  QuickCheck,
  Report,
  Run,
  Task,
} from "@/domain/types";
import { api as hostApi } from "@/host/commands";
import { mapPlanReviews } from "@/host/map";
import type { PlanRepairCaseResultDto } from "@/host/dto";
import { RepositoryError } from "@/repositories/errors";
import { HostRepository } from "@/repositories/HostRepository";
import { LocalStorageRepository } from "@/repositories/LocalStorageRepository";
import {
  callHost,
  clearHostDiagnostics,
  hostBridge,
  onHostContext,
  onHostWorkspaceDirty,
  openHostSource,
  publishHostDiagnostics,
  readHostContext,
  setHostActivityObserver,
  type HostCapabilitiesFlags,
  type HostDiagnosticInput,
  type HostGitSummary,
} from "@/host/bridge";
import { mapDoctorFacts } from "@/host/environment";
import type { DoctorFacts, EvidencePreview, RunLogTailEntry } from "@/host/dto";
import type {
  ConfirmPlanInput,
  ConventionInput,
  CreateProjectInput,
  CreateTaskInput,
  DeliveryInput,
  EvidenceInput,
  IssueCreateInput,
  IssuePatch,
  LinkAssertInput,
  ModuleInput,
  ProjectRepository,
  TaskPatch,
} from "@/repositories/ProjectRepository";
import { buildEnvironmentChecks } from "@/services/environmentService";
import { downloadBytes, downloadText } from "@/services/exportService";
import { useUiStore } from "./ui";
import { createSimulationDriver, errorMessage } from "./workspaceHelpers";

const OPERATOR = "本机用户";
const ANALYSIS_DELAY_MS = 800;
const RUN_STALE_MS = 10 * 60 * 1000;

let repository: ProjectRepository | null = null;
let repositoryLocked = false;

export function setRepository(next: ProjectRepository | null): void {
  repository = next;
  repositoryLocked = next !== null;
}

function repo(): ProjectRepository {
  if (!repository) {
    repository = hostBridge() ? new HostRepository() : new LocalStorageRepository();
  } else if (!repositoryLocked && repository.mode === "local" && hostBridge()) {
    // 桥接可能晚于首次读取出现；不能永久把宿主写入浏览器本地存储。
    repository = new HostRepository();
  }
  return repository;
}

export const useWorkspaceStore = defineStore("workspace", () => {
  const projects = ref<Project[]>([]);
  const currentProjectId = ref<string | null>(null);
  const status = ref<"idle" | "loading" | "ready" | "error">("idle");
  const error = ref("");
  const busy = ref<Record<string, boolean>>({});
  const planFailures = ref<Record<string, string>>({});
  const environment = ref<EnvironmentCheck[]>([]);
  const workspaceRoot = ref("");
  const quickCheck = ref<QuickCheck | null>(null);
  const initialized = ref(false);
  const quickCheckError = ref("");
  const dirtyFiles = ref<string[]>([]);
  const hostCapabilities = ref<Partial<HostCapabilitiesFlags>>({});
  const hostGit = ref<HostGitSummary | null>(null);
  let bootstrapPromise: Promise<void> | null = null;
  const analysisTimers = new Map<string, ReturnType<typeof setTimeout>>();
  const quickCheckTimers = new Map<string, ReturnType<typeof setTimeout>>();
  let hostPollTimer: ReturnType<typeof setTimeout> | null = null;
  let pollToken = 0;
  let switchSeq = 0;
  let storageListenerInstalled = false;
  let focusListenerInstalled = false;
  let hostContextInstalled = false;
  let hostDirtyInstalled = false;
  let lastContextProjectId = "";

  const mode = computed(() => repo().mode);
  const capabilities = computed(() => repo().capabilities);

  const currentProject = computed<Project | null>(
    () => projects.value.find((project) => project.id === currentProjectId.value) ?? null,
  );
  const currentTask = computed<Task | null>(() => {
    const project = currentProject.value;
    return project ? findActiveTask(project) : null;
  });
  const draftTask = computed<Task | null>(
    () => currentProject.value?.tasks.find((task) => task.stage === "fresh") ?? null,
  );
  const tasks = computed<Task[]>(
    () => currentProject.value?.tasks.filter((task) => task.stage !== "fresh") ?? [],
  );
  const runningTask = computed<Task | null>(() =>
    currentProject.value ? findRunningTask(currentProject.value) : null,
  );
  const currentPlan = computed<Plan | null>(() => {
    const task = currentTask.value;
    return task ? readCurrentPlan(task) : null;
  });
  const currentRun = computed<Run | null>(() => {
    const task = currentTask.value;
    return task ? readCurrentRun(task) : null;
  });
  const projectIssues = computed<Issue[]>(() => currentProject.value?.issues ?? []);
  const openIssues = computed<Issue[]>(() =>
    currentProject.value ? readOpenIssues(currentProject.value) : [],
  );
  const projectRuns = computed<Run[]>(() =>
    currentProject.value ? allRuns(currentProject.value) : [],
  );
  const projectReports = computed<Report[]>(() =>
    currentProject.value ? allReports(currentProject.value) : [],
  );
  const loading = computed(() => status.value === "loading");
  const hasRunningRun = computed(() => Boolean(runningTask.value));

  function busyKey(key: string): boolean {
    return Boolean(busy.value[key]);
  }

  function issueById(issueId: string): Issue | null {
    return projectIssues.value.find((issue) => issue.id === issueId) ?? null;
  }

  function runById(runId: string): Run | null {
    return projectRuns.value.find((run) => run.id === runId) ?? null;
  }

  function reportById(reportId: string): Report | null {
    return projectReports.value.find((report) => report.id === reportId) ?? null;
  }

  function planById(planId: string): Plan | null {
    return currentTask.value?.plans.find((plan) => plan.id === planId) ?? null;
  }

  function replaceProject(project: Project): void {
    const index = projects.value.findIndex((entry) => entry.id === project.id);
    const existing = index >= 0 ? projects.value[index] : null;
    if (existing && existing.reports.length) {
      // 宿主快照只带最新报告；保留已加载的历史版本，避免版本列表被下一次刷新塌缩。
      const known = new Set(project.reports.map((report) => report.id));
      const extra = existing.reports.filter((report) => !known.has(report.id));
      if (extra.length) {
        project = { ...project, reports: [...project.reports, ...extra] };
      }
    }
    if (index >= 0) {
      projects.value[index] = project;
    } else {
      projects.value.push(project);
    }
  }

  function updateTaskInProject(projectId: string, updatedTask: Task): void {
    const project = projects.value.find((entry) => entry.id === projectId);
    if (!project) {
      return;
    }
    const nextTasks = project.tasks.some((entry) => entry.id === updatedTask.id)
      ? project.tasks.map((entry) => (entry.id === updatedTask.id ? updatedTask : entry))
      : [updatedTask, ...project.tasks];
    replaceProject({ ...project, tasks: nextTasks });
  }

  async function refreshProject(projectId: string): Promise<Project | null> {
    const project = await repo().getProject(projectId);
    if (project) {
      replaceProject(project);
      if (currentProjectId.value === projectId) {
        dirtyFiles.value = [];
        void syncHostDiagnostics(projectId);
      }
    }
    return project;
  }

  async function syncOrRefreshProject(projectId: string): Promise<Project | null> {
    const cached = repo().cachedProject?.(projectId);
    if (cached) {
      replaceProject(cached);
      return cached;
    }
    return refreshProject(projectId);
  }

  async function refreshAll(): Promise<void> {
    const listed = await repo().listProjects();
    const existingById = new Map(projects.value.map((entry) => [entry.id, entry]));
    projects.value = listed.map((summary) => {
      const existing = existingById.get(summary.id);
      if (existing && !existing.summaryOnly) {
        return {
          ...existing,
          name: summary.name,
          root: summary.root,
          archived: summary.archived,
          updatedAt: summary.updatedAt || existing.updatedAt,
        };
      }
      return summary;
    });
    workspaceRoot.value = repo().workspacePath?.() ?? "";
  }

  /** 重新读取项目列表，并保住当前项目的完整数据（列表只含摘要）。 */
  async function refreshProjects(): Promise<void> {
    await refreshAll();
    const current = currentProjectId.value;
    if (current) {
      await refreshProject(current);
    }
  }

  function toastError(cause: unknown): void {
    useUiStore().showToast(errorMessage(cause), "error");
  }

  function installStorageListener(): void {
    if (storageListenerInstalled || typeof window === "undefined") {
      return;
    }
    storageListenerInstalled = true;
    window.addEventListener("storage", (event) => {
      if (event.key !== "aitest.panel.v1" && event.key !== "aitest.host.ui.v1") {
        return;
      }
      void refreshAll()
        .then(() => {
          const current = currentProjectId.value;
          return current ? refreshProject(current) : null;
        })
        .catch((cause) => toastError(cause));
    });
  }

  function installFocusRefresh(): void {
    // 项目可能在另一个窗口/CLI 里登记；回到面板时重新读一次 project.list，
    // 节流到 30 秒，避免窗口切换与焦点抖动造成高频 CLI 冷启动。
    if (focusListenerInstalled || typeof window === "undefined") {
      return;
    }
    if (repo().mode !== "host") {
      return;
    }
    focusListenerInstalled = true;
    let lastRun = 0;
    const refresh = () => {
      const now = Date.now();
      if (status.value !== "ready" || now - lastRun < 30_000) {
        return;
      }
      lastRun = now;
      void refreshProjects().catch((cause) => toastError(cause));
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        refresh();
      }
    });
  }

  const COMMAND_LABELS: Record<string, string> = {
    "plan.generate": "正在生成测试计划",
    "project.map.refresh": "正在刷新项目静态图谱",
    "task.add": "正在拆解验收项与创建任务",
    "task.suggest": "正在分析可测试目标",
    "delivery.draft": "正在生成交付清单草稿",
    "report.generate": "正在生成测试报告",
    "run.repair": "正在安装依赖并修复环境",
    "backup.create": "正在创建数据备份",
    "backup.restore": "正在恢复工作空间备份",
    "analysis.run": "正在分析运行失败原因",
    "github.read": "正在读取 GitHub 仓库与提交变更",
  };

  function installHostActivityObserver(): void {
    setHostActivityObserver({
      onStart: (command, cancel) =>
        useUiStore().startTask(command, COMMAND_LABELS[command] ?? `正在执行 ${command}`, cancel),
      onProgress: (token, event) => {
        useUiStore().updateTaskProgress(token, event);
      },
      onEnd: (token) => {
        useUiStore().finishTask(token);
      },
    });
  }

  function installHostContextListener(): void {
    if (hostContextInstalled || repo().mode !== "host") {
      return;
    }
    hostContextInstalled = true;
    const initialCtx = readHostContext();
    if (initialCtx?.capabilities) {
      hostCapabilities.value = initialCtx.capabilities;
    }
    if (initialCtx?.hostGit !== undefined) {
      hostGit.value = initialCtx.hostGit ?? null;
    }
    onHostContext((ctx) => {
      if (typeof ctx.workspacePath === "string") {
        workspaceRoot.value = ctx.workspacePath.trim();
      }
      if (ctx.capabilities) {
        hostCapabilities.value = ctx.capabilities;
      }
      if (ctx.hostGit !== undefined) {
        hostGit.value = ctx.hostGit ?? null;
      }
      const nextId = typeof ctx.projectId === "string" ? ctx.projectId.trim() : "";
      if (
        nextId &&
        nextId !== lastContextProjectId &&
        initialized.value &&
        projects.value.some((entry) => entry.id === nextId) &&
        currentProjectId.value !== nextId
      ) {
        lastContextProjectId = nextId;
        void setCurrentProject(nextId).catch(() => undefined);
      }
    });
    if (!hostDirtyInstalled) {
      hostDirtyInstalled = true;
      onHostWorkspaceDirty((event) => {
        const currentRoot = currentProject.value?.root ?? "";
        if (currentRoot && event.projectRoot && !sameRootPath(currentRoot, event.projectRoot)) {
          return;
        }
        const merged = new Set([...dirtyFiles.value, ...(event.changedPaths || [])]);
        dirtyFiles.value = [...merged].slice(-80);
      });
    }
  }

  function sameRootPath(left: string, right: string): boolean {
    return (
      left.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase() ===
      right.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase()
    );
  }

  async function withBusy<T>(key: string, callback: () => Promise<T>): Promise<T> {
    if (busy.value[key]) {
      throw new Error("操作正在进行，请稍候。");
    }
    busy.value[key] = true;
    try {
      return await callback();
    } catch (cause) {
      toastError(cause);
      throw cause;
    } finally {
      busy.value[key] = false;
    }
  }

  function scheduleAnalysis(project: Project, delay: number): void {
    if (analysisTimers.has(project.id)) {
      return;
    }
    const timer = setTimeout(() => {
      analysisTimers.delete(project.id);
      void completeAnalysis(project.id).catch((cause) => toastError(cause));
    }, delay);
    analysisTimers.set(project.id, timer);
  }

  function bootstrap(): Promise<void> {
    if (initialized.value) {
      return Promise.resolve();
    }
    if (bootstrapPromise) {
      return bootstrapPromise;
    }
    bootstrapPromise = runBootstrap().finally(() => {
      bootstrapPromise = null;
    });
    return bootstrapPromise;
  }

  async function completeAnalysis(projectId: string): Promise<void> {
    const project = projects.value.find((entry) => entry.id === projectId);
    if (!project || project.analysisStatus === "ready") {
      return;
    }
    try {
      const analysis: ProjectAnalysis = buildProjectAnalysis(project);
      const saved = await repo().saveAnalysis(projectId, analysis);
      replaceProject(saved);
    } catch (cause) {
      try {
        const failed = await repo().failAnalysis(projectId, errorMessage(cause));
        replaceProject(failed);
      } catch {
        // 标记失败也失败时保留原错误，避免产生未处理的 Promise 拒绝。
      }
      toastError(cause);
    }
  }

  async function analyzeProject(projectId: string): Promise<void> {
    // 手动重试要取消已排队的自动分析，避免两次结果互相覆盖。
    const scheduled = analysisTimers.get(projectId);
    if (scheduled) {
      clearTimeout(scheduled);
      analysisTimers.delete(projectId);
    }
    await withBusy(`analysis:${projectId}`, async () => {
      const project = await repo().beginAnalysis(projectId);
      replaceProject(project);
      try {
        if (repo().mode !== "host") {
          await new Promise<void>((resolve) => {
            setTimeout(resolve, ANALYSIS_DELAY_MS);
          });
        }
        const analysis = buildProjectAnalysis(project);
        const saved = await repo().saveAnalysis(projectId, analysis);
        replaceProject(saved);
      } catch (cause) {
        // 失败必须落盘，否则刷新后仍停在“分析中”且无法重试。
        try {
          replaceProject(await repo().failAnalysis(projectId, errorMessage(cause)));
        } catch {
          // 标记失败本身再失败时保留原错误，由 withBusy 统一提示。
        }
        throw cause;
      }
    });
  }

  async function runBootstrap(): Promise<void> {
    if (initialized.value) {
      return;
    }
    status.value = "loading";
    error.value = "";
    try {
      const loaded = await repo().listProjects();
      projects.value = loaded;
      workspaceRoot.value = repo().workspacePath?.() ?? "";
      if (repo().mode === "host") {
        // 宿主 doctor 需要探测外部命令与模型，放到首屏后异步刷新，避免阻塞渲染。
        environment.value = [];
        void refreshEnvironment();
      } else {
        environment.value = buildEnvironmentChecks();
      }
      const last = await repo().getLastProjectId();
      const preferred = last && loaded.some((project) => project.id === last) ? last : null;
      currentProjectId.value = preferred ?? loaded[0]?.id ?? null;
      // 先完成完整项目读取再标记 initialized；否则读取失败后“重新读取”会直接返回。
      if (currentProjectId.value) {
        const full = await repo().getProject(currentProjectId.value);
        if (!full) {
          throw new RepositoryError(
            "NOT_FOUND",
            "项目记录读取失败。",
            "确认宿主或本地数据可用后重新读取。",
          );
        }
        replaceProject(full);
      }
      lastContextProjectId = currentProjectId.value ?? "";
      status.value = "ready";
      initialized.value = true;
      installStorageListener();
      installFocusRefresh();
      installHostContextListener();
      installHostActivityObserver();
      for (const project of projects.value) {
        if (project.analysisStatus === "analyzing") {
          if (repo().mode === "host") {
            void completeAnalysis(project.id);
          } else {
            const started = new Date(project.analysisStartedAt || project.createdAt).getTime();
            const elapsed = Date.now() - started;
            if (elapsed >= ANALYSIS_DELAY_MS) {
              void completeAnalysis(project.id);
            } else {
              scheduleAnalysis(project, ANALYSIS_DELAY_MS - elapsed);
            }
          }
        }
        for (const task of project.tasks) {
          for (const run of task.runs) {
            if (run.status !== "running") {
              continue;
            }
            const lastActivity = new Date(run.heartbeatAt || run.startedAt).getTime();
            if (Number.isFinite(lastActivity) && Date.now() - lastActivity > RUN_STALE_MS) {
              void repo()
                .markRunInterrupted(project.id, run.id)
                .then(() => refreshProject(project.id))
                .catch((cause) => toastError(cause));
            } else {
              resumeRun(project, task, run);
            }
          }
        }
      }
    } catch (cause) {
      status.value = "error";
      error.value = errorMessage(cause);
    }
  }

  async function setCurrentProject(projectId: string): Promise<Project | null> {
    const seq = ++switchSeq;
    const full = await repo().getProject(projectId);
    if (seq !== switchSeq) {
      return null;
    }
    if (!full) {
      throw new RepositoryError(
        "NOT_FOUND",
        "项目不存在或已不可读。",
        "刷新项目列表后重新选择。",
      );
    }
    // 先完成读取再切换；上次项目写入失败不影响本次切换。
    try {
      await repo().setLastProjectId(projectId);
    } catch {
      useUiStore().showToast("已切换项目，但未能记住上次项目。", "info");
    }
    if (seq !== switchSeq) {
      return null;
    }
    if (currentProjectId.value !== projectId) {
      // 轮询绑定的是旧项目；离开时必须停止，避免继续查询隐藏项目。
      stopHostPolling();
    }
    currentProjectId.value = projectId;
    dirtyFiles.value = [];
    quickCheck.value = null;
    quickCheckError.value = "";
    clearQuickCheckTimers();
    replaceProject(full);
    void syncHostDiagnostics(projectId);
    const runningTaskEntry = full.tasks.find((task) =>
      task.runs.some((run) => run.status === "running"),
    );
    const runningRun = runningTaskEntry?.runs.find((run) => run.status === "running");
    if (runningTaskEntry && runningRun) {
      resumeRun(full, runningTaskEntry, runningRun);
    }
    return full;
  }

  function clearQuickCheckTimers(): void {
    for (const timer of quickCheckTimers.values()) {
      clearTimeout(timer);
    }
    quickCheckTimers.clear();
  }

  function resetRuntime(clearBusyFlags = false): void {
    simulator.stop();
    stopHostPolling();
    clearQuickCheckTimers();
    analysisTimers.forEach((timer) => clearTimeout(timer));
    analysisTimers.clear();
    environment.value = [];
    planFailures.value = {};
    if (clearBusyFlags) {
      busy.value = {};
    }
    quickCheck.value = null;
    quickCheckError.value = "";
  }

  async function createProject(input: CreateProjectInput): Promise<Project> {
    return withBusy("create-project", async () => {
      const project = await repo().createProject(input);
      replaceProject(project);
      currentProjectId.value = project.id;
      quickCheck.value = null;
      clearQuickCheckTimers();
      await repo().setLastProjectId(project.id);
      if (repo().mode === "host") {
        void completeAnalysis(project.id);
      } else {
        scheduleAnalysis(project, ANALYSIS_DELAY_MS);
      }
      return project;
    });
  }

  async function updateProject(
    projectId: string,
    patch: Partial<Pick<Project, "name" | "goal" | "archived">>,
  ): Promise<Project> {
    return withBusy(`project:${projectId}`, async () => {
      const project = await repo().updateProject(projectId, patch);
      replaceProject(project);
      return project;
    });
  }

  async function setFeatureIgnored(
    projectId: string,
    featureName: string,
    ignored: boolean,
  ): Promise<void> {
    await withBusy(`map:${projectId}`, async () => {
      const updated = await repo().setFeatureIgnored(projectId, featureName, ignored);
      replaceProject(updated);
    });
  }

  async function ensureDraft(projectId: string): Promise<Task> {
    return withBusy(`draft:${projectId}`, async () => {
      const task = await repo().ensureDraftTask(projectId);
      await refreshProject(projectId);
      return task;
    });
  }

  async function pickDirectory(): Promise<string | null> {
    const pick = repo().pickDirectory;
    if (!pick) {
      return null;
    }
    try {
      return await pick.call(repo());
    } catch (cause) {
      toastError(cause);
      throw cause;
    }
  }

  async function createTask(projectId: string, input: CreateTaskInput): Promise<Task> {
    return withBusy(`create-task:${projectId}`, async () => {
      const task = await repo().createTask(projectId, input);
      const project = await syncOrRefreshProject(projectId);
      if (project && project.activeTaskId !== task.id) {
        const updated = await repo().setActiveTask(projectId, task.id);
        replaceProject(updated);
      }
      return task;
    });
  }

  async function updateTask(projectId: string, taskId: string, patch: TaskPatch): Promise<Task> {
    return withBusy(`task:${taskId}`, async () => {
      const task = await repo().updateTask(projectId, taskId, patch);
      updateTaskInProject(projectId, task);
      return task;
    });
  }

  async function deleteTask(projectId: string, taskId: string): Promise<void> {
    return withBusy(`task-delete:${taskId}`, async () => {
      await repo().deleteTask(projectId, taskId);
      await syncOrRefreshProject(projectId);
    });
  }

  async function selectScope(projectId: string, taskId: string, itemIds: string[]): Promise<Task> {
    return withBusy(`scope:${taskId}`, async () => {
      const task = await repo().selectScope(projectId, taskId, itemIds);
      updateTaskInProject(projectId, task);
      return task;
    });
  }

  async function reviseScope(projectId: string, taskId: string): Promise<Task> {
    return withBusy(`revise:${taskId}`, async () => {
      const task = await repo().reviseScope(projectId, taskId);
      updateTaskInProject(projectId, task);
      return task;
    });
  }

  async function cancelRevision(projectId: string, taskId: string): Promise<Task> {
    return withBusy(`revise:${taskId}`, async () => {
      const task = await repo().cancelRevision(projectId, taskId);
      updateTaskInProject(projectId, task);
      return task;
    });
  }

  async function prepareRerun(
    projectId: string,
    taskId: string,
    itemIds?: string[],
  ): Promise<Plan> {
    return withBusy(`rerun:${taskId}`, async () => {
      const plan = await repo().prepareRerun(projectId, taskId, itemIds);
      await syncOrRefreshProject(projectId);
      return plan;
    });
  }

  async function setActiveTask(projectId: string, taskId: string): Promise<void> {
    await withBusy(`active:${taskId}`, async () => {
      const project = await repo().setActiveTask(projectId, taskId);
      await repo().setLastProjectId(projectId);
      replaceProject(project);
    });
  }

  async function generatePlan(projectId: string, taskId: string): Promise<Plan> {
    return withBusy(`plan:${taskId}`, async () => {
      try {
        const plan = await repo().generatePlan(projectId, taskId);
        await syncOrRefreshProject(projectId);
        delete planFailures.value[taskId];
        return plan;
      } catch (cause) {
        planFailures.value = {
          ...planFailures.value,
          [taskId]: errorMessage(cause),
        };
        throw cause;
      }
    });
  }

  async function repairRunDependencies(
    projectId: string,
    runId: string,
    packages: string[],
  ): Promise<void> {
    return withBusy(`repair:${runId}`, async () => {
      await repo().repairRunDependencies(projectId, runId, packages, OPERATOR);
      await syncOrRefreshProject(projectId);
    });
  }

  async function confirmAndStart(projectId: string, taskId: string, planId: string): Promise<Run> {
    return withBusy(`start:${taskId}`, async () => {
      const project = projects.value.find((entry) => entry.id === projectId);
      if (!project) {
        throw new RepositoryError("NOT_FOUND", "项目不存在。");
      }
      if (environment.value.length === 0) {
        // 宿主模式必须用真实 doctor 结果，不能拿本地演示检查冒充确认环境。
        environment.value =
          repo().mode === "host" ? await loadHostEnvironment() : buildEnvironmentChecks();
      }
      const input: ConfirmPlanInput = {
        operator: OPERATOR,
        environment: environment.value,
        sideEffects: sideEffectsFor(project),
      };
      try {
        await repo().confirmPlan(projectId, taskId, planId, input);
        const run = await repo().startRun(projectId, taskId, planId, OPERATOR);
        await refreshProject(projectId);
        if (repo().mode === "host") {
          startHostPolling(projectId, taskId, run.id);
        } else {
          const task = await repo()
            .getProject(projectId)
            .then((fresh) => fresh?.tasks.find((entry) => entry.id === taskId) ?? null);
          if (task) {
            await startSimulation(projectId, task, run);
          }
        }
        return run;
      } catch (cause) {
        await refreshProject(projectId);
        throw cause;
      }
    });
  }

  async function startTargetedRerun(
    projectId: string,
    taskId: string,
    planId: string,
    itemIds: string[],
  ): Promise<Run> {
    return withBusy(`start:${taskId}`, async () => {
      const targetRepo = repo();
      if (!targetRepo.startTargetedRerun) {
        throw new RepositoryError("CONFLICT", "当前仓库不支持零模型定向复测。");
      }
      const run = await targetRepo.startTargetedRerun(
        projectId,
        taskId,
        planId,
        itemIds,
        OPERATOR,
      );
      await refreshProject(projectId);
      if (targetRepo.mode === "host") {
        startHostPolling(projectId, taskId, run.id);
      } else {
        const task = projects.value
          .find((entry) => entry.id === projectId)
          ?.tasks.find((entry) => entry.id === taskId);
        if (task) {
          await startSimulation(projectId, task, run);
        }
      }
      return run;
    });
  }

  async function detectProjectConfig(projectId: string): Promise<ProjectConfig | null> {
    const fn = repo().detectProjectConfig;
    if (!fn) {
      return null;
    }
    return withBusy(`detect-config:${projectId}`, async () => fn.call(repo(), projectId));
  }

  async function previewEvidence(
    projectId: string,
    runId: string,
    ref: string,
  ): Promise<EvidencePreview | null> {
    const fn = repo().previewEvidence;
    if (!fn) {
      return null;
    }
    return fn.call(repo(), projectId, runId, ref);
  }

  async function fetchRunLogTail(projectId: string, runId: string): Promise<RunLogTailEntry[]> {
    const fn = repo().fetchRunLogTail;
    if (!fn) {
      return [];
    }
    return fn.call(repo(), projectId, runId);
  }

  const simulator = createSimulationDriver({
    repo,
    getProject: (projectId) => projects.value.find((entry) => entry.id === projectId),
    refreshProject,
    onError: toastError,
    onToast: (message, tone) => useUiStore().showToast(message, tone),
  });

  async function startSimulation(projectId: string, task: Task, run: Run): Promise<void> {
    await simulator.start(projectId, task, run);
  }

  function stopHostPolling(): void {
    pollToken += 1;
    if (hostPollTimer !== null) {
      clearTimeout(hostPollTimer);
      hostPollTimer = null;
    }
  }

  function startHostPolling(projectId: string, taskId: string, runId: string): void {
    stopHostPolling();
    const myToken = pollToken;
    let postprocessTicks = 0;
    let consecutiveErrors = 0;

    const pollInterval = (): number =>
      typeof document !== "undefined" && document.visibilityState === "hidden" ? 5000 : 1500;

    const scheduleNext = (ms?: number): void => {
      if (myToken !== pollToken) {
        return;
      }
      hostPollTimer = setTimeout(() => {
        void pollOnce();
      }, ms ?? pollInterval());
    };

    const pollOnce = async (): Promise<void> => {
      if (myToken !== pollToken) {
        return;
      }
      try {
        // 运行态只查 run.status，避免每次轮询再附带派生 evidence.list CLI 进程。
        const run = await repo().getRun?.(projectId, runId, false);
        if (myToken !== pollToken) {
          return;
        }
        consecutiveErrors = 0;
        if (!run) {
          scheduleNext();
          return;
        }
        const project = projects.value.find((entry) => entry.id === projectId);
        const task = project?.tasks.find((entry) => entry.id === taskId);
        if (!project || !task) {
          return;
        }
        const index = task.runs.findIndex((entry) => entry.id === run.id);
        if (index >= 0) {
          task.runs[index] = run;
        } else {
          task.runs.push(run);
        }
        task.currentRunId = run.id;
        task.stage = run.status === "running" ? "running" : "result";
        const processing =
          run.status !== "running" &&
          (run.postprocess === "running" || run.postprocess === "pending");
        if (processing) {
          postprocessTicks += 1;
          // 后处理独立轮询到 done/error；超时只提示“仍在生成”，不冒充运行结束。
          if (postprocessTicks < 400) {
            scheduleNext();
            return;
          }
          stopHostPolling();
          await refreshProject(projectId);
          useUiStore().showToast(
            "运行已结束，但报告后处理仍在进行；稍后刷新结果页查看。",
            "info",
          );
          return;
        }
        if (run.status !== "running") {
          stopHostPolling();
          await refreshProject(projectId);
          useUiStore().showToast(
            `运行结束：${
              run.verdict === "passed"
                ? "通过"
                : run.verdict === "failed"
                  ? "失败"
                  : "未完成"
            }。`,
            run.verdict === "passed" ? "success" : "info",
          );
          return;
        }
        scheduleNext();
      } catch (cause) {
        if (myToken !== pollToken) {
          return;
        }
        consecutiveErrors += 1;
        if (consecutiveErrors < 5) {
          scheduleNext(Math.min(5000, 1500 * consecutiveErrors));
          return;
        }
        stopHostPolling();
        toastError(cause);
      }
    };

    scheduleNext(1500);
  }

  function resumeRun(project: Project, task: Task, run: Run): void {
    if (repo().mode === "host") {
      startHostPolling(project.id, task.id, run.id);
      return;
    }
    void startSimulation(project.id, task, run);
  }

  async function stopRun(projectId: string, runId: string): Promise<Run> {
    return withBusy(`stop:${runId}`, async () => {
      simulator.stop();
      if (repo().mode === "host") {
        stopHostPolling();
        await repo().cancelRun(projectId, runId);
        const deadline = Date.now() + 60_000;
        let run = await repo().getRun?.(projectId, runId, false);
        while (run && run.status === "running" && Date.now() < deadline) {
          await new Promise<void>((resolve) => {
            setTimeout(resolve, 700);
          });
          run = await repo().getRun?.(projectId, runId, false);
        }
        await refreshProject(projectId);
        const refreshed = projects.value
          .find((entry) => entry.id === projectId)
          ?.tasks.flatMap((task) => task.runs)
          .find((entry) => entry.id === runId);
        if (refreshed) {
          return refreshed;
        }
        if (run) {
          return run;
        }
        throw new RepositoryError("NOT_FOUND", "运行记录不存在。");
      }
      const run = await repo().cancelRun(projectId, runId);
      await refreshProject(projectId);
      return run;
    });
  }

  async function recordManual(
    projectId: string,
    runId: string,
    caseId: string,
    input: { result: "passed" | "failed"; note: string; evidence: EvidenceInput[] },
  ): Promise<Run> {
    return withBusy(`manual:${caseId}`, async () => {
      const evidence = input.evidence.map((entry) => ({
        id: newId("ev"),
        name: entry.name,
        mediaType: entry.mediaType,
        bytes: entry.bytes,
        sha256: entry.sha256,
        note: entry.note,
        missing: false,
        truncated: false,
        addedAt: new Date().toISOString(),
        sourcePath: `runs/${runId}/evidence/${entry.name}`,
      }));
      const run = await repo().recordManual(projectId, runId, caseId, {
        result: input.result,
        note: input.note,
        evidence,
      });
      await refreshProject(projectId);
      return run;
    });
  }

  async function addEvidence(projectId: string, runId: string, input: EvidenceInput): Promise<Run> {
    return withBusy(`evidence:${runId}`, async () => {
      const run = await repo().addEvidence(projectId, runId, input);
      await refreshProject(projectId);
      return run;
    });
  }

  async function createIssue(projectId: string, input: IssueCreateInput): Promise<Issue> {
    return withBusy("issue-create", async () => {
      const issue = await repo().createIssue(projectId, input);
      await syncOrRefreshProject(projectId);
      return issue;
    });
  }

  async function updateIssue(
    projectId: string,
    issueId: string,
    patch: IssuePatch,
  ): Promise<Issue> {
    return withBusy(`issue:${issueId}`, async () => {
      const issue = await repo().updateIssue(projectId, issueId, patch);
      await syncOrRefreshProject(projectId);
      return issue;
    });
  }

  async function closeIssue(
    projectId: string,
    issueId: string,
    runId: string,
    reason: string,
  ): Promise<Issue> {
    return withBusy(`issue-close:${issueId}`, async () => {
      const issue = await repo().closeIssue(projectId, issueId, runId, reason);
      await syncOrRefreshProject(projectId);
      return issue;
    });
  }

  /** 合规关闭关联历史问题：按 pending -> in_progress -> retest -> closed 串联流转，绝不绕过状态机。 */
  async function closeLinkedIssue(
    projectId: string,
    issue: Issue,
    runId: string,
    reason: string,
  ): Promise<Issue> {
    const trimmed = reason.trim() || `新运行 ${runId} 已复测通过`;
    return withBusy(`issue-close:${issue.id}`, async () => {
      let currentStatus = issue.status;
      if (currentStatus === "pending") {
        await repo().updateIssue(projectId, issue.id, {
          status: "in_progress",
          reason: `结果页复测收口：${trimmed}`,
        });
        currentStatus = "in_progress";
      }
      if (currentStatus === "in_progress") {
        await repo().updateIssue(projectId, issue.id, {
          status: "retest",
          reason: `结果页关联通过运行 ${runId}：${trimmed}`,
        });
      }
      const closed = await repo().closeIssue(projectId, issue.id, runId, trimmed);
      await syncOrRefreshProject(projectId);
      return closed;
    });
  }

  /** 无锁只读双盲冲突评审（仅宿主模式调用，不阻塞阅读与 run.start）。 */
  async function reviewPlan(
    projectId: string,
    planId: string,
    refresh = false,
  ): Promise<{ highSeverityCount: number; reviews: PlanReviewItem[] }> {
    if (repo().mode !== "host") {
      return { highSeverityCount: 0, reviews: [] };
    }
    const raw = await hostApi.reviewPlan({ projectId, planId, refresh });
    const reviews = mapPlanReviews(raw.reviews);
    return {
      highSeverityCount:
        raw.high_severity_count ?? reviews.filter((r) => r.severity === "high").length,
      reviews,
    };
  }

  /** 将勾选的评审补丁用例通过 plan.update 持写锁并入计划，生成新 revision。 */
  async function mergePlanReviewCases(
    projectId: string,
    planId: string,
    selectedReviews: PlanReviewItem[],
  ): Promise<number> {
    return withBusy(`plan-merge:${planId}`, async () => {
      const detail = await hostApi.showPlan(projectId, planId);
      const existingCases = detail.plan.cases ?? [];
      const patchCases = selectedReviews.flatMap((r) => r.patchCases ?? []);
      const updated = await hostApi.updatePlan({
        projectId,
        planId,
        revision: detail.plan.revision,
        cases: [...existingCases, ...patchCases],
        aiReviews: selectedReviews.map((r) => ({
          review_id: r.reviewId,
          kind: r.kind,
          severity: r.severity,
          statement: r.statement,
          evidence: r.evidence,
          patch_cases: r.patchCases,
        })),
      });
      await syncOrRefreshProject(projectId);
      return updated.revision;
    });
  }

  /** 只读预览单条脚本用例修复 diff（不要求 --confirm、不落盘）。 */
  async function previewCaseRepair(input: {
    projectId: string;
    planId: string;
    caseId: string;
    runId?: string;
    repairedScript?: string;
    errorHint?: string;
  }): Promise<PlanRepairCaseResultDto> {
    return hostApi.repairPlanCase({ ...input, mode: "preview" });
  }

  /** 应用脚本修复更新 Plan 新 revision，并定向重跑受影响验收项（发起新 run，不改历史 run）。 */
  async function applyCaseRepairAndRerun(input: {
    projectId: string;
    taskId: string;
    planId: string;
    caseId: string;
    runId?: string;
    repairedScript?: string;
    errorHint?: string;
    expectedRevision?: number;
  }): Promise<Run> {
    return withBusy(`repair-case:${input.caseId}`, async () => {
      const applied = await hostApi.repairPlanCase({
        projectId: input.projectId,
        planId: input.planId,
        caseId: input.caseId,
        runId: input.runId,
        mode: "apply",
        repairedScript: input.repairedScript,
        errorHint: input.errorHint,
        expectedRevision: input.expectedRevision,
      });
      await syncOrRefreshProject(input.projectId);
      const targetItems = applied.acceptance_item_ids ?? [];
      if (targetItems.length > 0 && repo().startTargetedRerun) {
        return startTargetedRerun(input.projectId, input.taskId, input.planId, targetItems);
      }
      return confirmAndStart(input.projectId, input.taskId, input.planId);
    });
  }

  async function regenerateReport(projectId: string, runId: string): Promise<Report> {
    return withBusy(`report:${runId}`, async () => {
      const report = await repo().regenerateReport(projectId, runId);
      await syncOrRefreshProject(projectId);
      return report;
    });
  }

  async function saveOpinion(
    projectId: string,
    reportId: string,
    by: string,
    text: string,
  ): Promise<Report> {
    return withBusy(`opinion:${reportId}`, async () => {
      const report = await repo().saveOpinion(projectId, reportId, by, text);
      await syncOrRefreshProject(projectId);
      return report;
    });
  }

  async function exportReport(projectId: string, reportId: string) {
    try {
      const result = await repo().exportReport(projectId, reportId);
      if (result.path) {
        useUiStore().showToast(`已导出到 ${result.path}`, "success");
        return result;
      }
      if (!downloadText(result.filename, result.markdown, "text/markdown")) {
        throw new RepositoryError(
          "CONFLICT",
          "当前环境不能直接下载文件。",
          "使用桌面浏览器，或在 CLI 执行 report.export 导出。",
        );
      }
      useUiStore().showToast(
        `已导出 ${result.filename}：${result.bytes} 字节，${result.lines} 行。`,
        "success",
      );
      return result;
    } catch (cause) {
      toastError(cause);
      throw cause;
    }
  }

  async function saveDelivery(
    projectId: string,
    taskId: string,
    input: DeliveryInput,
  ): Promise<Task> {
    return withBusy(`delivery:${taskId}`, async () => {
      const task = await repo().saveDelivery(projectId, taskId, input);
      await syncOrRefreshProject(projectId);
      return task;
    });
  }

  async function draftDelivery(projectId: string, taskId: string): Promise<Delivery> {
    return withBusy(`delivery-draft:${taskId}`, async () => {
      return repo().draftDelivery(projectId, taskId);
    });
  }

  async function saveConfig(projectId: string, config: ProjectConfig): Promise<void> {
    await withBusy(`config:${projectId}`, async () => {
      const updated = await repo().saveConfig(projectId, config);
      replaceProject(updated);
    });
  }

  async function addConvention(projectId: string, input: ConventionInput): Promise<ProjectConvention[]> {
    return withBusy(`convention:${projectId}`, async () => {
      const fn = repo().addConvention;
      if (!fn) {
        throw new RepositoryError("CONFLICT", "当前仓库不支持配置错题本。");
      }
      const list = await fn.call(repo(), projectId, input);
      const target = projects.value.find((entry) => entry.id === projectId);
      if (target) {
        replaceProject({
          ...target,
          config: { ...target.config, conventions: list },
        });
      }
      useUiStore().showToast("已加入项目正常约定（错题本），后续将自动豁免同类误报。", "success");
      return list;
    });
  }

  async function removeConvention(projectId: string, conventionId: string): Promise<ProjectConvention[]> {
    return withBusy(`convention:${projectId}`, async () => {
      const fn = repo().removeConvention;
      if (!fn) {
        throw new RepositoryError("CONFLICT", "当前仓库不支持删除错题本。");
      }
      const list = await fn.call(repo(), projectId, conventionId);
      const target = projects.value.find((entry) => entry.id === projectId);
      if (target) {
        replaceProject({
          ...target,
          config: { ...target.config, conventions: list },
        });
      }
      useUiStore().showToast("已从项目错题本移除该约定。", "info");
      return list;
    });
  }

  async function openSourceLocation(file: string, line = 1, column = 1): Promise<boolean> {
    if (!file.trim()) {
      return false;
    }
    try {
      return await openHostSource(file, line, column);
    } catch (cause) {
      toastError(cause);
      return false;
    }
  }

  async function syncHostDiagnostics(projectId = currentProjectId.value ?? ""): Promise<number> {
    if (repo().mode !== "host") {
      return 0;
    }
    const target = projects.value.find((entry) => entry.id === projectId);
    if (!target) {
      await clearHostDiagnostics();
      return 0;
    }
    const items: HostDiagnosticInput[] = [];
    for (const issue of target.issues) {
      if (issue.status === "closed" || !issue.file) {
        continue;
      }
      items.push({
        file: issue.file,
        line: issue.line ?? 1,
        message: `[${issue.severity}${issue.confidence === "confirmed" ? " 铁证缺陷" : ""}] ${issue.title}：${issue.actual || issue.expected}`,
        severity: issue.confidence === "confirmed" || issue.severity === "P0" ? "error" : "warning",
        code: issue.id,
      });
      if (items.length >= 20) break;
    }
    for (const conflict of target.codeGraph?.conflicts ?? []) {
      if (items.length >= 30) break;
      if (!conflict.file) continue;
      items.push({
        file: conflict.file,
        line: 1,
        message: `[代码图跨层冲突] ${conflict.detail}`,
        severity: conflict.severity === "error" ? "error" : "warning",
        code: conflict.kind,
      });
    }
    if (items.length === 0) {
      await clearHostDiagnostics();
      return 0;
    }
    return publishHostDiagnostics(items);
  }

  async function saveGithubSnapshot(
    projectId: string,
    snapshot: Parameters<ProjectRepository["saveGithubSnapshot"]>[1],
  ): Promise<void> {
    await withBusy(`github:${projectId}`, async () => {
      const updated = await repo().saveGithubSnapshot(projectId, snapshot);
      replaceProject(updated);
    });
  }

  async function addModule(projectId: string, input: ModuleInput): Promise<void> {
    await withBusy(`module:${projectId}`, async () => {
      await repo().addModule(projectId, input);
      await syncOrRefreshProject(projectId);
    });
  }

  async function updateModule(
    projectId: string,
    moduleId: string,
    patch: Partial<ModuleInput>,
  ): Promise<void> {
    await withBusy(`module:${moduleId}`, async () => {
      await repo().updateModule(projectId, moduleId, patch);
      await syncOrRefreshProject(projectId);
    });
  }

  async function importLink(
    projectId: string,
    runId: string,
    link: Parameters<ProjectRepository["importLink"]>[2],
  ): Promise<void> {
    await withBusy(`link:${runId}`, async () => {
      await repo().importLink(projectId, runId, link);
      await refreshProject(projectId);
    });
  }

  async function importHostLink(projectId: string, runId: string): Promise<void> {
    await withBusy(`link:${runId}`, async () => {
      await repo().importLink(projectId, runId, {
        nodes: [],
        assertions: [],
        gaps: [],
        location: { known: false, path: "", line: 0, reason: "" },
        unknown: "",
      });
      await refreshProject(projectId);
    });
  }

  async function assertLink(
    projectId: string,
    runId: string,
    input: LinkAssertInput,
  ): Promise<void> {
    await withBusy(`link-assert:${runId}`, async () => {
      await repo().assertLink(projectId, runId, input);
      await refreshProject(projectId);
    });
  }

  async function loadReport(projectId: string, reportId: string): Promise<void> {
    const getReport = repo().getReport;
    if (!getReport) {
      return;
    }
    try {
      const report = await getReport.call(repo(), projectId, reportId);
      const project = projects.value.find((entry) => entry.id === projectId);
      if (!project || !report) {
        return;
      }
      const index = project.reports.findIndex((entry) => entry.id === report.id);
      if (index >= 0) {
        project.reports[index] = report;
      } else {
        project.reports.push(report);
      }
    } catch (cause) {
      toastError(cause);
    }
  }

  async function ensureReportVersions(projectId: string, runId: string): Promise<void> {
    const listReports = repo().listReports;
    if (!listReports) {
      return;
    }
    const reports = await listReports.call(repo(), projectId, runId);
    const project = projects.value.find((entry) => entry.id === projectId);
    if (!project) {
      return;
    }
    project.reports = [
      ...project.reports.filter((entry) => entry.runId !== runId),
      ...reports,
    ];
  }

  async function readGithub(
    projectId: string,
    repoName: string,
    ref: string,
  ): Promise<void> {
    if (!repo().readGithub) {
      throw new RepositoryError("CONFLICT", "当前仓库不支持真实 GitHub 读取。");
    }
    await withBusy(`github-read:${projectId}`, async () => {
      await repo().readGithub?.(projectId, repoName, ref);
      await refreshProject(projectId);
    });
  }

  async function openHostPath(path: string): Promise<void> {
    try {
      const bridge = hostBridge();
      if (!bridge?.openPath) {
        throw new RepositoryError("CONFLICT", "宿主不支持打开文件。");
      }
      const result = await bridge.openPath(path);
      if (!result.ok) {
        throw new RepositoryError("VALIDATION", result.error?.message ?? "无法打开文件。");
      }
    } catch (cause) {
      toastError(cause);
      throw cause;
    }
  }

  async function startQuickCheck(projectId: string): Promise<QuickCheck> {
    return withBusy(`quick-check:${projectId}`, async () => {
      const project = projects.value.find((entry) => entry.id === projectId);
      if (!project) {
        throw new RepositoryError("NOT_FOUND", "项目不存在。");
      }
      if (project.tasks.some((task) => task.runs.some((run) => run.status === "running"))) {
        throw new RepositoryError("RUN_ACTIVE", "有任务正在运行，完成或停止后再做快速检查。");
      }
      quickCheckError.value = "";
      const hostRepo = repo();
      if (hostRepo.mode === "host" && hostRepo.runQuickCheck) {
        quickCheck.value = {
          ...buildQuickCheckRun(project),
          // 宿主快速检查是真实 CLI 检查，不能标成本地模拟。
          simulated: false,
          status: "running",
        };
        try {
          const completed = await hostRepo.runQuickCheck(projectId);
          if (currentProjectId.value === projectId) {
            quickCheck.value = completed;
          }
          const latest = projects.value.find((entry) => entry.id === projectId);
          if (latest) {
            replaceProject({
              ...latest,
              quickChecks: [
                completed,
                ...latest.quickChecks.filter((entry) => entry.id !== completed.id),
              ].slice(0, 20),
            });
          }
          return completed;
        } catch (cause) {
          if (currentProjectId.value === projectId) {
            quickCheck.value = null;
            quickCheckError.value = errorMessage(cause);
          }
          throw cause;
        }
      }
      const quick = buildQuickCheckRun(project);
      quickCheck.value = quick;
      clearQuickCheckTimers();
      const timer = setTimeout(() => {
        quickCheckTimers.delete(projectId);
        if (currentProjectId.value !== projectId) {
          return;
        }
        const completed = completeQuickCheck(project, quick);
        quickCheck.value = completed;
        void repo()
          .recordQuickCheck(projectId, completed)
          .then((updated) => {
            if (currentProjectId.value === projectId) {
              replaceProject(updated);
            }
          })
          .catch((cause) => toastError(cause));
      }, 900);
      quickCheckTimers.set(projectId, timer);
      return quick;
    });
  }

  async function loadHostEnvironment(): Promise<EnvironmentCheck[]> {
    try {
      const facts = await callHost<DoctorFacts>("doctor", {});
      return mapDoctorFacts(facts);
    } catch (cause) {
      return [
        {
          key: "cli",
          name: "CLI 环境",
          value: "检查失败",
          ok: false,
          detail: cause instanceof Error ? cause.message : "无法连接 CLI。",
          repair: "确认 Trae 扩展与 Python 运行环境后重试。",
          risk: "high",
        },
      ];
    }
  }

  async function refreshEnvironment(): Promise<void> {
    environment.value =
      repo().mode === "host" ? await loadHostEnvironment() : buildEnvironmentChecks();
  }

  /** 调用宿主 doctor({ repair: true }) 一键修复缺失环境；本地模式仅刷新提示。 */
  async function repairEnvironment(): Promise<void> {
    if (repo().mode !== "host") {
      await refreshEnvironment();
      useUiStore().showToast("本地演示模式不支持自动修复环境。", "info");
      return;
    }
    return withBusy("doctor-repair", async () => {
      const facts = await callHost<DoctorFacts>("doctor", { repair: true });
      environment.value = mapDoctorFacts(facts);
      const stillMissing = environment.value.filter((item) => !item.ok).length;
      if (stillMissing === 0) {
        useUiStore().showToast("运行环境已检查并修复就绪。", "success");
      } else {
        useUiStore().showToast(`已尝试修复，仍有 ${stillMissing} 项需确认或手工配置。`, "info");
      }
    });
  }

  /** 对指定运行触发 AI 失败根因分析（宿主调用 analysis.run 并刷新项目数据）。 */
  async function runAnalysis(projectId: string, runId: string): Promise<boolean> {
    if (repo().mode !== "host") {
      useUiStore().showToast("本地演示模式已在运行结束时生成基线分析。", "info");
      return true;
    }
    return withBusy(`analysis-run:${runId}`, async () => {
      await callHost("analysis.run", { project_id: projectId, run_id: runId });
      await refreshProject(projectId);
      useUiStore().showToast("AI 根因诊断已完成，结果已更新。", "success");
      return true;
    });
  }

  async function backup(): Promise<void> {
    try {
      const payload = await repo().backup();
      if (payload.result.path) {
        useUiStore().showToast(`备份已创建：${payload.result.path}`, "success");
        return;
      }
      if (!downloadBytes(payload.result.filename, payload.data, "application/zip")) {
        throw new RepositoryError(
          "CONFLICT",
          "当前环境不能直接下载备份。",
          "使用桌面浏览器，或在 CLI 执行 backup.create。",
        );
      }
      useUiStore().showToast(
        `已导出备份：${payload.result.projects} 个项目，${payload.result.bytes} 字节。`,
        "success",
      );
    } catch (cause) {
      toastError(cause);
      throw cause;
    }
  }

  async function restoreBackup(data: string | ArrayBuffer): Promise<void> {
    return withBusy("restore", async () => {
      resetRuntime();
      const restored = await repo().restore(data);
      projects.value = restored;
      currentProjectId.value = restored[0]?.id ?? null;
      initialized.value = false;
      await bootstrap();
      useUiStore().showToast("备份已恢复。", "success");
    });
  }

  async function clearData(): Promise<void> {
    resetRuntime(true);
    await repo().clear();
    projects.value = [];
    currentProjectId.value = null;
    initialized.value = false;
    status.value = "ready";
    error.value = "";
  }

  function clearQuickCheck(): void {
    quickCheck.value = null;
  }

  const localMode = computed(() => repo().mode === "local");

  return {
    localMode,
    projects,
    currentProjectId,
    status,
    error,
    busy,
    planFailures,
    environment,
    quickCheck,
    quickCheckError,
    initialized,
    currentProject,
    currentTask,
    mode,
    capabilities,
    draftTask,
    tasks,
    runningTask,
    currentPlan,
    currentRun,
    projectIssues,
    openIssues,
    projectRuns,
    projectReports,
    loading,
    hasRunningRun,
    busyKey,
    issueById,
    runById,
    reportById,
    planById,
    bootstrap,
    analyzeProject,
    setCurrentProject,
    createProject,
    updateProject,
    setFeatureIgnored,
    ensureDraft,
    pickDirectory,
    createTask,
    updateTask,
    deleteTask,
    selectScope,
    reviseScope,
    cancelRevision,
    prepareRerun,
    setActiveTask,
    generatePlan,
    repairRunDependencies,
    confirmAndStart,
    startTargetedRerun,
    detectProjectConfig,
    previewEvidence,
    fetchRunLogTail,
    stopRun,
    recordManual,
    addEvidence,
    createIssue,
    updateIssue,
    closeIssue,
    closeLinkedIssue,
    reviewPlan,
    mergePlanReviewCases,
    previewCaseRepair,
    applyCaseRepairAndRerun,
    regenerateReport,
    saveOpinion,
    exportReport,
    saveDelivery,
    draftDelivery,
    runAnalysis,
    saveConfig,
    addConvention,
    removeConvention,
    openSourceLocation,
    syncHostDiagnostics,
    dirtyFiles,
    hostCapabilities,
    hostGit,
    saveGithubSnapshot,
    addModule,
    updateModule,
    importLink,
    importHostLink,
    assertLink,
    loadReport,
    ensureReportVersions,
    readGithub,
    openHostPath,
    startQuickCheck,
    refreshEnvironment,
    repairEnvironment,
    backup,
    restoreBackup,
    clearData,
    clearQuickCheck,
    refreshProject,
    refreshAll,
    refreshProjects,
    workspaceRoot,
  };
});
