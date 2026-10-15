/** 本地核心桥：编辑器入口经**既有 CLI**向唯一核心派发 Command。
 *
 * 设计约束（对应根 AGENTS.md 与一期架构 05 第 3 节）：
 * - **不复制协议**：不自行实现命名管道帧、不自行核对实例/工作空间身份与回执，
 *   一律交给 `aitest ... dispatch`（它已做端点发现、身份核对与回执校验）。
 * - **不把界面点击当作用户确认**：写动作携带 project/intent/expected_revision，
 *   只请求核心独立窗口确认；扩展不代签，普通派发仍只读。
 * - **不猜测**：核心不可达或回执无法核实时返回结构化 `unavailable`，不伪造成功。
 */

import { spawn } from "node:child_process";

/** 一期只读动作（`contracts/commands.py::READ_ACTIONS` 的子集，按扩展需要列明）。 */
export const READ_ONLY_ACTIONS: ReadonlySet<string> = new Set([
  "doctor",
  "query",
  "read_view",
  "events",
  "storage_usage",
  "check_source",
  "inspect_case_reuse",
  "copy_repair_brief",
]);

const PROTOCOL_VERSION = "aitest.local/2.0";
const MAX_STDOUT_BYTES = 8 * 1024 * 1024;

export interface DispatchRequest {
  readonly action: string;
  readonly parameters?: Readonly<Record<string, unknown>>;
  readonly projectId?: string | null;
  readonly bindingRevision?: number | null;
  readonly intentId?: string | null;
  readonly expectedRevision?: number | null;
  readonly target?: string | null;
}

export interface CoreCommand {
  readonly protocol_version: typeof PROTOCOL_VERSION;
  readonly request_id: string;
  readonly action: string;
  readonly project_id?: string | null;
  readonly binding_revision?: number | null;
  readonly intent_id?: string;
  readonly expected_revision?: number;
  readonly target?: string;
  readonly parameters: Record<string, unknown>;
}

export interface DispatchOutcome {
  /** `ok` 只有拿到核心回执且无 error 时成立；其余一律如实返回原因。 */
  readonly status: "ok" | "refused" | "unavailable";
  readonly response?: Record<string, unknown>;
  readonly code?: string;
  readonly message?: string;
}

export class BridgeRefused extends Error {
  readonly code = "ENTRY_ACTION_NOT_ALLOWED";
}

/** 构造一份完整 Command；写动作的身份字段由调用方提供，扩展不补造。 */
export function buildCommand(request: DispatchRequest, requestId: string): CoreCommand {
  if (!request.action.trim()) {
    throw new BridgeRefused("action must not be empty");
  }
  const command: CoreCommand = {
    protocol_version: PROTOCOL_VERSION,
    request_id: requestId,
    action: request.action,
    parameters: { ...(request.parameters ?? {}) },
    ...(request.projectId ? { project_id: request.projectId } : {}),
    ...(typeof request.bindingRevision === "number"
      ? { binding_revision: request.bindingRevision }
      : {}),
    ...(request.intentId ? { intent_id: request.intentId } : {}),
    ...(typeof request.expectedRevision === "number" ? { expected_revision: request.expectedRevision } : {}),
    ...(request.target ? { target: request.target } : {}),
  };
  return command;
}

/** 只读白名单校验：非只读动作必须走受控用户入口，扩展不代签。 */
export function requireReadOnly(request: DispatchRequest): void {
  if (!READ_ONLY_ACTIONS.has(request.action)) {
    throw new BridgeRefused(
        "action is not a read-only entry action; " +
        "write actions require the controlled user entry",
    );
  }
}

interface SpawnResult {
  readonly code: number | null;
  readonly stdout: string;
  readonly stderr: string;
  readonly timedOut: boolean;
  readonly oversized?: boolean;
}

function runCli(
  python: string,
  workspaceRoot: string,
  command: CoreCommand,
  timeoutMs: number,
  nativeConfirmation = false,
): Promise<SpawnResult> {
  return new Promise<SpawnResult>((resolve) => {
    const child = spawn(
      python,
      ["-m", "aitest.interfaces.tools.cli", "dispatch",
        ...(nativeConfirmation ? ["--native-confirmation"] : []), "--workspace", workspaceRoot],
      { stdio: ["pipe", "pipe", "pipe"], windowsHide: true },
    );
    let stdout = "";
    let stderr = "";
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let settled = false;
    const finish = (result: SpawnResult): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish({ code: null, stdout, stderr, timedOut: true });
    }, timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      if (settled) { return; }
      stdoutBytes += Buffer.byteLength(chunk, "utf8");
      if (stdoutBytes > MAX_STDOUT_BYTES) {
        child.kill();
        finish({ code: null, stdout: "", stderr: "", timedOut: false, oversized: true });
        return;
      }
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      if (settled) { return; }
      stderrBytes += Buffer.byteLength(chunk, "utf8");
      if (stderrBytes > MAX_STDOUT_BYTES) {
        child.kill();
        finish({ code: null, stdout: "", stderr: "", timedOut: false, oversized: true });
        return;
      }
      stderr += chunk;
    });
    child.on("error", (error: Error) => {
      finish({ code: null, stdout, stderr: `${stderr}${error.message}`, timedOut: false });
    });
    child.on("close", (code: number | null) => {
      finish({ code, stdout, stderr, timedOut: false });
    });
    child.stdin.on("error", () => {
      child.kill();
      finish({ code: null, stdout: "", stderr: "", timedOut: false });
    });
    child.stdin.end(`${JSON.stringify(command)}\n`, "utf8");
  });
}

function parseResponse(raw: string): Record<string, unknown> | null {
  const text = raw.trim();
  if (!text) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(text);
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** 确保该工作空间有唯一核心在服务（宿主职责，架构 05 第 1 节）。
 *
 * 走 `aitest ensure-core`：先连既有核心，连不上再启动一个；身份由核心侧核对。
 * 本调用**不派发业务动作**，也不写业务记录；路径缺失时核心侧结构化拒绝，
 * 我们如实转述，不替代它做猜测。
 */
export async function ensureCore(
  python: string,
  workspaceRoot: string,
  timeoutMs = 60_000,
  pythonCarriersFile?: string,
  businessQueriesFile?: string,
): Promise<DispatchOutcome> {
  if (!python.trim() || !Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 60_000) {
    return { status: "unavailable", code: "ENTRY_INVALID_CONFIG", message: "解释器或等待预算无法核实" };
  }
  if (!workspaceRoot.trim()) {
    return {
      status: "unavailable",
      code: "WORKSPACE_NOT_CONFIGURED",
      message: "尚未配置 aitest.workspaceRoot（用户数据工作空间），入口不能猜测其位置",
    };
  }
  return new Promise<DispatchOutcome>((resolve) => {
    const child = spawn(
      python,
      ["-m", "aitest.interfaces.tools.cli", "ensure-core", "--workspace", workspaceRoot],
      { stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
        env: {...process.env,
          ...(pythonCarriersFile ? {AITEST_PYTHON_CARRIERS:pythonCarriersFile} : {}),
          ...(businessQueriesFile ? {AITEST_BUSINESS_QUERIES:businessQueriesFile} : {})} },
    );
    let stdout = "";
    let stderr = "";
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let settled = false;
    const finish = (outcome: DispatchOutcome): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      resolve(outcome);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish({ status: "unavailable", code: "ENTRY_TIMEOUT", message: "启动核心在限定时间内未回报" });
    }, timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      if (settled) { return; }
      stdoutBytes += Buffer.byteLength(chunk, "utf8");
      if (stdoutBytes > MAX_STDOUT_BYTES) {
        child.kill();
        finish({ status: "unavailable", code: "ENTRY_OUTPUT_LIMIT", message: "核心入口输出超出允许预算，结果未核实" });
        return;
      }
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      if (settled) { return; }
      stderrBytes += Buffer.byteLength(chunk, "utf8");
      if (stderrBytes > MAX_STDOUT_BYTES) {
        child.kill();
        finish({ status: "unavailable", code: "ENTRY_OUTPUT_LIMIT", message: "核心入口输出超出允许预算，结果未核实" });
        return;
      }
      stderr += chunk;
    });
    child.on("error", () => {
      finish({ status: "unavailable", code: "ENTRY_SPAWN_FAILED", message: "无法启动已配置的本地核心入口" });
    });
    child.on("close", (code: number | null) => {
      const parsed = parseResponse(stdout);
      if (code === 0 && parsed !== null && parsed["status"] === "READY"
          && typeof parsed["workspace_id"] === "string" && parsed["workspace_id"].trim()
          && typeof parsed["instance_id"] === "string" && parsed["instance_id"].trim()) {
        finish({ status: "ok", response: parsed });
        return;
      }
      finish({
        status: "refused",
        code: "CORE_UNAVAILABLE",
        message:
          "核心未能在限定时间内核实并连接，请查看受控诊断",
      });
    });
  }).catch(() => ({ status: "unavailable", code: "ENTRY_SPAWN_FAILED", message: "无法启动已配置的本地核心入口" }));
}

/**
 * 派发一份只读 Command 到唯一核心。
 *
 * @param python 解释器（需已安装本包，或设置 PYTHONPATH 指向 `src`）
 * @param workspaceRoot 用户数据工作空间根（不是被打开的源码目录）
 */
export async function dispatchReadOnly(
  python: string,
  workspaceRoot: string,
  request: DispatchRequest,
  requestId: string,
  timeoutMs = 30_000,
): Promise<DispatchOutcome> {
  requireReadOnly(request);
  return dispatchCommand(python, workspaceRoot, request, requestId, timeoutMs, false);
}

/** This requests a core-owned dialog; it never sends an assertion of human approval. */
export async function dispatchControlled(
  python: string, workspaceRoot: string, request: DispatchRequest, requestId: string,
  timeoutMs = 150_000,
): Promise<DispatchOutcome> {
  if (READ_ONLY_ACTIONS.has(request.action) || !request.projectId || !request.intentId
      || request.intentId === requestId || !Number.isSafeInteger(request.expectedRevision)
      || (request.expectedRevision ?? -1) < 0) {
    throw new BridgeRefused("受控写入需要项目、稳定业务意图和准确的预期修订");
  }
  return dispatchCommand(python, workspaceRoot, request, requestId, timeoutMs, true);
}

async function dispatchCommand(
  python: string, workspaceRoot: string, request: DispatchRequest, requestId: string,
  timeoutMs: number, nativeConfirmation: boolean,
): Promise<DispatchOutcome> {
  if (!python.trim() || !Number.isFinite(timeoutMs) || timeoutMs <= 0
      || timeoutMs > (nativeConfirmation ? 150_000 : 60_000)) {
    return { status: "unavailable", code: "ENTRY_INVALID_CONFIG", message: "解释器或等待预算无法核实" };
  }
  if (!workspaceRoot.trim()) {
    return {
      status: "unavailable",
      code: "WORKSPACE_NOT_CONFIGURED",
      message: "尚未配置 aitest.workspaceRoot（用户数据工作空间），入口不能猜测其位置",
    };
  }
  const command = buildCommand(request, requestId);
  let result: SpawnResult;
  try {
    result = await runCli(python, workspaceRoot, command, timeoutMs, nativeConfirmation);
  } catch {
    return {
      status: "unavailable",
      code: "ENTRY_SPAWN_FAILED",
      message: "无法启动已配置的本地核心入口",
    };
  }
  if (result.timedOut) {
    return { status: "unavailable", code: "ENTRY_TIMEOUT",
      message: "核心入口在限定时间内无回执；请查询原业务意图，勿自动创建新意图重试" };
  }
  if (result.oversized) {
    return { status: "unavailable", code: "ENTRY_OUTPUT_LIMIT", message: "核心入口输出超出允许预算，结果未核实" };
  }
  const response = parseResponse(result.stdout);
  if (response === null) {
    return {
      status: "unavailable",
      code: "CORE_RESULT_UNVERIFIED",
      message: "核心未返回可核实的回执，请查看受控诊断",
    };
  }
  // 回执身份核对交给 CLI/核心；这里只检查请求-回执的对应关系，不重算业务结论。
  if (response["request_id"] !== command.request_id || response["protocol_version"] !== PROTOCOL_VERSION
      || typeof response["instance_id"] !== "string" || !response["instance_id"].trim()
      || typeof response["workspace_id"] !== "string" || !response["workspace_id"].trim()
      || (request.projectId && response["project_id"] !== request.projectId)
      || (request.intentId && response["intent_id"] !== request.intentId)
      || (request.bindingRevision != null && response["binding_revision"] !== request.bindingRevision)) {
    return {
      status: "unavailable",
      code: "CORE_RESULT_UNVERIFIED",
      message: "回执身份或协议与请求不一致，结果未核实",
    };
  }
  if (response["error"] !== null && response["error"] !== undefined) {
    if (typeof response["error"] !== "object" || Array.isArray(response["error"])) {
      return { status: "unavailable", code: "CORE_RESULT_UNVERIFIED", message: "核心错误回执形状无法核实" };
    }
    const error = response["error"] as Record<string, unknown>;
    return {
      status: "refused",
      response,
      code: typeof error["code"] === "string" ? error["code"] : "UNKNOWN_ERROR",
      message: typeof error["message"] === "string" ? error["message"] : "核心拒绝该请求",
    };
  }
  if (result.code !== 0 || typeof response["result"] !== "object" || response["result"] === null || Array.isArray(response["result"])) {
    return { status: "unavailable", code: "CORE_RESULT_UNVERIFIED", message: "核心退出状态或结果形状无法核实" };
  }
  return { status: "ok", response };
}
