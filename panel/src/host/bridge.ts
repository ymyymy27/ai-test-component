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
}

export interface HostCallOptions {
  confirm?: boolean;
  onProgress?: (event: HostProgressEvent) => void;
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
  openPath?(path: string): Promise<HostCallResult<boolean>>;
  pickDirectory?(): Promise<HostCallResult<string>>;
}

export interface HostContext {
  projectId?: string;
  projectRoot?: string;
  pythonPath?: string;
  workspacePath?: string;
  workspaceFolderCount?: number;
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

/** 同一命令 + 参数仍在执行时复用同一个 Promise，避免超时重试造成重复副作用。 */
const inFlight = new Map<string, Promise<unknown>>();

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
  const promise = new Promise<T>((resolve, reject) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const clear = (): void => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    };
    const arm = (): void => {
      clear();
      // 宿主每收到一次进度/阶段事件就重置计时：下载、安装与长报告仍在推进时不算超时。
      timer = setTimeout(() => {
        settled = true;
        // 通知扩展终止子进程；查询类命令由用户刷新确认结果。
        host.cancel?.(command, payload, { confirm: options.confirm === true });
        reject(
          new HostError({
            code: "PANEL_TIMEOUT",
            message: "这次操作等待超时，结果未知",
            next_step: "先刷新状态确认是否已经生效，不要直接重复提交",
          }),
        );
      }, timeoutFor(command));
    };
    arm();

    host
      .call(command, payload, {
        confirm: options.confirm === true,
        // 始终安装进度回调：宿主的运行时/长命令心跳必须能重置面板计时。
        onProgress: (event) => {
          if (!settled) {
            arm();
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
  };
  promise.then(cleanup, cleanup);
  return promise;
}
