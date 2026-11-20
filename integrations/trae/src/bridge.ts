/** 轻量 CLI 桥：每个业务动作调用一次 `python -m aitest.cli`，不保留常驻进程。
 *
 * 契约（一期设计 03-CLI与数据契约）：
 * - 请求：`--workspace <dir> --input <json-file>`，信封 `{command, request_id, payload}`；
 * - 输出：stdout 一行 JSON，日志写 stderr；
 * - 退出码：0 成功，1 业务拒绝，2 参数/环境错误。
 *
 * 本文件只做进程调用与信封校验：超时、非零退出、非法 JSON、缺失字段都转成
 * 结构化错误；不解释业务结论，也不重算任何业务字段。
 */
import { randomUUID } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { cleanEnvironment, spawnFailureCode, spawnProcess, type SpawnFn } from "./process";

const MAX_OUTPUT_BYTES = 8 * 1024 * 1024;
const MAX_TIMEOUT_MS = 900_000;

export const DEFAULT_TIMEOUT_MS = 30_000;

export interface BridgeConfig {
  /** 解释器路径，来自运行环境发现。 */
  readonly pythonPath: string;
  /** 解释器前置参数，例如 `py -3` 的 `["-3"]`。 */
  readonly pythonArgs?: readonly string[];
  /** 用户数据工作空间，不是被打开的源码目录。 */
  readonly workspacePath: string;
  /** 单次调用默认超时。 */
  readonly timeoutMs?: number;
}

export interface CallOptions {
  /** 只对需要显式确认的命令（run.start、data.cleanup）生效。 */
  readonly confirm?: boolean;
  readonly timeoutMs?: number;
}

export interface BridgeError {
  readonly code: string;
  readonly message: string;
  readonly next_step?: string;
}

export interface BridgeResult {
  readonly ok: boolean;
  readonly request_id?: string;
  readonly data?: unknown;
  readonly error?: BridgeError;
  readonly exitCode?: number | null;
  /** 仅扩展本地诊断使用（例如运行环境发现结果），不来自 CLI。 */
  readonly details?: unknown;
}

interface RunResult {
  readonly code: number | null;
  readonly stdout: string;
  readonly stderr: string;
  readonly timedOut: boolean;
  readonly oversized: boolean;
  readonly spawnError?: string;
}

function failure(
  code: string,
  message: string,
  next_step?: string,
  exitCode?: number | null,
): BridgeResult {
  return {
    ok: false,
    error: { code, message, ...(next_step ? { next_step } : {}) },
    ...(exitCode === undefined ? {} : { exitCode }),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeError(raw: unknown): BridgeError | null {
  if (!isRecord(raw) || typeof raw["code"] !== "string" || !raw["code"].trim()) {
    return null;
  }
  const message = typeof raw["message"] === "string" && raw["message"].trim()
    ? raw["message"]
    : "CLI 拒绝了该请求";
  const nextStep = typeof raw["next_step"] === "string" && raw["next_step"].trim()
    ? raw["next_step"]
    : undefined;
  return {
    code: raw["code"],
    message,
    ...(nextStep ? { next_step: nextStep } : {}),
  };
}

/** 解析 stdout 的第一行合法 JSON 对象；契约要求 CLI 只输出一行。 */
function parseEnvelope(raw: string): Record<string, unknown> | null {
  for (const line of raw.split(/\r?\n/)) {
    const text = line.trim();
    if (!text) {
      continue;
    }
    try {
      const value: unknown = JSON.parse(text);
      if (isRecord(value)) {
        return value;
      }
    } catch {
      // 继续尝试下一行；CLI 若把日志写进 stdout，也不会被当成结果。
    }
  }
  return null;
}

function runProcess(
  spawnFn: SpawnFn,
  command: string,
  args: string[],
  timeoutMs: number,
): Promise<RunResult> {
  return new Promise<RunResult>((resolve) => {
    let child: ReturnType<SpawnFn>;
    try {
      child = spawnFn(command, args, {
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
        env: cleanEnvironment(),
      });
    } catch (error) {
      resolve({
        code: null,
        stdout: "",
        stderr: "",
        timedOut: false,
        oversized: false,
        spawnError: spawnFailureCode(error),
      });
      return;
    }

    let stdout = "";
    let stderr = "";
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let settled = false;

    const finish = (result: RunResult): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish({ code: null, stdout, stderr, timedOut: true, oversized: false });
    }, timeoutMs);

    const collect = (chunk: string, stream: "stdout" | "stderr"): void => {
      const bytes = Buffer.byteLength(chunk, "utf8");
      if (stream === "stdout") {
        stdoutBytes += bytes;
        if (stdoutBytes > MAX_OUTPUT_BYTES) {
          child.kill();
          finish({ code: null, stdout: "", stderr: "", timedOut: false, oversized: true });
          return;
        }
        stdout += chunk;
      } else {
        stderrBytes += bytes;
        if (stderrBytes > MAX_OUTPUT_BYTES) {
          child.kill();
          finish({ code: null, stdout: "", stderr: "", timedOut: false, oversized: true });
          return;
        }
        stderr += chunk;
      }
    };

    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => collect(chunk, "stdout"));
    child.stderr?.on("data", (chunk: string) => collect(chunk, "stderr"));
    child.on("error", (error: Error) => {
      finish({
        code: null,
        stdout: "",
        stderr: "",
        timedOut: false,
        oversized: false,
        spawnError: spawnFailureCode(error),
      });
    });
    child.on("close", (code: number | null) => {
      finish({ code, stdout, stderr, timedOut: false, oversized: false });
    });
  });
}

export class Bridge {
  constructor(
    private readonly config: BridgeConfig,
    private readonly spawnFn: SpawnFn = spawnProcess,
  ) {}

  /** `call(command, payload, {confirm})`：一次调用即一次业务意图。 */
  async call(
    command: string,
    payload: Record<string, unknown> = {},
    options: CallOptions = {},
  ): Promise<BridgeResult> {
    if (!/^[a-z]+(?:\.[a-z]+)*$/.test(command)) {
      return failure("INVALID_REQUEST", `命令名不合法：${command || "(空)"}`, "检查命令名拼写");
    }
    if (!this.config.pythonPath.trim()) {
      return failure("PYTHON_NOT_FOUND", "尚未确定可用的 Python 解释器", "先在设置中配置 aitest.pythonPath");
    }
    if (!this.config.workspacePath.trim()) {
      return failure(
        "WORKSPACE_NOT_CONFIGURED",
        "尚未设置 aitest.workspacePath（用户数据工作空间）",
        "在设置中填写工作空间目录；不要指向源码目录",
      );
    }
    const requested = options.timeoutMs ?? this.config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const timeoutMs = Math.min(Math.max(Math.trunc(requested), 1), MAX_TIMEOUT_MS);
    const requestId = randomUUID();

    let directory: string | null = null;
    try {
      directory = await mkdtemp(join(tmpdir(), "aitest-bridge-"));
      const inputFile = join(directory, "input.json");
      await writeFile(
        inputFile,
        JSON.stringify({ command, request_id: requestId, payload }),
        "utf8",
      );
      const args = [
        ...(this.config.pythonArgs ?? []),
        "-m",
        "aitest.cli",
        command,
        "--workspace",
        this.config.workspacePath,
        "--input",
        inputFile,
        ...(options.confirm ? ["--confirm"] : []),
      ];
      const result = await runProcess(this.spawnFn, this.config.pythonPath, args, timeoutMs);
      return this.interpret(result, requestId, timeoutMs);
    } catch (error) {
      const code = (error as NodeJS.ErrnoException | null)?.code;
      return failure(
        "CLI_IO_FAILED",
        `无法准备 CLI 输入文件（${typeof code === "string" ? code : "UNKNOWN"}）`,
        "检查临时目录权限与磁盘空间",
      );
    } finally {
      if (directory !== null) {
        await rm(directory, { recursive: true, force: true }).catch(() => undefined);
      }
    }
  }

  private interpret(result: RunResult, requestId: string, timeoutMs: number): BridgeResult {
    if (result.timedOut) {
      return failure(
        "CLI_TIMEOUT",
        `CLI 在 ${timeoutMs} 毫秒内没有返回`,
        "先用 run.status 查询原意图是否已生效，不要直接重复启动",
      );
    }
    if (result.spawnError) {
      return failure(
        "CLI_SPAWN_FAILED",
        `无法启动 Python 进程（${result.spawnError}）`,
        "检查 aitest.pythonPath 是否指向 Python 3.11+",
      );
    }
    if (result.oversized) {
      return failure(
        "CLI_OUTPUT_LIMIT",
        "CLI 输出超出允许大小，结果未采用",
        "检查 CLI 是否把日志写进了 stdout",
      );
    }
    const envelope = parseEnvelope(result.stdout);
    if (envelope === null) {
      if (result.code !== 0) {
        return failure(
          "CLI_EXIT_NONZERO",
          `CLI 以退出码 ${result.code} 结束，且未返回 JSON 信封`,
          "运行 doctor 检查环境，或查看 CLI stderr 日志",
          result.code,
        );
      }
      return failure(
        "CLI_INVALID_JSON",
        "CLI 输出不是合法 JSON 信封",
        "确认 aitest 版本与扩展匹配",
        result.code,
      );
    }
    if (typeof envelope["ok"] !== "boolean") {
      return failure("CLI_INVALID_JSON", "CLI 回执缺少 ok 字段", "确认 aitest 版本与扩展匹配", result.code);
    }
    if (envelope["ok"]) {
      const echoed = envelope["request_id"];
      if (echoed !== undefined && echoed !== requestId) {
        return failure(
          "CLI_INVALID_JSON",
          "CLI 回执的 request_id 与本次请求不一致",
          "重新执行；若已有副作用，先用查询命令核对",
          result.code,
        );
      }
      if (result.code !== 0) {
        return failure(
          "CLI_EXIT_NONZERO",
          `CLI 声称成功但退出码为 ${result.code}`,
          "运行 doctor 检查环境",
          result.code,
        );
      }
      return {
        ok: true,
        request_id: requestId,
        data: envelope["data"] ?? null,
        exitCode: result.code,
      };
    }
    const error = normalizeError(envelope["error"]);
    if (error === null) {
      return failure(
        "CLI_INVALID_JSON",
        "CLI 失败回执缺少 error.code",
        "确认 aitest 版本与扩展匹配",
        result.code,
      );
    }
    return {
      ok: false,
      request_id: typeof envelope["request_id"] === "string" ? envelope["request_id"] : requestId,
      error,
      exitCode: result.code,
    };
  }
}

export function createBridge(config: BridgeConfig, spawnFn: SpawnFn = spawnProcess): Bridge {
  return new Bridge(config, spawnFn);
}
