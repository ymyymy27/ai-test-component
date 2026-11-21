import type {
  Candidate,
  Confirmation,
  GithubSnapshot,
  QuickCheck,
  TemplateResult,
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
  lastProjectId: string;
  activeTask: Record<string, string>;
  scopes: Record<string, string[]>;
  recommended: Record<string, string[]>;
  stage: Record<string, StageOverride>;
  confirmations: Record<string, Confirmation>;
  analysis: Record<string, AnalysisSidecar>;
  ignored: Record<string, string[]>;
  github: Record<string, GithubSnapshot>;
  templates: Record<string, TemplateResult[]>;
  quickChecks: Record<string, QuickCheck[]>;
}

const KEY = "aitest.host.ui.v1";

function emptyState(): HostSidecarState {
  return {
    lastProjectId: "",
    activeTask: {},
    scopes: {},
    recommended: {},
    stage: {},
    confirmations: {},
    analysis: {},
    ignored: {},
    github: {},
    templates: {},
    quickChecks: {},
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
      return {
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
        templates: isRecord(parsed.templates)
          ? (parsed.templates as Record<string, TemplateResult[]>)
          : {},
        quickChecks: isRecord(parsed.quickChecks)
          ? (parsed.quickChecks as Record<string, QuickCheck[]>)
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

  get templates(): Record<string, TemplateResult[]> {
    return this.state.templates;
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

  setTemplates(taskId: string, value: TemplateResult[]): void {
    this.state.templates[taskId] = [...value];
    this.save();
  }

  addQuickCheck(projectId: string, value: QuickCheck): void {
    const list = this.state.quickChecks[projectId] ?? [];
    this.state.quickChecks[projectId] = [value, ...list].slice(0, 20);
    this.save();
  }
}
