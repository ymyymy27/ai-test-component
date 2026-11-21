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
  Project,
  ProjectConfig,
  QuickCheck,
  Report,
  Run,
  Task,
} from "@/domain/types";
import { RepositoryError } from "@/repositories/errors";
import { HostRepository } from "@/repositories/HostRepository";
import { LocalStorageRepository } from "@/repositories/LocalStorageRepository";
import { callHost, hostBridge, HostError } from "@/host/bridge";
import { mapDoctorFacts } from "@/host/environment";
import type { DoctorFacts } from "@/host/dto";
import type {
  ConfirmPlanInput,
  CreateProjectInput,
  CreateTaskInput,
  DeliveryInput,
  EvidenceInput,
  IssueCreateInput,
  IssuePatch,
  LinkAssertInput,
  ModuleInput,
  ProjectRepository,
  RuleInput,
  TaskPatch,
} from "@/repositories/ProjectRepository";
import { buildEnvironmentChecks } from "@/services/environmentService";
import { downloadBytes, downloadText } from "@/services/exportService";
import { simulateOutcome } from "@/services/execution";
import { RunSimulator } from "@/services/runSimulator";
import { useUiStore } from "./ui";

const OPERATOR = "本机用户";
const ANALYSIS_DELAY_MS = 800;
const RUN_STALE_MS = 10 * 60 * 1000;

let repository: ProjectRepository | null = null;
let repositoryLocked = false;
let simulator: RunSimulator | null = null;
let simulatorRunId = "";
let simulatorProjectId = "";

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

function errorMessage(error: unknown): string {
  if (error instanceof RepositoryError || error instanceof HostError) {
    return error.nextStep ? `${error.message} ${error.nextStep}` : error.message;
  }
  return error instanceof Error ? error.message : "操作失败，请重试。";
}

export const useWorkspaceStore = defineStore("workspace", () => {
  const projects = ref<Project[]>([]);
  const currentProjectId = ref<string | null>(null);
  const status = ref<"idle" | "loading" | "ready" | "error">("idle");
  const error = ref("");
  const busy = ref<Record<string, boolean>>({});
  const planFailures = ref<Record<string, string>>({});
  const environment = ref<EnvironmentCheck[]>([]);
  const quickCheck = ref<QuickCheck | null>(null);
  const initialized = ref(false);
  const quickCheckError = ref("");
  let bootstrapPromise: Promise<void> | null = null;
  const analysisTimers = new Map<string, ReturnType<typeof setTimeout>>();
  const quickCheckTimers = new Map<string, ReturnType<typeof setTimeout>>();
  let hostPollTimer: ReturnType<typeof setInterval> | null = null;
  let storageListenerInstalled = false;

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

  async function refreshProject(projectId: string): Promise<Project | null> {
    const project = await repo().getProject(projectId);
    if (project) {
      replaceProject(project);
    }
    return project;
  }

  async function refreshAll(): Promise<void> {
    projects.value = await repo().listProjects();
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
        await new Promise<void>((resolve) => {
          setTimeout(resolve, ANALYSIS_DELAY_MS);
        });
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
      environment.value =
        repo().mode === "host" ? await loadHostEnvironment() : buildEnvironmentChecks();
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
      status.value = "ready";
      initialized.value = true;
      installStorageListener();
      for (const project of projects.value) {
        if (project.analysisStatus === "analyzing") {
          const started = new Date(project.analysisStartedAt || project.createdAt).getTime();
          const elapsed = Date.now() - started;
          if (elapsed >= ANALYSIS_DELAY_MS) {
            void completeAnalysis(project.id);
          } else {
            scheduleAnalysis(project, ANALYSIS_DELAY_MS - elapsed);
          }
        }
        for (const task of project.tasks) {
          for (const run of task.runs) {
            if (run.status !== "running") {
              continue;
            }
            const startedAt = new Date(run.startedAt).getTime();
            if (Date.now() - startedAt > RUN_STALE_MS) {
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
    const full = await repo().getProject(projectId);
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
    if (currentProjectId.value !== projectId) {
      // 轮询绑定的是旧项目；离开时必须停止，避免继续查询隐藏项目。
      stopHostPolling();
    }
    currentProjectId.value = projectId;
    quickCheck.value = null;
    quickCheckError.value = "";
    clearQuickCheckTimers();
    replaceProject(full);
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
    simulator?.stop();
    simulator = null;
    simulatorRunId = "";
    simulatorProjectId = "";
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
      projects.value.push(project);
      currentProjectId.value = project.id;
      quickCheck.value = null;
      clearQuickCheckTimers();
      await repo().setLastProjectId(project.id);
      scheduleAnalysis(project, ANALYSIS_DELAY_MS);
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
      await repo().setFeatureIgnored(projectId, featureName, ignored);
      await refreshProject(projectId);
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
      const project = await refreshProject(projectId);
      if (project && project.activeTaskId !== task.id) {
        await repo().setActiveTask(projectId, task.id);
        await refreshProject(projectId);
      }
      return task;
    });
  }

  async function updateTask(projectId: string, taskId: string, patch: TaskPatch): Promise<Task> {
    return withBusy(`task:${taskId}`, async () => {
      const task = await repo().updateTask(projectId, taskId, patch);
      await refreshProject(projectId);
      return task;
    });
  }

  async function deleteTask(projectId: string, taskId: string): Promise<void> {
    return withBusy(`task-delete:${taskId}`, async () => {
      await repo().deleteTask(projectId, taskId);
      await refreshProject(projectId);
    });
  }

  async function selectScope(projectId: string, taskId: string, itemIds: string[]): Promise<Task> {
    return withBusy(`scope:${taskId}`, async () => {
      const task = await repo().selectScope(projectId, taskId, itemIds);
      await refreshProject(projectId);
      return task;
    });
  }

  async function reviseScope(projectId: string, taskId: string): Promise<Task> {
    return withBusy(`revise:${taskId}`, async () => {
      const task = await repo().reviseScope(projectId, taskId);
      await refreshProject(projectId);
      return task;
    });
  }

  async function cancelRevision(projectId: string, taskId: string): Promise<Task> {
    return withBusy(`revise:${taskId}`, async () => {
      const task = await repo().cancelRevision(projectId, taskId);
      await refreshProject(projectId);
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
      await refreshProject(projectId);
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
        await refreshProject(projectId);
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
      await refreshProject(projectId);
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

  async function startSimulation(projectId: string, task: Task, run: Run): Promise<void> {
    if (simulator && simulatorRunId === run.id) {
      return;
    }
    if (simulator) {
      const previousRun = simulatorRunId;
      const previousProject = simulatorProjectId;
      simulator.stop();
      // 被替换的旧运行必须落为中断，不能永久停在 running。
      if (previousRun && previousProject) {
        void repo()
          .markRunInterrupted(previousProject, previousRun)
          .catch((cause) => toastError(cause));
      }
    }
    const instance = new RunSimulator();
    simulator = instance;
    simulatorRunId = run.id;
    simulatorProjectId = projectId;
    const taskRunCount = task.runs.length;
    const project = projects.value.find((entry) => entry.id === projectId);
    if (!project) {
      return;
    }
    const manualCases = run.cases.filter(
      (testCase) => testCase.layer === "L3" && testCase.result === "not_run",
    );
    await Promise.all(
      manualCases.map((testCase) =>
        repo().recordRunCase(projectId, run.id, testCase.id, {
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
      await refreshProject(projectId);
    }
    const autoCases = run.cases.filter(
      (testCase) => testCase.layer !== "L3" && testCase.result === "not_run",
    );
    if (autoCases.length === 0) {
      await finishSimulation(projectId, run.id);
      return;
    }
    instance.start(
      autoCases.map((testCase) => () => {
        const outcome = simulateOutcome(project, task, run, testCase, taskRunCount);
        void repo()
          .recordRunCase(projectId, run.id, testCase.id, outcome)
          .then(() => refreshProject(projectId))
          .catch((cause) => {
            instance.stop();
            if (simulator === instance) {
              simulator = null;
              simulatorRunId = "";
              simulatorProjectId = "";
            }
            toastError(cause);
          });
      }),
      () => {
        void finishSimulation(projectId, run.id);
      },
    );
  }

  async function finishSimulation(projectId: string, runId: string): Promise<void> {
    try {
      await repo().completeRun(projectId, runId);
      await refreshProject(projectId);
      simulator = null;
      simulatorRunId = "";
      simulatorProjectId = "";
      const project = projects.value.find((entry) => entry.id === projectId);
      const run = project
        ? project.tasks.flatMap((task) => task.runs).find((entry) => entry.id === runId)
        : null;
      useUiStore().showToast(
        run ? `运行结束：${run.verdict === "passed" ? "通过" : "已生成结果"}。` : "运行结束。",
        run?.verdict === "passed" ? "success" : "info",
      );
    } catch (cause) {
      toastError(cause);
      simulator = null;
      simulatorRunId = "";
    }
  }

  function stopHostPolling(): void {
    if (hostPollTimer !== null) {
      clearInterval(hostPollTimer);
      hostPollTimer = null;
    }
  }

  function startHostPolling(projectId: string, taskId: string, runId: string): void {
    stopHostPolling();
    let postprocessTicks = 0;
    hostPollTimer = setInterval(() => {
      void (async () => {
        try {
          const run = await repo().getRun?.(projectId, runId);
          if (!run) {
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
          }
        } catch (cause) {
          stopHostPolling();
          toastError(cause);
        }
      })();
    }, 1500);
  }

  function resumeRun(project: Project, task: Task, run: Run): void {
    if (repo().mode === "host") {
      startHostPolling(project.id, task.id, run.id);
      return;
    }
    void startSimulation(project.id, task, run);
  }

  async function stopRun(projectId: string, runId: string): Promise<Run> {
    simulator?.stop();
    simulator = null;
    simulatorRunId = "";
    if (repo().mode === "host") {
      stopHostPolling();
      await repo().cancelRun(projectId, runId);
      const deadline = Date.now() + 60_000;
      let run = await repo().getRun?.(projectId, runId);
      while (run && run.status === "running" && Date.now() < deadline) {
        await new Promise<void>((resolve) => {
          setTimeout(resolve, 700);
        });
        run = await repo().getRun?.(projectId, runId);
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
      await refreshProject(projectId);
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
      await refreshProject(projectId);
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
      await refreshProject(projectId);
      return issue;
    });
  }

  async function regenerateReport(projectId: string, runId: string): Promise<Report> {
    return withBusy(`report:${runId}`, async () => {
      const report = await repo().regenerateReport(projectId, runId);
      await refreshProject(projectId);
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
      await refreshProject(projectId);
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
      await refreshProject(projectId);
      return task;
    });
  }

  async function draftDelivery(projectId: string, taskId: string): Promise<Delivery> {
    return withBusy(`delivery-draft:${taskId}`, async () => {
      return repo().draftDelivery(projectId, taskId);
    });
  }

  async function applyTemplate(
    projectId: string,
    taskId: string,
    templateId: string,
  ): Promise<Task> {
    return withBusy(`template:${taskId}`, async () => {
      const task = await repo().applyTemplate(projectId, taskId, templateId);
      await refreshProject(projectId);
      return task;
    });
  }

  async function saveRule(projectId: string, input: RuleInput): Promise<void> {
    await withBusy(`rule:${projectId}`, async () => {
      await repo().saveRule(projectId, input);
      await refreshProject(projectId);
    });
  }

  async function bindRule(projectId: string, ruleId: string): Promise<void> {
    await withBusy(`rule-bind:${projectId}`, async () => {
      await repo().bindRule(projectId, ruleId);
      await refreshProject(projectId);
    });
  }

  async function saveConfig(projectId: string, config: ProjectConfig): Promise<void> {
    await withBusy(`config:${projectId}`, async () => {
      await repo().saveConfig(projectId, config);
      await refreshProject(projectId);
    });
  }

  async function saveGithubSnapshot(
    projectId: string,
    snapshot: Parameters<ProjectRepository["saveGithubSnapshot"]>[1],
  ): Promise<void> {
    await withBusy(`github:${projectId}`, async () => {
      await repo().saveGithubSnapshot(projectId, snapshot);
      await refreshProject(projectId);
    });
  }

  async function addModule(projectId: string, input: ModuleInput): Promise<void> {
    await withBusy(`module:${projectId}`, async () => {
      await repo().addModule(projectId, input);
      await refreshProject(projectId);
    });
  }

  async function updateModule(
    projectId: string,
    moduleId: string,
    patch: Partial<ModuleInput>,
  ): Promise<void> {
    await withBusy(`module:${moduleId}`, async () => {
      await repo().updateModule(projectId, moduleId, patch);
      await refreshProject(projectId);
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
        quickCheck.value = completed;
        await refreshProject(projectId);
        return completed;
      } catch (cause) {
        quickCheck.value = null;
        quickCheckError.value = errorMessage(cause);
        toastError(cause);
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
    stopRun,
    recordManual,
    addEvidence,
    createIssue,
    updateIssue,
    closeIssue,
    regenerateReport,
    saveOpinion,
    exportReport,
    saveDelivery,
    draftDelivery,
    applyTemplate,
    saveRule,
    bindRule,
    saveConfig,
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
    backup,
    restoreBackup,
    clearData,
    clearQuickCheck,
    refreshProject,
    refreshAll,
  };
});
