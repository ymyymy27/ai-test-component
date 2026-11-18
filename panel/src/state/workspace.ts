/** 任务台账状态：任务 → 计划 → 运行。
 *
 * 面板只消费 CLI 返回的事实；这里只负责加载、选择与恢复，不计算结论。
 */
import { reactive } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import { readHostContext, readHostState, writeHostState, type HostContext } from "@/api/host";
import type {
  DoctorFacts,
  Issue,
  IssueSummary,
  Plan,
  PlanDetail,
  Project,
  ProjectSummary,
  Report,
  ReportVersion,
  RunDetail,
  RunSummary,
  TargetCandidate,
  Task,
  TaskSummary,
  Verdict,
} from "@/api/types";

export type AppView = "test" | "issues" | "history" | "settings";
export type TestStage = "no-project" | "no-task" | "plan" | "running" | "result";
export type Sheet = "plan" | "report" | null;

interface PersistedState {
  view?: AppView;
  projectId?: string;
  taskId?: string;
  planId?: string;
  runId?: string;
}

interface WorkspaceState {
  ready: boolean;
  booting: boolean;
  bootError: FriendlyError | null;
  view: AppView;
  stage: TestStage;
  sheet: Sheet;
  projects: ProjectSummary[];
  project: Project | null;
  doctor: DoctorFacts | null;
  env: "ok" | "warn" | "error" | "unknown";
  tasks: TaskSummary[];
  task: Task | null;
  candidates: TargetCandidate[];
  candidateIndex: number;
  candidateLoading: boolean;
  candidateError: FriendlyError | null;
  goal: string;
  planIds: string[];
  plans: Plan[];
  plan: Plan | null;
  planDetail: PlanDetail | null;
  scope: string[];
  run: RunDetail | null;
  historical: boolean;
  history: RunSummary[];
  issues: IssueSummary[];
  issue: Issue | null;
  reports: ReportVersion[];
  report: Report | null;
  loading: Record<string, boolean>;
  notice: string;
  lastError: FriendlyError | null;
  hostContext: HostContext | null;
  prefillRoot: string;
  addProjectOpen: boolean;
}

export const workspace = reactive<WorkspaceState>({
  ready: false,
  booting: false,
  bootError: null,
  view: "test",
  stage: "no-project",
  sheet: null,
  projects: [],
  project: null,
  doctor: null,
  env: "unknown",
  tasks: [],
  task: null,
  candidates: [],
  candidateIndex: -1,
  candidateLoading: false,
  candidateError: null,
  goal: "",
  planIds: [],
  plans: [],
  plan: null,
  planDetail: null,
  scope: [],
  run: null,
  historical: false,
  history: [],
  issues: [],
  issue: null,
  reports: [],
  report: null,
  loading: {},
  notice: "",
  lastError: null,
  hostContext: null,
  prefillRoot: "",
  addProjectOpen: false,
});

let contextWaiters: ((context: HostContext | null) => void)[] = [];
let pollTimer: ReturnType<typeof setInterval> | null = null;

export function applyHostContext(raw: unknown): void {
  if (!raw || typeof raw !== "object") {
    return;
  }
  const data = raw as HostContext;
  const context: HostContext = {
    projectId: typeof data.projectId === "string" ? data.projectId : "",
    projectRoot: typeof data.projectRoot === "string" ? data.projectRoot : "",
    pythonPath: typeof data.pythonPath === "string" ? data.pythonPath : "",
    workspacePath: typeof data.workspacePath === "string" ? data.workspacePath : "",
    workspaceFolderCount:
      typeof data.workspaceFolderCount === "number" ? data.workspaceFolderCount : 0,
  };
  workspace.hostContext = context;
  workspace.prefillRoot = context.projectId ? "" : (context.projectRoot ?? "");
  const waiters = contextWaiters;
  contextWaiters = [];
  for (const resolve of waiters) {
    resolve(context);
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("message", (event: MessageEvent) => {
    const data = event.data as { type?: unknown } | null;
    if (data && typeof data === "object" && data.type === "context") {
      applyHostContext(data);
    }
  });
}

async function waitHostContext(): Promise<HostContext | null> {
  if (workspace.hostContext) {
    return workspace.hostContext;
  }
  const cached = readHostContext();
  if (cached) {
    applyHostContext(cached);
    return workspace.hostContext;
  }
  return await Promise.race([
    new Promise<HostContext | null>((resolve) => {
      contextWaiters.push(resolve);
    }),
    new Promise<HostContext | null>((resolve) => {
      setTimeout(() => resolve(null), 1500);
    }),
  ]);
}

function persist(): void {
  const state: PersistedState = {
    view: workspace.view,
    projectId: workspace.project?.project_id,
    taskId: workspace.task?.task_id,
    planId: workspace.plan?.plan_id,
    runId: workspace.historical ? "" : workspace.run?.run.run_id,
  };
  writeHostState(state);
}

export function persistState(): void {
  persist();
}

function setLoading(key: string, value: boolean): void {
  workspace.loading[key] = value;
}

export function isBusy(key: string): boolean {
  return Boolean(workspace.loading[key]);
}

export function flash(message: string): void {
  workspace.notice = message;
}

export function clearNotice(): void {
  workspace.notice = "";
}

export function clearError(): void {
  workspace.lastError = null;
}

export function dismissBootError(): void {
  workspace.bootError = null;
}

export function computeEnv(facts: DoctorFacts | null): "ok" | "warn" | "error" | "unknown" {
  if (!facts) {
    return "unknown";
  }
  const core = [facts.workspace?.ok, facts.python?.ok, facts.package?.ok];
  if (core.some((value) => value === false)) {
    return "error";
  }
  return facts.model?.ok || facts.github?.ok || facts.proxy?.ok ? "ok" : "warn";
}

function errorOf(error: unknown): FriendlyError {
  return describeError(error);
}

export function setView(view: AppView): void {
  workspace.view = view;
  workspace.lastError = null;
  if (view === "issues") {
    void loadIssues();
  } else if (view === "history") {
    void loadHistory();
  }
  persist();
}

export function openAddProject(): void {
  workspace.addProjectOpen = true;
}

export function closeAddProject(): void {
  workspace.addProjectOpen = false;
}

async function loadProjects(): Promise<void> {
  const data = await api.listProjects();
  workspace.projects = data.projects;
}

export async function loadHistory(): Promise<void> {
  if (!workspace.project) {
    workspace.history = [];
    return;
  }
  try {
    const data = await api.listRuns(workspace.project.project_id, 50);
    workspace.history = data.runs;
  } catch (error) {
    workspace.lastError = errorOf(error);
  }
}

export async function loadIssues(): Promise<void> {
  if (!workspace.project) {
    workspace.issues = [];
    return;
  }
  try {
    const data = await api.listIssues(workspace.project.project_id);
    workspace.issues = data.issues;
  } catch (error) {
    workspace.lastError = errorOf(error);
  }
}

async function loadPlans(planId?: string, runId?: string): Promise<void> {
  if (!workspace.project) {
    return;
  }
  const ids = workspace.planIds.length ? workspace.planIds : [];
  const plans: Plan[] = [];
  for (const id of ids) {
    try {
      const detail = await api.showPlan(workspace.project.project_id, id);
      plans.push(detail.plan);
    } catch {
      // 单个旧计划损坏时仍允许打开其他计划。
    }
  }
  workspace.plans = plans.sort((a, b) => {
    const number = (b.plan_no ?? 0) - (a.plan_no ?? 0);
    return number !== 0 ? number : b.revision - a.revision;
  });
  const saved = planId ? workspace.plans.find((item) => item.plan_id === planId) : undefined;
  const selected = saved ?? workspace.plans[0] ?? null;
  if (!selected) {
    workspace.plan = null;
    workspace.planDetail = null;
    workspace.run = null;
    workspace.historical = false;
    workspace.scope = [];
    workspace.stage = "plan";
    return;
  }
  await selectPlan(selected.plan_id, runId);
}

async function selectPlan(planId: string, runId?: string): Promise<void> {
  if (!workspace.project) {
    return;
  }
  const detail = await api.showPlan(workspace.project.project_id, planId);
  workspace.plan = detail.plan;
  workspace.planDetail = detail;
  const active = new Set(
    (workspace.task?.acceptance_items ?? [])
      .filter((item) => item.status === "active")
      .map((item) => item.item_id),
  );
  workspace.scope =
    active.size === 0
      ? []
      : detail.plan.scope.acceptance_item_ids.filter((itemId) => active.has(itemId));
  workspace.sheet = null;
  if (runId) {
    const loaded = await loadRun(runId);
    if (loaded) {
      return;
    }
  }
  if (!detail.stale) {
    const recent = workspace.history.find(
      (item) => item.plan_id === planId && item.status !== "cancelled",
    );
    if (recent) {
      const loaded = await loadRun(recent.run_id);
      if (loaded) {
        return;
      }
    }
  }
  workspace.run = null;
  workspace.historical = false;
  workspace.stage = "plan";
  persist();
}

async function loadTask(taskId: string, planId?: string, runId?: string): Promise<void> {
  if (!workspace.project) {
    return;
  }
  const data = await api.showTask(workspace.project.project_id, taskId);
  workspace.task = data.task;
  workspace.planIds = data.plans ?? [];
  await loadPlans(planId, runId);
  if (!workspace.planIds.length) {
    workspace.scope = data.task.acceptance_items
      .filter((item) => item.status === "active")
      .map((item) => item.item_id);
  }
  persist();
}

export async function openProject(
  projectId: string,
  saved?: { taskId?: string; planId?: string; runId?: string },
): Promise<void> {
  setLoading("project", true);
  workspace.lastError = null;
  try {
    const data = await api.showProject(projectId);
    workspace.project = data.project;
    workspace.task = null;
    workspace.tasks = [];
    workspace.plan = null;
    workspace.planDetail = null;
    workspace.planIds = [];
    workspace.plans = [];
    workspace.run = null;
    workspace.historical = false;
    workspace.sheet = null;
    workspace.issue = null;
    workspace.issues = [];
    workspace.report = null;
    workspace.reports = [];
    workspace.candidates = [];
    workspace.scope = [];
    const tasks = await api.listTasks(projectId);
    workspace.tasks = tasks.tasks;
    await loadHistory();
    const targetTask =
      (saved?.taskId && tasks.tasks.find((item) => item.task_id === saved.taskId)) ??
      tasks.tasks[0];
    if (targetTask) {
      await loadTask(targetTask.task_id, saved?.planId, saved?.runId);
    } else {
      workspace.stage = "no-task";
      await loadCandidates();
    }
    persist();
  } catch (error) {
    workspace.bootError = errorOf(error);
  } finally {
    setLoading("project", false);
  }
}

export async function switchProject(projectId: string): Promise<void> {
  await openProject(projectId);
}

export async function addProject(root: string, name?: string): Promise<boolean> {
  setLoading("projectAdd", true);
  workspace.lastError = null;
  try {
    const created = await api.addProject(root.trim(), name);
    await loadProjects();
    await openProject(created.project_id);
    flash(created.existing ? "这个目录已经登记过，已切换到现有项目" : "项目已登记");
    return true;
  } catch (error) {
    workspace.bootError = errorOf(error);
    return false;
  } finally {
    setLoading("projectAdd", false);
  }
}

export async function beginNewGoal(): Promise<void> {
  workspace.task = null;
  workspace.plan = null;
  workspace.planDetail = null;
  workspace.planIds = [];
  workspace.plans = [];
  workspace.scope = [];
  workspace.run = null;
  workspace.historical = false;
  workspace.sheet = null;
  workspace.stage = "no-task";
  await loadCandidates();
  persist();
}

export async function loadCandidates(): Promise<void> {
  if (!workspace.project) {
    return;
  }
  workspace.candidateLoading = true;
  workspace.candidateError = null;
  try {
    const data = await api.suggestTargets(workspace.project.project_id);
    const candidates =
      data.candidates.length > 0
        ? data.candidates
        : data.fallback
          ? [
              {
                title: data.fallback,
                reason: "通用目标",
                source: "fallback",
                intent: data.fallback_intent ?? "smoke",
              },
            ]
          : [];
    workspace.candidates = candidates;
    workspace.candidateIndex = candidates.length ? 0 : -1;
    workspace.goal = candidates[0]?.title ?? "";
  } catch (error) {
    workspace.candidateError = errorOf(error);
    workspace.candidates = [];
    workspace.candidateIndex = -1;
    workspace.goal = "";
  } finally {
    workspace.candidateLoading = false;
  }
}

export function chooseCandidate(index: number): void {
  const candidate = workspace.candidates[index];
  if (candidate) {
    workspace.candidateIndex = index;
    workspace.goal = candidate.title;
  }
}

export async function createTask(): Promise<boolean> {
  if (!workspace.project) {
    return false;
  }
  const goal = workspace.goal.trim();
  if (!goal) {
    workspace.lastError = {
      title: "还没有验证目标",
      detail: "先选一个建议目标，或自己写一句要验证什么。",
      next: "写清这次改了什么、想验证什么，再开始。",
    };
    return false;
  }
  const candidate = workspace.candidates[workspace.candidateIndex];
  setLoading("taskAdd", true);
  workspace.lastError = null;
  try {
    const created = await api.addTask({
      projectId: workspace.project.project_id,
      title: goal,
      goal,
      requirement: goal,
      intent: candidate?.intent ?? "business",
    });
    await loadTask(created.task_id);
    workspace.stage = "plan";
    persist();
    return true;
  } catch (error) {
    workspace.lastError = errorOf(error);
    return false;
  } finally {
    setLoading("taskAdd", false);
  }
}

export async function selectTask(taskId: string): Promise<void> {
  await loadTask(taskId);
}

export function setScope(items: string[]): void {
  workspace.scope = [...items];
}

export async function generatePlan(): Promise<boolean> {
  if (!workspace.project || !workspace.task) {
    return false;
  }
  if (workspace.scope.length === 0 && workspace.task.intent !== "smoke") {
    workspace.lastError = {
      title: "还没有选范围",
      detail: "至少选择一个验收项再生成计划。",
      next: "在验收项列表里勾选这次要验证的项。",
    };
    return false;
  }
  setLoading("planGenerate", true);
  workspace.lastError = null;
  try {
    const generated = await api.generatePlan({
      projectId: workspace.project.project_id,
      taskId: workspace.task.task_id,
      preset: "selected",
      moduleIds: [],
      acceptanceItemIds: workspace.scope,
    });
    await loadTask(workspace.task.task_id, generated.plan_id);
    workspace.stage = "plan";
    workspace.run = null;
    workspace.historical = false;
    persist();
    return true;
  } catch (error) {
    workspace.lastError = errorOf(error);
    return false;
  } finally {
    setLoading("planGenerate", false);
  }
}

export async function loadRun(runId: string): Promise<boolean> {
  if (!workspace.project) {
    return false;
  }
  try {
    const detail = await api.runStatus(workspace.project.project_id, runId);
    workspace.run = detail;
    workspace.historical = false;
    workspace.stage = detail.run.status === "running" ? "running" : "result";
    workspace.sheet = null;
    persist();
    return true;
  } catch (error) {
    workspace.lastError = errorOf(error);
    return false;
  }
}

export function stopPolling(): void {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

export function startPolling(): void {
  stopPolling();
  pollTimer = setInterval(() => {
    void refreshRun();
  }, 2000);
}

export async function refreshRun(): Promise<void> {
  if (!workspace.project || !workspace.run) {
    return;
  }
  const runId = workspace.run.run.run_id;
  try {
    const detail = await api.runStatus(workspace.project.project_id, runId);
    workspace.run = detail;
    if (detail.run.status !== "running") {
      workspace.stage = "result";
      stopPolling();
      await Promise.all([loadHistory(), loadIssues()]);
      if (detail.run.latest_report_id) {
        await loadReports(runId);
      }
    }
  } catch {
    // 轮询失败不覆盖最后一次成功状态；下一次继续。
  }
}

export async function startRun(): Promise<boolean> {
  if (!workspace.project || !workspace.plan) {
    return false;
  }
  setLoading("runStart", true);
  workspace.lastError = null;
  try {
    const started = await api.startRun(workspace.project.project_id, workspace.plan.plan_id);
    await loadRun(started.run_id);
    startPolling();
    return true;
  } catch (error) {
    workspace.lastError = errorOf(error);
    return false;
  } finally {
    setLoading("runStart", false);
  }
}

export async function cancelRun(): Promise<void> {
  if (!workspace.project || !workspace.run) {
    return;
  }
  try {
    await api.cancelRun(workspace.project.project_id, workspace.run.run.run_id);
    flash("已请求停止，将在当前步骤结束后生效");
  } catch (error) {
    workspace.lastError = errorOf(error);
  }
}

export async function rerunFailed(): Promise<boolean> {
  if (!workspace.project || !workspace.run?.plan) {
    return false;
  }
  const failedItems = new Set(
    (workspace.run.item_outcomes ?? [])
      .filter((item) => item.result !== "passed")
      .map((item) => item.item_id),
  );
  if (failedItems.size === 0) {
    return false;
  }
  setLoading("runStart", true);
  workspace.lastError = null;
  try {
    const started = await api.startRun(
      workspace.project.project_id,
      workspace.run.plan.plan_id,
      [...failedItems],
    );
    await loadRun(started.run_id);
    startPolling();
    return true;
  } catch (error) {
    workspace.lastError = errorOf(error);
    return false;
  } finally {
    setLoading("runStart", false);
  }
}

export async function openIssues(): Promise<void> {
  setView("issues");
  await loadIssues();
}

export async function openIssue(issueId: string): Promise<void> {
  if (!workspace.project) {
    return;
  }
  try {
    const data = await api.showIssue(workspace.project.project_id, issueId);
    workspace.issue = data.issue;
  } catch (error) {
    workspace.lastError = errorOf(error);
  }
}

export function closeIssueDetail(): void {
  workspace.issue = null;
}

export async function openHistory(): Promise<void> {
  setView("history");
  await loadHistory();
}

export async function openHistoryRun(runId: string): Promise<void> {
  workspace.historical = true;
  await loadRun(runId);
  workspace.historical = true;
  workspace.view = "test";
  persistState();
}

export async function backToCurrent(): Promise<void> {
  workspace.historical = false;
  if (workspace.plan) {
    await selectPlan(workspace.plan.plan_id);
    return;
  }
  workspace.run = null;
  workspace.stage = "plan";
  persist();
}

export async function loadReports(runId: string): Promise<void> {
  if (!workspace.project) {
    return;
  }
  try {
    const data = await api.listReports(workspace.project.project_id, runId);
    workspace.reports = data.reports;
    const latest =
      data.reports.find((item) => item.report_id === workspace.run?.run.latest_report_id) ??
      data.reports[0];
    if (latest) {
      await loadReport(latest.report_id);
    } else {
      workspace.report = null;
    }
  } catch (error) {
    workspace.lastError = errorOf(error);
  }
}

export async function loadReport(reportId: string): Promise<void> {
  if (!workspace.project) {
    return;
  }
  try {
    const data = await api.showReport(workspace.project.project_id, reportId);
    workspace.report = data.report;
    workspace.sheet = "report";
  } catch (error) {
    workspace.lastError = errorOf(error);
  }
}

export async function generateReport(): Promise<void> {
  if (!workspace.project || !workspace.run) {
    return;
  }
  setLoading("reportGenerate", true);
  try {
    const summary = await api.generateReport(
      workspace.project.project_id,
      workspace.run.run.run_id,
    );
    await loadReports(workspace.run.run.run_id);
    await loadReport(summary.report_id);
    flash("报告已重新生成；旧报告仍保留");
  } catch (error) {
    workspace.lastError = errorOf(error);
  } finally {
    setLoading("reportGenerate", false);
  }
}

export function openPlanSheet(): void {
  workspace.sheet = "plan";
}

export function closeSheet(): void {
  workspace.sheet = null;
}

export function openReportSheet(): void {
  if (!workspace.run) {
    return;
  }
  workspace.report = null;
  workspace.reports = [];
  workspace.sheet = "report";
  void loadReports(workspace.run.run.run_id);
}

export async function refreshDoctor(repair = false): Promise<void> {
  setLoading("doctor", true);
  workspace.lastError = null;
  try {
    const facts = await api.doctor(repair);
    workspace.doctor = facts;
    workspace.env = computeEnv(facts);
    flash(repair ? "已执行低风险修复并重新检查" : "环境已重新检查");
  } catch (error) {
    workspace.lastError = errorOf(error);
  } finally {
    setLoading("doctor", false);
  }
}

export async function bootstrap(): Promise<void> {  workspace.booting = true;
  workspace.bootError = null;
  const saved = readHostState<PersistedState>();
  try {
    const [facts] = await Promise.all([
      api.doctor().catch((error: unknown) => {
        workspace.bootError = errorOf(error);
        return null;
      }),
      loadProjects().catch((error: unknown) => {
        if (!workspace.bootError) {
          workspace.bootError = errorOf(error);
        }
      }),
    ]);
    workspace.doctor = facts;
    workspace.env = computeEnv(facts);

    const hostContext = await waitHostContext();
    let target: ProjectSummary | undefined =
      workspace.projects.find((item) => item.project_id === hostContext?.projectId) ??
      workspace.projects.find((item) => item.project_id === saved?.projectId) ??
      workspace.projects[0];

    const currentRoot = hostContext?.projectRoot || "";
    if (!target && workspace.projects.length === 0 && currentRoot) {
      try {
        const created = await api.addProject(currentRoot);
        await loadProjects();
        target = workspace.projects.find((item) => item.project_id === created.project_id);
        if (target) {
          flash("已绑定当前文件夹");
        }
      } catch {
        // 目录不可用或与已有项目重叠时保留选择页，由人明确选择。
      }
    }

    if (target) {
      await openProject(target.project_id, {
        taskId: saved?.taskId,
        planId: saved?.planId,
        runId: saved?.runId,
      });
      workspace.view = saved?.view ?? "test";
    } else {
      workspace.project = null;
      workspace.stage = "no-project";
    }
    persist();
  } finally {
    workspace.booting = false;
    workspace.ready = true;
  }
}

export function verdictOfRun(run: RunDetail | null): Verdict | null {
  return run?.outcome ?? null;
}
