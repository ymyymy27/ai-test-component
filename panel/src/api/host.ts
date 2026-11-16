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

export interface HostBridge {
  call(
    command: string,
    payload: object,
    options?: { confirm?: boolean },
  ): Promise<HostCallResult<unknown>>;
  getState(): unknown;
  setState(state: unknown): void;
  getContext?(): unknown;
  openPath?(path: string): Promise<HostCallResult<boolean>>;
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

/** 单次调用的兜底上限；正常由宿主 bridge 自己控制超时。 */
const CALL_TIMEOUT_MS = 180_000;

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

let sequence = 0;

export function callHost<T>(
  command: string,
  payload: object = {},
  options: { confirm?: boolean } = {},
): Promise<T> {
  const host = hostBridge();
  if (host === null) {
    return Promise.reject(new HostMissingError());
  }
  sequence += 1;
  const label = `${command}#${sequence}`;
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      settled = true;
      reject(
        new HostError({
          code: "PANEL_TIMEOUT",
          message: "这次操作等待超时，结果未知",
          next_step: "先刷新状态确认是否已经生效，不要直接重复提交",
        }),
      );
    }, CALL_TIMEOUT_MS);

    host
      .call(command, payload, options)
      .then((result) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);
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
        clearTimeout(timer);
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
    void label;
  });
}
