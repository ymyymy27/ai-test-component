import type {
  Candidate,
  Confirmation,
  GithubSnapshot,
  QuickCheck,
} from "@/domain/types";

export interface AnalysisSidecar {
  status: "idle" | "analyzing" | "ready" | "failed";
  error: string;
  candidates: Candidate[];
}

export interface StageOverride {
  stage: "scope";
  previousStage: "planned" | "result";
}

export interface HostSidecarState {
  schemaVersion: number;
  lastProjectId: string;
  activeTask: Record<string, string>;
  scopes: Record<string, string[]>;
  recommended: Record<string, string[]>;
  stage: Record<string, StageOverride>;
  confirmations: Record<string, Confirmation>;
  analysis: Record<string, AnalysisSidecar>;
  ignored: Record<string, string[]>;
  github: Record<string, GithubSnapshot>;
  quickChecks: Record<string, QuickCheck[]>;
  projectTasks: Record<string, string[]>;
  projectPlans: Record<string, string[]>;
}

const KEY = "aitest.host.ui.v1";
const SCHEMA_VERSION = 2;

function emptyState(): HostSidecarState {
  return {
    schemaVersion: SCHEMA_VERSION,
    lastProjectId: "",
    activeTask: {},
    scopes: {},
    recommended: {},
    stage: {},
    confirmations: {},
    analysis: {},
    ignored: {},
    github: {},
    quickChecks: {},
    projectTasks: {},
    projectPlans: {},
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export class HostSidecar {
  private state: HostSidecarState;

  constructor() {
    this.state = this.load();
    // 两个面板（侧栏 webview 与编辑器面板）共享同一个 localStorage 键；
    // 其他文档写入后重新加载，避免各自长期持有过期内存副本。
    if (typeof window !== "undefined") {
      window.addEventListener("storage", (event) => {
        if (event.key === KEY) {
          this.state = this.load();
        }
      });
    }
  }

  private load(): HostSidecarState {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) {
        return emptyState();
      }
      const parsed = JSON.parse(raw) as unknown;
      if (!isRecord(parsed)) {
        return emptyState();
      }
      const version = typeof parsed.schemaVersion === "number" ? parsed.schemaVersion : 1;
      if (version > SCHEMA_VERSION) {
        // 更新版本的面板写过新结构；旧面板不猜字段，按空状态处理。
        return emptyState();
      }
      return {
        schemaVersion: SCHEMA_VERSION,
        lastProjectId: typeof parsed.lastProjectId === "string" ? parsed.lastProjectId : "",
        activeTask: isRecord(parsed.activeTask) ? (parsed.activeTask as Record<string, string>) : {},
        scopes: isRecord(parsed.scopes) ? (parsed.scopes as Record<string, string[]>) : {},
        recommended: isRecord(parsed.recommended)
          ? (parsed.recommended as Record<string, string[]>)
          : {},
        stage: isRecord(parsed.stage) ? (parsed.stage as Record<string, StageOverride>) : {},
        confirmations: isRecord(parsed.confirmations)
          ? (parsed.confirmations as Record<string, Confirmation>)
          : {},
        analysis: isRecord(parsed.analysis)
          ? (parsed.analysis as Record<string, AnalysisSidecar>)
          : {},
        ignored: isRecord(parsed.ignored) ? (parsed.ignored as Record<string, string[]>) : {},
        github: isRecord(parsed.github) ? (parsed.github as Record<string, GithubSnapshot>) : {},
        quickChecks: isRecord(parsed.quickChecks)
          ? (parsed.quickChecks as Record<string, QuickCheck[]>)
          : {},
        projectTasks: isRecord(parsed.projectTasks)
          ? (parsed.projectTasks as Record<string, string[]>)
          : {},
        projectPlans: isRecord(parsed.projectPlans)
          ? (parsed.projectPlans as Record<string, string[]>)
          : {},
      };
    } catch {
      return emptyState();
    }
  }

  private save(): void {
    try {
      localStorage.setItem(KEY, JSON.stringify(this.state));
    } catch {
      // UI 偏好写不进去不影响 CLI 业务事实。
    }
  }

  get activeTask(): Record<string, string> {
    return this.state.activeTask;
  }

  get scopes(): Record<string, string[]> {
    return this.state.scopes;
  }

  get recommended(): Record<string, string[]> {
    return this.state.recommended;
  }

  get stage(): Record<string, StageOverride> {
    return this.state.stage;
  }

  get confirmations(): Record<string, Confirmation> {
    return this.state.confirmations;
  }

  get analysis(): Record<string, AnalysisSidecar> {
    return this.state.analysis;
  }

  get ignored(): Record<string, string[]> {
    return this.state.ignored;
  }

  get github(): Record<string, GithubSnapshot> {
    return this.state.github;
  }

  get quickChecks(): Record<string, QuickCheck[]> {
    return this.state.quickChecks;
  }

  get lastProjectId(): string {
    return this.state.lastProjectId;
  }

  setLastProjectId(projectId: string): void {
    this.state.lastProjectId = projectId;
    this.save();
  }

  setActiveTask(projectId: string, taskId: string): void {
    this.state.activeTask[projectId] = taskId;
    this.save();
  }

  setScope(taskId: string, ids: string[]): void {
    this.state.scopes[taskId] = [...ids];
    this.save();
  }

  clearScope(taskId: string): void {
    if (taskId in this.state.scopes) {
      delete this.state.scopes[taskId];
      this.save();
    }
  }

  setRecommended(taskId: string, ids: string[]): void {
    this.state.recommended[taskId] = [...ids];
    this.save();
  }

  setStage(taskId: string, value: StageOverride | null): void {
    if (value) {
      this.state.stage[taskId] = value;
    } else {
      delete this.state.stage[taskId];
    }
    this.save();
  }

  setConfirmation(planId: string, value: Confirmation): void {
    this.state.confirmations[planId] = value;
    this.save();
  }

  setAnalysis(projectId: string, value: AnalysisSidecar): void {
    this.state.analysis[projectId] = value;
    this.save();
  }

  setIgnored(projectId: string, names: string[]): void {
    this.state.ignored[projectId] = [...names];
    this.save();
  }

  setGithub(projectId: string, value: GithubSnapshot | null): void {
    if (value) {
      this.state.github[projectId] = value;
    } else {
      delete this.state.github[projectId];
    }
    this.save();
  }

  addQuickCheck(projectId: string, value: QuickCheck): void {
    const list = this.state.quickChecks[projectId] ?? [];
    this.state.quickChecks[projectId] = [value, ...list].slice(0, 20);
    this.save();
  }

  /** 只清理明确曾归属当前项目的失效任务/计划，绝不误删其他项目状态。 */
  prune(projectId: string, taskIds: string[], planIds: string[]): void {
    const taskSet = new Set(taskIds);
    const planSet = new Set(planIds);
    const otherTasks = new Set(
      Object.entries(this.state.projectTasks)
        .filter(([pid]) => pid !== projectId)
        .flatMap(([, ids]) => ids),
    );
    const otherPlans = new Set(
      Object.entries(this.state.projectPlans)
        .filter(([pid]) => pid !== projectId)
        .flatMap(([, ids]) => ids),
    );
    const previousTasks = this.state.projectTasks[projectId] ?? [];
    for (const staleTaskId of previousTasks) {
      if (!taskSet.has(staleTaskId) && !otherTasks.has(staleTaskId)) {
        delete this.state.scopes[staleTaskId];
        delete this.state.recommended[staleTaskId];
        delete this.state.stage[staleTaskId];
      }
    }
    const previousPlans = this.state.projectPlans[projectId] ?? [];
    for (const stalePlanId of previousPlans) {
      if (!planSet.has(stalePlanId) && !otherPlans.has(stalePlanId)) {
        delete this.state.confirmations[stalePlanId];
      }
    }
    this.state.projectTasks[projectId] = [...taskIds];
    this.state.projectPlans[projectId] = [...planIds];
    if (this.state.activeTask[projectId] && !taskSet.has(this.state.activeTask[projectId])) {
      delete this.state.activeTask[projectId];
    }
    this.save();
  }

  pruneProjects(existingProjectIds: string[]): void {
    const known = new Set(existingProjectIds);
    const activeTasks = new Set(
      existingProjectIds.flatMap((pid) => this.state.projectTasks[pid] ?? []),
    );
    const activePlans = new Set(
      existingProjectIds.flatMap((pid) => this.state.projectPlans[pid] ?? []),
    );
    Object.keys(this.state.projectTasks).forEach((pid) => {
      if (known.has(pid)) {
        return;
      }
      for (const taskId of this.state.projectTasks[pid] ?? []) {
        if (!activeTasks.has(taskId)) {
          delete this.state.scopes[taskId];
          delete this.state.recommended[taskId];
          delete this.state.stage[taskId];
        }
      }
      delete this.state.projectTasks[pid];
    });
    Object.keys(this.state.projectPlans).forEach((pid) => {
      if (known.has(pid)) {
        return;
      }
      for (const planId of this.state.projectPlans[pid] ?? []) {
        if (!activePlans.has(planId)) {
          delete this.state.confirmations[planId];
        }
      }
      delete this.state.projectPlans[pid];
    });
    [this.state.analysis, this.state.ignored, this.state.github, this.state.quickChecks].forEach(
      (record) => {
        Object.keys(record).forEach((key) => {
          if (!known.has(key)) {
            delete record[key];
          }
        });
      },
    );
    Object.keys(this.state.activeTask).forEach((key) => {
      if (!known.has(key)) {
        delete this.state.activeTask[key];
      }
    });
    if (this.state.lastProjectId && !known.has(this.state.lastProjectId)) {
      this.state.lastProjectId = "";
    }
    this.save();
  }
}
