/** 面板会话状态：一张测试单从选项目到结果的相位机。
 *
 * 只有一个主视图，相位在同一页内切换；历史、项目详情、调整与证据是抽屉。
 * 结论、覆盖、用例结果都直接取自 CLI 返回字段；写入动作完成后重新读取，
 * 不在本地推导“已通过”。
 */
import { reactive } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import { readHostContext, readHostState, writeHostState, type HostContext } from "@/api/host";
import { sourceLabel } from "@/api/labels";
import type {
  DoctorFacts,
  IssueSummary,
  Plan,
  Project,
  ProjectSummary,
  RunDetail,
  RunSummary,
  RunStatus,
  SuggestResult,
  TargetCandidate,
  Task,
  Verdict,
} from "@/api/types";

export type Phase = "no-project" | "proposal" | "running" | "result";
export type Screen = "session" | "settings";

const DEFAULT_TARGET = "验证项目能否启动并跑通主流程";

export interface DraftItem {
  id: string;
  text: string;
  savedText: string;
  observable: boolean;
  enabled: boolean;
  source: string;
  status: "active" | "pending";
}

export interface ProposalState {
  candidates: TargetCandidate[];
  index: number;
  fallback: string;
  goal: string;
  basis: string;
  taskId: string;
  taskRevision: number;
  taskGoal: string;
  items: DraftItem[];
  itemsLoading: boolean;
  itemsError: FriendlyError | null;
  itemsDirty: boolean;
  preset: "all_applicable" | "selected";
  selected: string[];
  moduleIds: string[];
  suggestLoading: boolean;
  suggestError: FriendlyError | null;
  previewPlan: Plan | null;
  previewSignature: string;
  starting: boolean;
  startError: FriendlyError | null;
}

function createProposal(): ProposalState {
  return {
    candidates: [],
    index: -1,
    fallback: "",
    goal: "",
    basis: "",
    taskId: "",
    taskRevision: 0,
    taskGoal: "",
    items: [],
    itemsLoading: false,
    itemsError: null,
    itemsDirty: false,
    preset: "all_applicable",
    selected: [],
    moduleIds: [],
    suggestLoading: false,
    suggestError: null,
    previewPlan: null,
    previewSignature: "",
    starting: false,
    startError: null,
  };
}

export interface LastRun {
  status: RunStatus;
  outcome: Verdict | null;
  startedAt: string;
  selected: number;
  applicable: number;
}

interface PersistedState {
  screen?: Screen;
  projectId?: string;
  runId?: string;
  planId?: string;
}

interface SessionState {
  ready: boolean;
  booting: boolean;
  bootError: FriendlyError | null;
  screen: Screen;
  phase: Phase;
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
  proposal: ProposalState;
  loading: Record<string, boolean>;
  notice: string;
  hostContext: HostContext | null;
  prefillRoot: string;
  historyOpen: boolean;
  adjustOpen: boolean;
  projectOpen: boolean;
  evidenceOpen: boolean;
}

export const session = reactive<SessionState>({
  ready: false,
  booting: false,
  bootError: null,
  screen: "session",
  phase: "no-project",
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
  proposal: createProposal(),
  loading: {},
  notice: "",
  hostContext: null,
  prefillRoot: "",
  historyOpen: false,
  adjustOpen: false,
  projectOpen: false,
  evidenceOpen: false,
});

let contextWaiters: ((context: HostContext | null) => void)[] = [];
let localSeq = 0;

/** 编辑器在面板打开后推送上下文；这里只缓存，不推导项目结论。 */
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
  session.hostContext = context;
  session.prefillRoot = context.projectId ? "" : (context.projectRoot ?? "");
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
  if (session.hostContext) {
    return session.hostContext;
  }
  const cached = readHostContext();
  if (cached) {
    applyHostContext(cached);
    return session.hostContext;
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
    screen: session.screen,
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

export function dismissBootError(): void {
  session.bootError = null;
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

/** 读取项目与最近运行，不决定相位；切项目与启动都走这里。 */
export async function openProject(projectId: string): Promise<void> {
  setLoading("project", true);
  try {
    const data = await api.showProject(projectId);
    session.project = data.project;
    session.bootError = null;
    session.result = null;
    session.plan = null;
    session.task = null;
    session.activeRunId = "";
    session.activePlanId = "";
    session.activeTaskId = "";
    await refreshHistory();
    await loadLastRun(session.history[0]);
  } catch (error) {
    session.bootError = describeError(error);
  } finally {
    setLoading("project", false);
  }
}

/** 相位判断：最近一次运行直接回放，否则准备建议测试单。 */
async function routeAfterProject(): Promise<void> {
  session.screen = "session";
  session.historyOpen = false;
  session.adjustOpen = false;
  session.projectOpen = false;
  session.evidenceOpen = false;
  if (!session.project) {
    session.phase = "no-project";
    return;
  }
  const latest = session.history[0];
  if (latest) {
    const opened = await openResult(latest.run_id);
    if (opened) {
      return;
    }
  }
  await loadProposal();
}

export async function switchProject(projectId: string): Promise<void> {
  await openProject(projectId);
  await routeAfterProject();
  persist();
}

export async function addProjectByPath(root: string, name?: string): Promise<boolean> {
  setLoading("projectAdd", true);
  try {
    const created = await api.addProject(root.trim(), name);
    await loadProjects();
    await openProject(created.project_id);
    await routeAfterProject();
    flash(created.existing ? "这个目录已经登记过，已切换到现有项目" : "项目已登记");
    persist();
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

    const hostContext = await waitHostContext();
    const target =
      session.projects.find((item) => item.project_id === hostContext?.projectId) ??
      session.projects.find((item) => item.project_id === saved?.projectId) ??
      session.projects[0];
    if (target) {
      await openProject(target.project_id);
      await routeAfterProject();
      if (saved?.screen === "settings") {
        session.screen = "settings";
      }
    } else {
      session.project = null;
      session.screen = "session";
      session.phase = "no-project";
    }
    persist();
  } finally {
    session.booting = false;
    session.ready = true;
  }
}

// -- 测试单：建议 -----------------------------------------------------------

export function applicableItems(): DraftItem[] {
  return session.proposal.items.filter(
    (item) => item.observable && item.status === "active",
  );
}

export function selectedItems(): DraftItem[] {
  const ids = new Set(session.proposal.selected);
  return applicableItems().filter((item) => ids.has(item.id));
}

export function selectedCount(): number {
  return selectedItems().length;
}

export function applicableCount(): number {
  return applicableItems().length;
}

function syncSelection(): void {
  const proposal = session.proposal;
  const applicable = applicableItems();
  const enabled = applicable.filter((item) => item.enabled);
  proposal.selected = enabled.map((item) => item.id);
  proposal.preset =
    applicable.length > 0 && enabled.length === applicable.length
      ? "all_applicable"
      : "selected";
}

function invalidatePreview(): void {
  session.proposal.previewPlan = null;
  session.proposal.previewSignature = "";
}

function scopeSignature(): string {
  const proposal = session.proposal;
  return [
    proposal.preset,
    proposal.moduleIds.slice().sort().join(","),
    proposal.preset === "selected" ? proposal.selected.slice().sort().join(",") : "all",
  ].join("|");
}

function fillTask(task: Task): void {
  const proposal = session.proposal;
  proposal.taskId = task.task_id;
  proposal.taskRevision = task.revision;
  proposal.taskGoal = task.title;
  proposal.items = task.acceptance_items.map((item) => ({
    id: item.item_id,
    text: item.text,
    savedText: item.text,
    observable: item.observable,
    enabled: item.observable && item.status === "active",
    source: item.source ?? "",
    status: item.status,
  }));
  proposal.itemsDirty = false;
  session.task = task;
  session.activeTaskId = task.task_id;
  syncSelection();
}

async function selectTarget(
  target: { title: string; reason?: string; source?: string },
  index: number,
): Promise<void> {
  if (!session.project || !target.title.trim()) {
    return;
  }
  const proposal = session.proposal;
  proposal.index = index;
  proposal.goal = target.title.trim();
  proposal.basis = [target.reason?.trim(), sourceLabel(target.source)]
    .filter(Boolean)
    .join(" · ");
  proposal.itemsLoading = true;
  proposal.itemsError = null;
  proposal.startError = null;
  try {
    const created = await api.addTask({
      projectId: session.project.project_id,
      title: proposal.goal,
      goal: proposal.goal,
      requirement: proposal.goal,
    });
    const data = await api.showTask(session.project.project_id, created.task_id);
    fillTask(data.task);
    invalidatePreview();
  } catch (error) {
    proposal.itemsError = describeError(error);
  } finally {
    proposal.itemsLoading = false;
  }
}

/** 目标候选 → 任务；只提取验收标准，不启动运行。 */
export async function loadProposal(): Promise<void> {
  if (!session.project) {
    session.phase = "no-project";
    return;
  }
  session.screen = "session";
  session.phase = "proposal";
  session.proposal = createProposal();
  session.bootError = null;
  const proposal = session.proposal;
  proposal.suggestLoading = true;
  try {
    const result: SuggestResult = await api.suggestTargets(session.project.project_id);
    proposal.candidates = result.candidates ?? [];
    proposal.fallback = result.fallback ?? "";
  } catch (error) {
    proposal.suggestError = describeError(error);
  } finally {
    proposal.suggestLoading = false;
  }
  const first = proposal.candidates[0];
  const target = first
    ? { title: first.title, reason: first.reason, source: first.source }
    : { title: proposal.fallback || DEFAULT_TARGET, reason: "", source: "" };
  await selectTarget(target, first ? 0 : -2);
}

/** 换一个目标：重新提取验收标准，不启动运行。 */
export async function chooseTarget(index: number): Promise<void> {
  const proposal = session.proposal;
  if (index === -2) {
    await selectTarget(
      { title: proposal.fallback || DEFAULT_TARGET, reason: "", source: "" },
      -2,
    );
    return;
  }
  const candidate = proposal.candidates[index];
  if (!candidate) {
    return;
  }
  await selectTarget(candidate, index);
}

/** 自己写一句：同样只提取验收标准，不启动运行。 */
export async function chooseCustomTarget(title: string): Promise<void> {
  await selectTarget({ title, reason: "手动填写", source: "" }, -1);
}

export async function newTarget(): Promise<void> {
  await loadProposal();
}

export function toggleProposalItem(item: DraftItem): void {
  if (!item.observable || item.status !== "active") {
    return;
  }
  item.enabled = !item.enabled;
  syncSelection();
  invalidatePreview();
}

export function setProposalPreset(preset: "all_applicable" | "selected"): void {
  const proposal = session.proposal;
  proposal.preset = preset;
  if (preset === "all_applicable") {
    for (const item of proposal.items) {
      item.enabled = item.observable && item.status === "active";
    }
    syncSelection();
  }
  invalidatePreview();
}

export function toggleProposalModule(moduleId: string): void {
  const proposal = session.proposal;
  proposal.moduleIds = proposal.moduleIds.includes(moduleId)
    ? proposal.moduleIds.filter((id) => id !== moduleId)
    : [...proposal.moduleIds, moduleId];
  invalidatePreview();
}

export function addProposalItem(text: string): void {
  const value = text.trim();
  if (!value) {
    return;
  }
  localSeq += 1;
  const proposal = session.proposal;
  proposal.items.push({
    id: `local-${localSeq}`,
    text: value,
    savedText: "",
    observable: true,
    enabled: true,
    source: "手动补充",
    status: "active",
  });
  proposal.itemsDirty = true;
  syncSelection();
  invalidatePreview();
}

export function updateProposalItem(
  id: string,
  patch: { text?: string; observable?: boolean },
): void {
  const proposal = session.proposal;
  const item = proposal.items.find((row) => row.id === id);
  if (!item) {
    return;
  }
  if (typeof patch.text === "string" && patch.text.trim()) {
    item.text = patch.text.trim();
  }
  if (typeof patch.observable === "boolean" && patch.observable !== item.observable) {
    item.observable = patch.observable;
    item.status = patch.observable ? "active" : "pending";
    item.enabled = patch.observable;
  }
  proposal.itemsDirty = true;
  syncSelection();
  invalidatePreview();
}

export function removeProposalItem(id: string): void {
  const proposal = session.proposal;
  proposal.items = proposal.items.filter((item) => item.id !== id);
  proposal.itemsDirty = true;
  syncSelection();
  invalidatePreview();
}

/** 把验收项改动写回任务；没有任务时创建一条。 */
async function saveItems(): Promise<boolean> {
  const proposal = session.proposal;
  if (!session.project) {
    return false;
  }
  if (!proposal.itemsDirty && proposal.taskId) {
    return true;
  }
  const filled = proposal.items.filter((item) => item.text.trim().length > 0);
  if (filled.length === 0) {
    proposal.itemsError = {
      title: "至少保留一条验收标准",
      detail: "",
      next: "补充一条可观察的标准，或换一个测试目标",
    };
    return false;
  }
  proposal.itemsLoading = true;
  proposal.itemsError = null;
  try {
    if (proposal.taskId) {
      await api.updateTask({
        projectId: session.project.project_id,
        taskId: proposal.taskId,
        revision: proposal.taskRevision,
        items: filled.map((item) => ({
          ...(item.id.startsWith("local-") ? {} : { item_id: item.id }),
          text: item.text.trim(),
          observable: item.observable,
          status: item.status,
          source: item.source || "手动补充",
        })),
      });
    } else {
      const created = await api.addTask({
        projectId: session.project.project_id,
        title: proposal.goal || DEFAULT_TARGET,
        goal: proposal.goal || DEFAULT_TARGET,
        acceptanceItems: filled.map((item) => ({
          text: item.text.trim(),
          observable: item.observable,
          source: item.source || "手动补充",
        })),
      });
      proposal.taskId = created.task_id;
    }
    const data = await api.showTask(session.project.project_id, proposal.taskId);
    const flags = filled.map((item) => item.enabled);
    fillTask(data.task);
    proposal.items.forEach((item, index) => {
      item.enabled = flags[index] ?? item.enabled;
    });
    syncSelection();
    proposal.itemsDirty = false;
    invalidatePreview();
    return true;
  } catch (error) {
    proposal.itemsError = describeError(error);
    return false;
  } finally {
    proposal.itemsLoading = false;
  }
}

export async function saveAdjustment(): Promise<boolean> {
  const saved = await saveItems();
  if (saved) {
    flash("验收标准与范围已保存");
  }
  return saved;
}

async function generatePlan(): Promise<string> {
  const proposal = session.proposal;
  if (!session.project || !proposal.taskId) {
    throw new Error("缺少测试任务");
  }
  const payload = {
    projectId: session.project.project_id,
    taskId: proposal.taskId,
    preset: proposal.preset,
    moduleIds: proposal.moduleIds,
    acceptanceItemIds:
      proposal.preset === "selected"
        ? proposal.selected
        : applicableItems().map((item) => item.id),
  };
  let generated;
  try {
    generated = await api.generatePlan(payload);
  } catch (error) {
    const code = (error as { code?: string } | null)?.code;
    if (code !== "HOST_CALL_FAILED" && code !== "PANEL_TIMEOUT") {
      throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 800));
    generated = await api.generatePlan(payload);
  }
  session.activePlanId = generated.plan_id;
  return generated.plan_id;
}

/** 调整抽屉里的模板处置：需要先有一份与当前范围一致的预览计划。 */
export async function applyProposalTemplate(
  templateName: string,
): Promise<FriendlyError | null> {
  const proposal = session.proposal;
  if (!session.project || !proposal.taskId || !templateName) {
    return { title: "还不能应用模板", detail: "", next: "先生成验收标准" };
  }
  try {
    if (proposal.previewPlan && proposal.previewSignature === scopeSignature()) {
      await api.applyTemplate({
        projectId: session.project.project_id,
        taskId: proposal.taskId,
        templateName,
        planId: proposal.previewPlan.plan_id,
      });
    } else {
      if (!(await saveItems())) {
        return proposal.itemsError;
      }
      const planId = await generatePlan();
      const generated = await api.showPlan(session.project.project_id, planId);
      proposal.previewPlan = generated.plan;
      proposal.previewSignature = scopeSignature();
      await api.applyTemplate({
        projectId: session.project.project_id,
        taskId: proposal.taskId,
        templateName,
        planId,
      });
    }
    const data = await api.showPlan(
      session.project.project_id,
      proposal.previewPlan?.plan_id ?? session.activePlanId,
    );
    proposal.previewPlan = data.plan;
    return null;
  } catch (error) {
    return describeError(error);
  }
}

async function beginRun(planId: string): Promise<void> {
  if (!session.project) {
    throw new Error("缺少项目");
  }
  const started = await api.startRun(session.project.project_id, planId);
  session.activeRunId = started.run_id;
  session.activePlanId = planId;
  session.activeTaskId = session.proposal.taskId || session.activeTaskId;
  session.result = null;
  session.phase = "running";
  session.screen = "session";
  persist();
  await refreshRun();
}

/** 开始测试：一次点击完成保存、生成计划与确认执行。 */
export async function startTest(): Promise<void> {
  const proposal = session.proposal;
  if (!session.project || proposal.starting || applicableItems().length === 0) {
    return;
  }
  proposal.starting = true;
  proposal.startError = null;
  try {
    if (!(await saveItems())) {
      return;
    }
    let planId = "";
    if (proposal.previewPlan && proposal.previewSignature === scopeSignature()) {
      planId = proposal.previewPlan.plan_id;
      session.plan = proposal.previewPlan;
    } else {
      planId = await generatePlan();
      try {
        const detail = await api.showPlan(session.project.project_id, planId);
        session.plan = detail.plan;
      } catch {
        // 计划读取失败不阻塞执行；运行结果会再按计划号读取一次。
        session.plan = null;
      }
    }
    await beginRun(planId);
  } catch (error) {
    proposal.startError = describeError(error);
  } finally {
    proposal.starting = false;
  }
}

// -- 运行与结果 -------------------------------------------------------------

export async function refreshRun(): Promise<void> {
  if (!session.project || !session.activeRunId) {
    return;
  }
  try {
    const detail = await api.runStatus(session.project.project_id, session.activeRunId);
    session.result = detail;
    session.activePlanId = detail.run.plan_id;
    if (detail.run.status !== "running") {
      await hydratePlanAndTask(detail.run.plan_id);
      await loadIssues();
      if (session.phase === "running") {
        session.phase = "result";
        await refreshHistory();
        await loadLastRun(session.history[0]);
        persist();
      }
    }
  } catch {
    // 轮询失败不覆盖已有画面；下一次轮询继续。
  }
}

async function hydratePlanAndTask(planId: string): Promise<void> {
  if (!session.project) {
    return;
  }
  try {
    const plan = await api.showPlan(session.project.project_id, planId);
    session.plan = plan.plan;
    session.activePlanId = plan.plan.plan_id;
    const taskId = plan.plan.task_id;
    if (taskId) {
      session.activeTaskId = taskId;
      const task = await api.showTask(session.project.project_id, taskId);
      session.task = task.task;
    }
  } catch {
    session.plan = null;
  }
}

async function loadIssues(): Promise<void> {
  if (!session.project) {
    session.issues = [];
    return;
  }
  try {
    const data = await api.listIssues(session.project.project_id);
    session.issues = data.issues ?? [];
  } catch {
    session.issues = [];
  }
}

/** 打开一次运行：运行中进入运行相位，结束的运行直接显示结果。 */
export async function openResult(runId: string): Promise<boolean> {
  if (!session.project) {
    return false;
  }
  setLoading("result", true);
  session.activeRunId = runId;
  try {
    const detail = await api.runStatus(session.project.project_id, runId);
    session.result = detail;
    session.activePlanId = detail.run.plan_id;
    await hydratePlanAndTask(detail.run.plan_id);
    await loadIssues();
    session.bootError = null;
    session.phase = detail.run.status === "running" ? "running" : "result";
    session.screen = "session";
    session.historyOpen = false;
    session.evidenceOpen = false;
    persist();
    return true;
  } catch (error) {
    session.bootError = describeError(error);
    return false;
  } finally {
    setLoading("result", false);
  }
}

/** 再测一次：优先复用最近计划，计划不在了才重新生成。 */
export async function rerun(): Promise<FriendlyError | null> {
  if (!session.project) {
    return { title: "还没有选择项目", detail: "", next: "先选择一个项目文件夹" };
  }
  setLoading("rerun", true);
  try {
    let planId = session.activePlanId || session.plan?.plan_id || "";
    let taskId = session.plan?.task_id || session.task?.task_id || session.activeTaskId;
    const scope = session.plan?.scope;
    if (planId) {
      try {
        const data = await api.showPlan(session.project.project_id, planId);
        session.plan = data.plan;
        taskId = data.plan.task_id;
      } catch {
        planId = "";
      }
    }
    if (!planId) {
      if (!taskId) {
        return {
          title: "找不到上次的计划与任务",
          detail: "",
          next: "换一个新目标重新开始",
        };
      }
      planId = await api
        .generatePlan({
          projectId: session.project.project_id,
          taskId,
          preset: scope?.preset ?? "all_applicable",
          moduleIds: scope?.module_ids ?? [],
          acceptanceItemIds: scope?.acceptance_item_ids ?? [],
        })
        .then((generated) => generated.plan_id);
    }
    await beginRun(planId);
    return null;
  } catch (error) {
    return describeError(error);
  } finally {
    setLoading("rerun", false);
  }
}

export async function rerunItem(acceptanceItemId: string): Promise<FriendlyError | null> {
  if (!session.project || !session.activePlanId) {
    return { title: "找不到上次的计划", detail: "", next: "换一个新目标重新开始" };
  }
  setLoading("rerun", true);
  try {
    const started = await api.startRun(
      session.project.project_id,
      session.activePlanId,
      [acceptanceItemId],
    );
    session.activeRunId = started.run_id;
    session.result = null;
    session.phase = "running";
    persist();
    await refreshRun();
    return null;
  } catch (error) {
    return describeError(error);
  } finally {
    setLoading("rerun", false);
  }
}

export async function generateAnalysis(): Promise<void> {
  if (!session.project || !session.activeRunId) {
    return;
  }
  setLoading("analysis", true);
  try {
    await api.runAnalysis(session.project.project_id, session.activeRunId);
    await refreshRun();
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
    await refreshRun();
  } catch (error) {
    const friendly = describeError(error);
    flash(`${friendly.title}。${friendly.next}`);
  } finally {
    setLoading("cancel", false);
  }
}

// -- 抽屉与屏幕 -------------------------------------------------------------

export function openHistoryDrawer(): void {
  session.historyOpen = true;
  session.adjustOpen = false;
  session.projectOpen = false;
  session.evidenceOpen = false;
  void refreshHistory();
}

export function openAdjustDrawer(): void {
  session.adjustOpen = true;
  session.historyOpen = false;
  session.projectOpen = false;
  session.evidenceOpen = false;
}

export function openProjectDrawer(): void {
  session.projectOpen = true;
  session.historyOpen = false;
  session.adjustOpen = false;
  session.evidenceOpen = false;
}

export function openEvidenceDrawer(): void {
  session.evidenceOpen = true;
  session.historyOpen = false;
  session.adjustOpen = false;
  session.projectOpen = false;
}

export function closeDrawers(): void {
  session.historyOpen = false;
  session.adjustOpen = false;
  session.projectOpen = false;
  session.evidenceOpen = false;
}

export function openSettings(): void {
  closeDrawers();
  session.screen = "settings";
  persist();
}

export function backToSession(): void {
  session.screen = "session";
  persist();
}
