/** 唯一的宿主依赖：window.aitestHost。
 *
 * Trae 扩展先注入 bridge，再加载本面板；其他编辑器只需实现同一 bridge。
 * 这里不读宿主文件、不猜测命令语义，只做调用、超时与结构化错误。
 */

export interface HostErrorPayload {
  code: string;
  message: string;
  next_step?: string;
}

export type HostCallResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: HostErrorPayload };

export interface HostProgressEvent {
  type: "progress";
  id?: string;
  phase?: string;
  stage?: string;
  ok?: boolean;
  message?: string;
  done?: number;
  total?: number;
}

export interface HostCallOptions {
  confirm?: boolean;
  onProgress?: (event: HostProgressEvent) => void;
}

export type HostCapabilityName =
  | "git"
  | "fileWatcher"
  | "documentSymbols"
  | "openSource"
  | "diagnostics"
  | "secretStorage"
  | "statusBar"
  | "outputChannel";

export type HostCapabilitiesFlags = Record<HostCapabilityName, boolean>;

export interface HostGitSummary {
  available: boolean;
  branch: string;
  head: string;
  ahead: number;
  behind: number;
  dirtyCount: number;
}

export interface HostDiagnosticInput {
  file: string;
  line?: number;
  endLine?: number;
  message: string;
  severity?: "error" | "warning" | "info";
  code?: string;
}

export interface WorkspaceDirtyEvent {
  projectRoot: string;
  changedPaths: string[];
}

export interface HostBridge {
  call(
    command: string,
    payload: object,
    options?: HostCallOptions,
  ): Promise<HostCallResult<unknown>>;
  cancel?(command: string, payload: object, options?: { confirm?: boolean }): void;
  getState(): unknown;
  setState(state: unknown): void;
  getContext?(): unknown;
  getTheme?(): string;
  onContext?(cb: (context: HostContext) => void): () => void;
  onTheme?(cb: (theme: string) => void): () => void;
  openPath?(path: string): Promise<HostCallResult<boolean>>;
  pickDirectory?(): Promise<HostCallResult<string>>;
  openSource?(
    path: string,
    line?: number,
    column?: number,
  ): Promise<HostCallResult<{ path: string; line: number; column: number }>>;
  publishDiagnostics?(
    items: HostDiagnosticInput[],
  ): Promise<HostCallResult<{ count: number; unsupported?: boolean }>>;
  clearDiagnostics?(): Promise<HostCallResult<boolean>>;
  onWorkspaceDirty?(cb: (event: WorkspaceDirtyEvent) => void): () => void;
}

export interface HostContext {
  projectId?: string;
  projectRoot?: string;
  pythonPath?: string;
  workspacePath?: string;
  workspaceFolderCount?: number;
  themeKind?: "dark" | "light";
  capabilities?: Partial<HostCapabilitiesFlags>;
  hostGit?: HostGitSummary | null;
}

declare global {
  interface Window {
    aitestHost?: HostBridge;
  }
}

/** 单次调用兜底上限；首次运行时准备可能需要下载与离线安装。 */
const CALL_TIMEOUT_MS = 300_000;

/**
 * 长命令的等待预算必须覆盖扩展侧超时；否则扩展还在安装/生成，
 * 面板先报“结果未知”，用户重试会重复安装或重复生成报告。
 */
const COMMAND_TIMEOUT_MS: Readonly<Record<string, number>> = {
  "run.start": 120_000,
  "run.quick": 120_000,
  "run.repair": 630_000,
  "backup.create": 630_000,
  "backup.restore": 630_000,
  "report.generate": 330_000,
  "plan.generate": 210_000,
  "project.map.refresh": 210_000,
  "delivery.draft": 210_000,
  "task.add": 210_000,
  "template.apply": 120_000,
  "analysis.run": 120_000,
  "github.read": 150_000,
  "link.import": 150_000,
};

function timeoutFor(command: string): number {
  return COMMAND_TIMEOUT_MS[command] ?? CALL_TIMEOUT_MS;
}

export class HostError extends Error {
  readonly code: string;
  readonly nextStep: string;

  constructor(error: HostErrorPayload) {
    super(error.message);
    this.name = "HostError";
    this.code = error.code;
    this.nextStep = error.next_step ?? "";
  }
}

export class HostMissingError extends Error {
  constructor() {
    super("没有连接编辑器宿主");
    this.name = "HostMissingError";
  }
}

export function hostBridge(): HostBridge | null {
  const host = typeof window === "undefined" ? undefined : window.aitestHost;
  return host && typeof host.call === "function" ? host : null;
}

export function isHostReady(): boolean {
  return hostBridge() !== null;
}

export function readHostState<T>(): T | undefined {
  try {
    const value = hostBridge()?.getState();
    return value && typeof value === "object" ? (value as T) : undefined;
  } catch {
    return undefined;
  }
}

export function writeHostState(state: unknown): void {
  try {
    hostBridge()?.setState(state);
  } catch {
    // 宿主状态只用于恢复视图；写不进去不影响业务数据。
  }
}

/** 读取宿主缓存的编辑器上下文；宿主不支持时返回 null。 */
export function readHostContext(): HostContext | null {
  try {
    const value = hostBridge()?.getContext?.();
    return value && typeof value === "object" ? (value as HostContext) : null;
  } catch {
    return null;
  }
}

/** 读取宿主当前主题极性；非宿主环境或不支持时返回 null。 */
export function readHostTheme(): "dark" | "light" | null {
  try {
    const host = hostBridge();
    const direct = host?.getTheme?.();
    if (direct === "dark" || direct === "light") {
      return direct;
    }
    const fromCtx = readHostContext()?.themeKind;
    return fromCtx === "dark" || fromCtx === "light" ? fromCtx : null;
  } catch {
    return null;
  }
}

export function onHostContext(cb: (context: HostContext) => void): () => void {
  const host = hostBridge();
  if (!host || typeof host.onContext !== "function") {
    return () => undefined;
  }
  return host.onContext(cb);
}

export function onHostTheme(cb: (theme: "dark" | "light") => void): () => void {
  const host = hostBridge();
  if (!host || typeof host.onTheme !== "function") {
    return () => undefined;
  }
  return host.onTheme((value) => {
    if (value === "dark" || value === "light") {
      cb(value);
    }
  });
}

/** 让宿主弹出目录选择器；取消时返回 null。 */
export async function pickHostDirectory(): Promise<string | null> {
  const host = hostBridge();
  if (host === null || typeof host.pickDirectory !== "function") {
    return null;
  }
  const result = await host.pickDirectory();
  if (!result.ok) {
    throw new HostError(result.error);
  }
  return result.data.trim() || null;
}

/** 让宿主用系统程序打开一个已保存的证据文件。 */
export async function openHostPath(path: string): Promise<boolean> {
  const host = hostBridge();
  if (host === null) {
    throw new HostMissingError();
  }
  if (typeof host.openPath !== "function") {
    throw new HostError({
      code: "HOST_CALL_FAILED",
      message: "当前宿主不支持打开文件",
      next_step: "复制证据引用后在资源管理器中打开",
    });
  }
  const result = await host.openPath(path);
  if (!result.ok) {
    throw new HostError(result.error);
  }
  return Boolean(result.data);
}

/** 跳转到已绑定项目内的源码指定行，非宿主或不支持时抛出可读提示错误。 */
export async function openHostSource(path: string, line = 1, column = 1): Promise<boolean> {
  const host = hostBridge();
  if (host === null || typeof host.openSource !== "function") {
    throw new HostError({
      code: "HOST_UNSUPPORTED",
      message: "当前环境不支持一键跳源码",
      next_step: "请在编辑器侧边文件树中打开该文件",
    });
  }
  const result = await host.openSource(path, line, column);
  if (!result.ok) {
    throw new HostError(result.error);
  }
  return true;
}

/** 将结构化缺陷诊断推送到编辑器原生“问题（Problems）”面板；无宿主能力时静默跳过。 */
export async function publishHostDiagnostics(items: HostDiagnosticInput[]): Promise<number> {
  const host = hostBridge();
  if (host === null || typeof host.publishDiagnostics !== "function") {
    return 0;
  }
  try {
    const result = await host.publishDiagnostics(items);
    return result.ok ? result.data.count : 0;
  } catch {
    return 0;
  }
}

/** 清除由 AI 测试推送到宿主“问题”面板的诊断条目；无宿主能力时静默跳过。 */
export async function clearHostDiagnostics(): Promise<boolean> {
  const host = hostBridge();
  if (host === null || typeof host.clearDiagnostics !== "function") {
    return false;
  }
  try {
    const result = await host.clearDiagnostics();
    return result.ok ? Boolean(result.data) : false;
  } catch {
    return false;
  }
}

/** 监听宿主工作区文件保存改动通知。 */
export function onHostWorkspaceDirty(cb: (event: WorkspaceDirtyEvent) => void): () => void {
  const host = hostBridge();
  if (!host || typeof host.onWorkspaceDirty !== "function") {
    return () => undefined;
  }
  return host.onWorkspaceDirty(cb);
}

/** 同一命令 + 参数仍在执行时复用同一个 Promise，避免超时重试造成重复副作用。 */
const inFlight = new Map<string, Promise<unknown>>();

export interface HostActivityObserver {
  onStart(command: string, cancel: (() => void) | null): string;
  onProgress(token: string, event: HostProgressEvent): void;
  onEnd(token: string): void;
}

const LONG_RUNNING_COMMANDS: ReadonlySet<string> = new Set([
  "plan.generate",
  "project.map.refresh",
  "task.add",
  "task.suggest",
  "delivery.draft",
  "report.generate",
  "run.repair",
  "backup.create",
  "backup.restore",
  "analysis.run",
  "github.read",
]);

let activityObserver: HostActivityObserver | null = null;

export function setHostActivityObserver(observer: HostActivityObserver | null): void {
  activityObserver = observer;
}

function callKey(command: string, payload: object, options: HostCallOptions): string {
  return `${command}\u0000${options.confirm === true ? "1" : "0"}\u0000${JSON.stringify(payload)}`;
}

export function callHost<T>(
  command: string,
  payload: object = {},
  options: HostCallOptions = {},
): Promise<T> {
  const host = hostBridge();
  if (host === null) {
    return Promise.reject(new HostMissingError());
  }
  const key = callKey(command, payload, options);
  const existing = inFlight.get(key);
  if (existing) {
    return existing as Promise<T>;
  }
  const trackLong = LONG_RUNNING_COMMANDS.has(command) && activityObserver !== null;
  const activityToken = trackLong
    ? activityObserver!.onStart(
        command,
        typeof host.cancel === "function"
          ? () => host.cancel?.(command, payload, { confirm: options.confirm === true })
          : null,
      )
    : "";
  const promise = new Promise<T>((resolve, reject) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let hardTimer: ReturnType<typeof setTimeout> | null = null;
    const baseTimeout = timeoutFor(command);
    const hardCapMs = Math.max(baseTimeout * 2, 1_800_000);
    const clear = (): void => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      if (hardTimer !== null) {
        clearTimeout(hardTimer);
        hardTimer = null;
      }
    };
    const failTimeout = (): void => {
      if (settled) {
        return;
      }
      settled = true;
      clear();
      // 通知扩展终止子进程；查询类命令由用户刷新确认结果。
      host.cancel?.(command, payload, { confirm: options.confirm === true });
      reject(
        new HostError({
          code: "PANEL_TIMEOUT",
          message: "这次操作等待超时，结果未知",
          next_step: "先刷新状态确认是否已经生效，不要直接重复提交",
        }),
      );
    };
    const arm = (): void => {
      if (timer !== null) {
        clearTimeout(timer);
      }
      // 宿主每收到一次进度/阶段事件就重置活跃计时；总耗时仍受 hardCapMs 封顶保护。
      timer = setTimeout(failTimeout, baseTimeout);
    };
    hardTimer = setTimeout(failTimeout, hardCapMs);
    arm();

    host
      .call(command, payload, {
        confirm: options.confirm === true,
        // 始终安装进度回调：宿主的运行时/长命令心跳必须能重置面板计时。
        onProgress: (event) => {
          if (!settled) {
            arm();
          }
          if (activityToken) {
            activityObserver?.onProgress(activityToken, event);
          }
          options.onProgress?.(event);
        },
      })
      .then((result) => {
        if (settled) {
          return;
        }
        settled = true;
        clear();
        if (result.ok) {
          resolve(result.data as T);
        } else {
          reject(new HostError(result.error));
        }
      })
      .catch((error: unknown) => {
        if (settled) {
          return;
        }
        settled = true;
        clear();
        if (error instanceof HostError) {
          reject(error);
          return;
        }
        reject(
          new HostError({
            code: "HOST_CALL_FAILED",
            message: error instanceof Error ? error.message : "与编辑器的连接中断",
            next_step: "重新打开面板后再试",
          }),
        );
      });
  });
  inFlight.set(key, promise);
  const cleanup = (): void => {
    if (inFlight.get(key) === promise) {
      inFlight.delete(key);
    }
    if (activityToken) {
      activityObserver?.onEnd(activityToken);
    }
  };
  promise.then(cleanup, cleanup);
  return promise;
}
