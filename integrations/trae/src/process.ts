/** 子进程最小接口：bridge 与 runtime 共用，便于单测注入假进程，不启动真实宿主。 */
import { spawn } from "node:child_process";

export interface ReadableLike {
  setEncoding(encoding: BufferEncoding): void;
  on(event: "data", listener: (chunk: string) => void): unknown;
}

export interface ChildLike {
  stdout: ReadableLike | null;
  stderr: ReadableLike | null;
  on(event: "error", listener: (error: Error) => void): unknown;
  on(event: "close", listener: (code: number | null) => void): unknown;
  kill(): boolean;
}

export interface SpawnOptionsLike {
  env?: NodeJS.ProcessEnv;
  windowsHide?: boolean;
  stdio?: Array<"ignore" | "pipe">;
}

export type SpawnFn = (command: string, args: string[], options: SpawnOptionsLike) => ChildLike;

export const spawnProcess: SpawnFn = (command, args, options) =>
  spawn(command, args, options as never) as unknown as ChildLike;

/** 宿主可能从无关的 Python（例如 uv）继承 PYTHONHOME，会让另一套 3.11 在 `-m` 之前失败。 */
export function cleanEnvironment(): NodeJS.ProcessEnv {
  const env = { ...process.env };
  delete env.PYTHONHOME;
  env.PYTHONUTF8 = "1";
  env.PYTHONIOENCODING = "utf-8";
  return env;
}

/** 只把稳定的 OS 错误码带到 UI，不回显解释器路径、stderr 或环境变量。 */
export function spawnFailureCode(error: unknown): string {
  const code = (error as NodeJS.ErrnoException | null)?.code;
  return typeof code === "string" ? code : "UNKNOWN";
}
