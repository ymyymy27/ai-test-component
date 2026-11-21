/** 子进程最小接口：bridge 与 runtime 共用，便于单测注入假进程，不启动真实宿主。 */
import { spawn, spawnSync } from "node:child_process";
import { tmpdir } from "node:os";

export interface ReadableLike {
  setEncoding(encoding: BufferEncoding): void;
  on(event: "data", listener: (chunk: string) => void): unknown;
}

export interface ChildLike {
  /** 真实子进程的 pid；假进程可以省略。 */
  pid?: number;
  stdout: ReadableLike | null;
  stderr: ReadableLike | null;
  on(event: "error", listener: (error: Error) => void): unknown;
  on(event: "close", listener: (code: number | null) => void): unknown;
  kill(): boolean;
}

export interface SpawnOptionsLike {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  windowsHide?: boolean;
  stdio?: Array<"ignore" | "pipe">;
}

export type SpawnFn = (command: string, args: string[], options: SpawnOptionsLike) => ChildLike;

export const spawnProcess: SpawnFn = (command, args, options) =>
  spawn(command, args, options as never) as unknown as ChildLike;

/** 子进程一律在临时目录启动：打开不可信仓库时，不让 `-m` 命中仓库里的同名包。 */
export function safeWorkingDirectory(): string {
  return tmpdir();
}

/**
 * 宿主可能从无关的 Python（例如 uv）继承 PYTHONHOME/PYTHONPATH，
 * 会让另一套 3.11 在 `-m` 之前失败，或让仓库内同名模块覆盖已安装的 aitest。
 * 这里只保留解释器自身需要的变量，并关闭用户 site 与隐式脚本目录。
 */
export function cleanEnvironment(): NodeJS.ProcessEnv {
  const env = { ...process.env };
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  delete env.PYTHONSTARTUP;
  delete env.PYTHONUSERBASE;
  env.PYTHONUTF8 = "1";
  env.PYTHONIOENCODING = "utf-8";
  env.PYTHONSAFEPATH = "1";
  env.PYTHONNOUSERSITE = "1";
  return env;
}

/**
 * 终止子进程及其后代。`child.kill()` 只杀直接子进程：`py -3` 场景下
 * 真正的 CLI、worker 与 pip 都是后代，超时后仍会继续写工作空间。
 * Windows 用 `taskkill /T`；其他平台保持普通 kill（本扩展只交付 Windows）。
 */
export function stopChild(child: ChildLike): void {
  if (process.platform === "win32" && typeof child.pid === "number" && child.pid > 0) {
    try {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
      });
    } catch {
      // taskkill 不可用时退回直接 kill。
    }
  }
  try {
    child.kill();
  } catch {
    // 进程已退出时 kill 会抛错，不影响结论。
  }
}

/** 只把稳定的 OS 错误码带到 UI，不回显解释器路径、stderr 或环境变量。 */
export function spawnFailureCode(error: unknown): string {
  const code = (error as NodeJS.ErrnoException | null)?.code;
  return typeof code === "string" ? code : "UNKNOWN";
}
