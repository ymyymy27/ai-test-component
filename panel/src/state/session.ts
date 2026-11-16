/** 面板会话状态：只缓存界面需要的读取结果，不计算业务结论。
 *
 * 结论、覆盖分母、用例结果都直接取自 CLI 返回字段；
 * 写入动作完成后重新读取，不在本地推导“已通过”。
 */
import { reactive } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import { readHostState, writeHostState } from "@/api/host";
import type {
  DoctorFacts,
  IssueSummary,
  Plan,
  Project,
  ProjectSummary,
  RunDetail,
  RunSummary,
  RunStatus,
  Task,
  Verdict,
} from "@/api/types";

export type View = "project" | "wizard" | "run" | "result" | "history" | "settings";

export interface LastRun {
  status: RunStatus;
  outcome: Verdict | null;
  startedAt: string;
  selected: number;
  applicable: number;
}

interface PersistedState {
  view?: View;
  projectId?: string;
  runId?: string;
  planId?: string;
}

interface SessionState {
  ready: boolean;
  booting: boolean;
  bootError: FriendlyError | null;
  view: View;
  projects: ProjectSummary[];
  project: Project | null;
  doctor: DoctorFacts | null;
  doctorLoading: boolean;
  env: "ok" | "warn" | "error" | "unknown";
  history: RunSummary[];
  lastRun: LastRun | null;
  activeRunId: string;
  activePlanId: string;
  activeTaskId: string;
  result: RunDetail | null;
  plan: Plan | null;
  task: Task | null;
  issues: IssueSummary[];
  loading: Record<string, boolean>;
  notice: string;
}

export const session = reactive<SessionState>({
  ready: false,
  booting: false,
  bootError: null,
  view: "project",
  projects: [],
  project: null,
  doctor: null,
  doctorLoading: false,
  env: "unknown",
  history: [],
  lastRun: null,
  activeRunId: "",
  activePlanId: "",
  activeTaskId: "",
  result: null,
  plan: null,
  task: null,
  issues: [],
  loading: {},
  notice: "",
});

function persist(): void {
  const state: PersistedState = {
    view: session.view,
    projectId: session.project?.project_id,
    runId: session.activeRunId,
    planId: session.activePlanId,
  };
  writeHostState(state);
}

function setLoading(key: string, value: boolean): void {
  session.loading[key] = value;
}

export function isBusy(key: string): boolean {
  return Boolean(session.loading[key]);
}

export function flash(message: string): void {
  session.notice = message;
}

export function clearNotice(): void {
  session.notice = "";
}

export function computeEnv(facts: DoctorFacts | null): "ok" | "warn" | "error" | "unknown" {
  if (!facts) {
    return "unknown";
  }
  const required = [facts.workspace.ok, facts.python.ok, facts.package.ok];
  if (required.some((ok) => !ok)) {
    return "error";
  }
  if (!facts.model.ok || !facts.github.ok) {
    return "warn";
  }
  return "ok";
}

export async function refreshDoctor(): Promise<void> {
  session.doctorLoading = true;
  try {
    const facts = await api.doctor();
    session.doctor = facts;
    session.env = computeEnv(facts);
  } catch {
    session.env = "error";
  } finally {
    session.doctorLoading = false;
  }
}

export async function repairEnvironment(): Promise<void> {
  session.doctorLoading = true;
  try {
    const facts = await api.doctor(true);
    session.doctor = facts;
    session.env = computeEnv(facts);
    const done = facts.repairs_done?.length ? facts.repairs_done.length : 0;
    flash(done > 0 ? `已处理 ${done} 项可自动修复的环境问题` : "没有可以自动处理的环境问题");
  } catch (error) {
    const friendly = describeError(error);
    flash(`${friendly.title}。${friendly.next}`);
  } finally {
    session.doctorLoading = false;
  }
}

export async function loadProjects(): Promise<void> {
  const data = await api.listProjects();
  session.projects = data.projects ?? [];
}

export async function refreshHistory(): Promise<void> {
  if (!session.project) {
    session.history = [];
    return;
  }
  setLoading("history", true);
  try {
    const data = await api.listRuns(session.project.project_id, 30);
    session.history = data.runs ?? [];
  } catch (error) {
    session.history = [];
    session.bootError = describeError(error);
  } finally {
    setLoading("history", false);
  }
}

async function loadLastRun(summary: RunSummary | undefined): Promise<void> {
  if (!summary || !session.project) {
    session.lastRun = null;
    return;
  }
  try {
    const detail = await api.runStatus(session.project.project_id, summary.run_id);
    let applicable = 0;
    try {
      const plan = await api.showPlan(session.project.project_id, detail.run.plan_id);
      applicable = plan.plan.applicable_count;
    } catch {
      applicable = 0;
    }
    session.lastRun = {
      status: detail.run.status,
      outcome: detail.outcome,
      startedAt: detail.run.started_at,
      selected: detail.run.scope.acceptance_item_ids.length,
      applicable,
    };
  } catch {
    session.lastRun = null;
  }
}

export async function openProject(projectId: string, options: { keepView?: boolean } = {}): Promise<void> {
  setLoading("project", true);
  try {
    const data = await api.showProject(projectId);
    session.project = data.project;
    if (!options.keepView) {
      session.view = "project";
    }
    await refreshHistory();
    await loadLastRun(session.history[0]);
    persist();
  } catch (error) {
    session.bootError = describeError(error);
  } finally {
    setLoading("project", false);
  }
}

export async function addProjectByPath(root: string, name?: string): Promise<boolean> {
  setLoading("projectAdd", true);
  try {
    const created = await api.addProject(root.trim(), name);
    await loadProjects();
    await openProject(created.project_id);
    flash(created.existing ? "这个目录已经登记过，已切换到现有项目" : "项目已登记，已扫描模块与最近运行");
    return true;
  } catch (error) {
    session.bootError = describeError(error);
    return false;
  } finally {
    setLoading("projectAdd", false);
  }
}

export async function bootstrap(): Promise<void> {
  session.booting = true;
  session.bootError = null;
  const saved = readHostState<PersistedState>();
  try {
    const [facts] = await Promise.all([
      api.doctor().catch((error: unknown) => {
        session.bootError = describeError(error);
        return null;
      }),
      loadProjects().catch((error: unknown) => {
        if (!session.bootError) {
          session.bootError = describeError(error);
        }
      }),
    ]);
    session.doctor = facts;
    session.env = computeEnv(facts);

    const target =
      session.projects.find((item) => item.project_id === saved?.projectId) ??
      session.projects[0];
    if (target) {
      await openProject(target.project_id, { keepView: true });
      if (saved?.view === "result" && saved.runId) {
        await openResult(saved.runId, { keepView: true });
        session.view = "result";
      } else if (saved?.view === "run" && saved.runId) {
        session.activeRunId = saved.runId;
        session.view = "run";
      } else if (saved?.view === "history") {
        session.view = "history";
      } else if (saved?.view === "settings") {
        session.view = "settings";
      } else {
        session.view = "project";
      }
    } else {
      session.view = "project";
    }
    persist();
  } finally {
    session.booting = false;
    session.ready = true;
  }
}

export function go(view: View): void {
  session.view = view;
  session.bootError = null;
  persist();
}

export function startWizard(): void {
  session.bootError = null;
  session.notice = "";
  session.view = "wizard";
  persist();
}

export async function openHistory(): Promise<void> {
  session.view = "history";
  await refreshHistory();
  persist();
}

export async function openRun(runId: string): Promise<void> {
  session.activeRunId = runId;
  session.view = "run";
  persist();
}

export async function openResult(
  runId: string,
  options: { keepView?: boolean } = {},
): Promise<void> {
  if (!session.project) {
    return;
  }
  setLoading("result", true);
  session.activeRunId = runId;
  try {
    const detail = await api.runStatus(session.project.project_id, runId);
    session.result = detail;
    session.activePlanId = detail.run.plan_id;
    let taskId = "";
    try {
      const plan = await api.showPlan(session.project.project_id, detail.run.plan_id);
      session.plan = plan.plan;
      taskId = plan.plan.task_id;
      session.activeTaskId = taskId;
      if (taskId) {
        const task = await api.showTask(session.project.project_id, taskId);
        session.task = task.task;
      }
    } catch {
      session.plan = null;
      session.task = null;
    }
    try {
      const issues = await api.listIssues(session.project.project_id);
      session.issues = issues.issues ?? [];
    } catch {
      session.issues = [];
    }
    if (!options.keepView) {
      session.view = "result";
    }
    persist();
  } catch (error) {
    session.bootError = describeError(error);
  } finally {
    setLoading("result", false);
  }
}

export async function reloadActiveRun(): Promise<void> {
  if (!session.project || !session.activeRunId) {
    return;
  }
  try {
    const detail = await api.runStatus(session.project.project_id, session.activeRunId);
    session.result = detail;
  } catch {
    // 轮询失败不覆盖已有画面；下一次轮询继续。
  }
}

export async function generateAnalysis(): Promise<void> {
  if (!session.project || !session.activeRunId) {
    return;
  }
  setLoading("analysis", true);
  try {
    await api.runAnalysis(session.project.project_id, session.activeRunId);
    await reloadActiveRun();
    flash("失败分析已生成");
  } catch (error) {
    const friendly = describeError(error);
    flash(`${friendly.title}。${friendly.next}`);
  } finally {
    setLoading("analysis", false);
  }
}

export async function cancelActiveRun(): Promise<void> {
  if (!session.project || !session.activeRunId) {
    return;
  }
  setLoading("cancel", true);
  try {
    await api.cancelRun(session.project.project_id, session.activeRunId);
    flash("已请求取消，当前用例结束后停止");
    await reloadActiveRun();
  } catch (error) {
    const friendly = describeError(error);
    flash(`${friendly.title}。${friendly.next}`);
  } finally {
    setLoading("cancel", false);
  }
}

