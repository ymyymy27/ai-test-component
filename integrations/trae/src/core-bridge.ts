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
import { existsSync } from "node:fs";

/** 一期只读动作（`contracts/commands.py::READ_ACTIONS` 的子集，按扩展需要列明）。 */
export const READ_ONLY_ACTIONS: ReadonlySet<string> = new Set([
  "doctor",
  "query",
  "read_view",
  "events",
  "storage_usage",
  "discover_tests",
  "suggest_regression",
  "aggregate_project_runs",
  "check_source",
  "inspect_case_reuse",
  "copy_repair_brief",
  "plan_confirmation_state",
  "export_rules_markdown",
]);

/**
 * Ordinary controlled writes that do NOT require a per-action human gesture.
 *
 * This is deliberately an allowlist of `PHASE_ONE_ACTIONS` members that are
 * neither `HUMAN_ACTIONS` nor `CHALLENGE_ACTIONS` in the core.  Adding an action
 * here must be justified by the core contract: confirmations, publications,
 * authorizations and delivery submission stay on `dispatchControlled`.
 */
export const PLAIN_WRITE_ACTIONS: ReadonlySet<string> = new Set([
  "save_context",
  "save_environment",
  "save_case",
  "save_task",
  "save_delivery",
  "save_acceptance",
  "save_dependency_graph",
  "save_project_view_preference",
  "save_rules",
  "import_rules_markdown",
  "generate_draft",
  "request_model_draft",
  "resolve_model_response",
  "save_plan",
  "analyze_project",
  "prepare_run",
  "register_run",
  "prepare_execution",
  "execute_step",
  "start_run",
  "pause_run",
  "resume_run",
  "cancel_run",
  "retry_step",
  "return_stage",
  "verify_pending",
  "attach_evidence",
  "record_agent_evidence",
  "import_external_result",
  "create_report",
  "export_report",
  "record_fix",
  "request_regression",
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
  readonly spawnFailure?: string;
}

/** Only fixed OS classifications may cross the UI boundary, never raw stderr/path/env. */
function spawnFailure(error: unknown, python: string): string {
  const code = (error as NodeJS.ErrnoException | null)?.code;
  const classification = typeof code === "string" && ["ENOENT", "EACCES", "EPERM", "EINVAL", "ENOMEM", "UNKNOWN"].includes(code)
    ? code : "UNCLASSIFIED";
  return `${classification}; ${process.platform}; executable_exists=${existsSync(python)}`;
}

function coreEnvironment(): NodeJS.ProcessEnv {
  // The core runs on the user-configured interpreter. A host process may
  // inherit PYTHONHOME from an unrelated Python (e.g. uv), which makes a
  // different 3.11 installation fail before it can run `-m`. PYTHONPATH is
  // left alone: hosts and the packaged entry may use it for the core package.
  const env = { ...process.env };
  delete env.PYTHONHOME;
  return env;
}

function runCli(
  python: string,
  workspaceRoot: string,
  payload: unknown,
  timeoutMs: number,
  nativeConfirmation = false,
  panel = false,
): Promise<SpawnResult> {
  return new Promise<SpawnResult>((resolve) => {
    const child = spawn(
      python,
      ["-m", "aitest.interfaces.tools.cli",
        panel ? "dispatch-panel" : "dispatch",
        ...(nativeConfirmation ? ["--native-confirmation"] : []), "--workspace", workspaceRoot],
      { stdio: ["pipe", "pipe", "pipe"], windowsHide: true, env: coreEnvironment() },
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
      finish({ code: null, stdout: "", stderr: "", timedOut: false, spawnFailure: spawnFailure(error, python) });
    });
    child.on("close", (code: number | null) => {
      finish({ code, stdout, stderr, timedOut: false });
    });
    child.stdin.on("error", () => {
      child.kill();
      finish({ code: null, stdout: "", stderr: "", timedOut: false });
    });
    child.stdin.end(`${JSON.stringify(payload)}\n`, "utf8");
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

const ensureInFlight = new Map<string, Promise<DispatchOutcome>>();
const ensureReady = new Map<string, { at: number; response: Record<string, unknown> }>();
const ENSURE_CACHE_MS = 5_000;

/** 确保该工作空间有唯一核心在服务（宿主职责，架构 05 第 1 节）。
 *
 * 走 `aitest ensure-core`：先连既有核心，连不上再启动一个；身份由核心侧核对。
 * 本调用**不派发业务动作**，也不写业务记录；路径缺失时核心侧结构化拒绝，
 * 我们如实转述，不替代它做猜测。
 *
 * 面板的每次读写都会先走这里。多个并发调用必须共享同一次启动声明，
 * 否则它们会互相看到 `another host is claiming this core startup`。
 */
export async function ensureCore(
  python: string,
  workspaceRoot: string,
  timeoutMs = 60_000,
  pythonCarriersFile?: string,
  businessQueriesFile?: string,
  modelConfigurationFile?: string,
): Promise<DispatchOutcome> {
  const key = JSON.stringify([
    python, workspaceRoot, pythonCarriersFile ?? "",
    businessQueriesFile ?? "", modelConfigurationFile ?? "",
  ]);
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
  const existing = ensureInFlight.get(key);
  if (existing !== undefined) {
    return existing;
  }
  const cached = ensureReady.get(key);
  if (cached !== undefined && Date.now() - cached.at < ENSURE_CACHE_MS) {
    return { status: "ok", response: cached.response };
  }
  const pending = coreEntry("ensure-core", python, workspaceRoot, timeoutMs,
    pythonCarriersFile, businessQueriesFile, modelConfigurationFile)
    .then((outcome) => {
      if (outcome.status === "ok" && outcome.response !== undefined) {
        ensureReady.set(key, { at: Date.now(), response: outcome.response });
      }
      return outcome;
    })
    .finally(() => {
      if (ensureInFlight.get(key) === pending) {
        ensureInFlight.delete(key);
      }
    });
  ensureInFlight.set(key, pending);
  return pending;
}

/** 受控重启核心：先优雅停机，再启动并核对新实例；活动执行未排空时由核心拒绝。 */
export async function restartCore(
  python: string,
  workspaceRoot: string,
  timeoutMs = 120_000,
  pythonCarriersFile?: string,
  businessQueriesFile?: string,
  modelConfigurationFile?: string,
): Promise<DispatchOutcome> {
  ensureInFlight.clear();
  ensureReady.clear();
  return coreEntry("restart-core", python, workspaceRoot, timeoutMs,
    pythonCarriersFile, businessQueriesFile, modelConfigurationFile);
}

async function coreEntry(
  subcommand: "ensure-core" | "restart-core",
  python: string,
  workspaceRoot: string,
  timeoutMs: number,
  pythonCarriersFile?: string,
  businessQueriesFile?: string,
  modelConfigurationFile?: string,
): Promise<DispatchOutcome> {
  const maxTimeoutMs = subcommand === "ensure-core" ? 60_000 : 120_000;
  if (!python.trim() || !Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > maxTimeoutMs) {
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
      ["-m", "aitest.interfaces.tools.cli", subcommand, "--workspace", workspaceRoot],
      { stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
        env: {...coreEnvironment(),
          ...(pythonCarriersFile ? {AITEST_PYTHON_CARRIERS:pythonCarriersFile} : {}),
          ...(businessQueriesFile ? {AITEST_BUSINESS_QUERIES:businessQueriesFile} : {}),
          ...(modelConfigurationFile ? {AITEST_MODEL_CONFIGURATION:modelConfigurationFile} : {})} },
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
      finish({ status: "unavailable", code: "ENTRY_TIMEOUT",
        message: subcommand === "ensure-core"
          ? "启动核心在限定时间内未回报"
          : "重启核心在限定时间内未回报" });
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
    child.on("error", (error: Error) => {
      finish({ status: "unavailable", code: "ENTRY_SPAWN_FAILED", message: `无法启动已配置的本地核心入口（${spawnFailure(error, python)}）` });
    });
    child.on("close", (code: number | null) => {
      const parsed = parseResponse(stdout);
      if (code !== 0 && parsed !== null) {
        const errorCode = parsed["code"];
        const errorMessage = parsed["message"];
        if (typeof errorCode === "string" && errorCode
            && typeof errorMessage === "string" && errorMessage.trim()) {
          finish({ status: "refused", code: errorCode, message: errorMessage, response: parsed });
          return;
        }
      }
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
          subcommand === "ensure-core"
            ? "核心未能在限定时间内核实并连接，请查看受控诊断"
            : "核心未能在限定时间内重启并连接，请查看受控诊断",
      });
    });
  }).catch((error: unknown) => ({ status: "unavailable", code: "ENTRY_SPAWN_FAILED",
    message: `无法启动已配置的本地核心入口（${spawnFailure(error, python)}）` }));
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

function panelOutcome(
  result: SpawnResult,
  command: CoreCommand,
  request: DispatchRequest,
): DispatchOutcome {
  if (result.timedOut) {
    return { status: "unavailable", code: "ENTRY_TIMEOUT",
      message: "面板确认在限定时间内无回执；请查询原业务意图，勿自动重试" };
  }
  if (result.spawnFailure) {
    return { status: "unavailable", code: "ENTRY_SPAWN_FAILED",
      message: `无法启动已配置的本地核心入口（${result.spawnFailure}）` };
  }
  if (result.oversized) {
    return { status: "unavailable", code: "ENTRY_OUTPUT_LIMIT", message: "核心入口输出超出允许预算，结果未核实" };
  }
  const response = parseResponse(result.stdout);
  if (response === null) {
    return { status: "unavailable", code: "CORE_RESULT_UNVERIFIED", message: "核心未返回可核实的回执" };
  }
  if (response["request_id"] !== command.request_id || response["protocol_version"] !== PROTOCOL_VERSION
      || typeof response["instance_id"] !== "string" || !response["instance_id"].trim()
      || typeof response["workspace_id"] !== "string" || !response["workspace_id"].trim()
      || (request.projectId && response["project_id"] !== request.projectId)
      || (request.intentId && response["intent_id"] !== request.intentId)
      || (request.bindingRevision != null && response["binding_revision"] !== request.bindingRevision)) {
    return { status: "unavailable", code: "CORE_RESULT_UNVERIFIED",
      message: "回执身份或协议与请求不一致，结果未核实" };
  }
  if (response["error"] !== null && response["error"] !== undefined) {
    if (typeof response["error"] !== "object" || Array.isArray(response["error"])) {
      return { status: "unavailable", code: "CORE_RESULT_UNVERIFIED", message: "核心错误回执形状无法核实" };
    }
    const error = response["error"] as Record<string, unknown>;
    return { status: "refused", response,
      code: typeof error["code"] === "string" ? error["code"] : "UNKNOWN_ERROR",
      message: typeof error["message"] === "string" ? error["message"] : "核心拒绝该请求" };
  }
  if (result.code !== 0 || typeof response["result"] !== "object"
      || response["result"] === null || Array.isArray(response["result"])) {
    return { status: "unavailable", code: "CORE_RESULT_UNVERIFIED", message: "核心退出状态或结果形状无法核实" };
  }
  return { status: "ok", response };
}

/** This requests a core-owned dialog; it never sends an assertion of human approval. */
export async function dispatchControlled(
  python: string, workspaceRoot: string, request: DispatchRequest, requestId: string,
  timeoutMs = 270_000,
  panelConfirm?: (prompt: Record<string, unknown>) => Promise<boolean>,
): Promise<DispatchOutcome> {
  if (READ_ONLY_ACTIONS.has(request.action) || !request.projectId || !request.intentId
      || request.intentId === requestId || !Number.isSafeInteger(request.expectedRevision)
      || (request.expectedRevision ?? -1) < 0) {
    throw new BridgeRefused("受控写入需要项目、稳定业务意图和准确的预期修订");
  }
  if (!panelConfirm) {
    return dispatchCommand(python, workspaceRoot, request, requestId, timeoutMs, true);
  }
  if (!python.trim() || !Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    return { status: "unavailable", code: "ENTRY_INVALID_CONFIG", message: "解释器或等待预算无法核实" };
  }
  if (!workspaceRoot.trim()) {
    return { status: "unavailable", code: "WORKSPACE_NOT_CONFIGURED",
      message: "尚未配置 aitest.workspaceRoot（用户数据工作空间）" };
  }
  const command = buildCommand(request, requestId);
  const prepared = panelOutcome(
    await runCli(python, workspaceRoot, { phase: "prepare", command }, timeoutMs, false, true),
    command,
    request,
  );
  if (prepared.status !== "ok") {
    return prepared;
  }
  const preparedResult = prepared.response?.["result"];
  if (!preparedResult || typeof preparedResult !== "object" || Array.isArray(preparedResult)) {
    return { status: "unavailable", code: "CORE_RESULT_UNVERIFIED", message: "核心没有返回面板确认材料" };
  }
  const prompt = (preparedResult as Record<string, unknown>)["prompt"];
  if (prompt === undefined) {
    // Idempotent recall already produced the final receipt; no new gesture is needed.
    return prepared;
  }
  if (!prompt || typeof prompt !== "object" || Array.isArray(prompt)) {
    return { status: "unavailable", code: "CORE_RESULT_UNVERIFIED", message: "面板确认材料形状无效" };
  }
  const challenge = (preparedResult as Record<string, unknown>)["challenge_id"];
  const digest = (preparedResult as Record<string, unknown>)["input_digest"];
  if (typeof challenge !== "string" || !challenge || typeof digest !== "string" || !digest) {
    return { status: "unavailable", code: "CORE_RESULT_UNVERIFIED", message: "面板确认缺少 challenge" };
  }
  const confirmed = await panelConfirm(prompt as Record<string, unknown>);
  if (!confirmed) {
    return { status: "refused", code: "USER_DECLINED", message: "用户取消了本次面板确认" };
  }
  const final = panelOutcome(
    await runCli(
      python,
      workspaceRoot,
      { phase: "confirm", command, challenge_id: challenge, input_digest: digest },
      timeoutMs,
      false,
      true,
    ),
    command,
    request,
  );
  return final;
}

/**
 * Ordinary controlled write without a native dialog.
 *
 * Only actions that are neither human-confirmation nor challenge actions may
 * use this path.  The core still verifies project/intent/revision and every
 * domain guard; the difference is only that no human gesture is required.
 */
export async function dispatchWrite(
  python: string, workspaceRoot: string, request: DispatchRequest, requestId: string,
  timeoutMs = 180_000,
): Promise<DispatchOutcome> {
  if (!PLAIN_WRITE_ACTIONS.has(request.action) || READ_ONLY_ACTIONS.has(request.action)) {
    throw new BridgeRefused("该动作需要人工确认，不能走普通写入");
  }
  if (!request.projectId || !request.intentId || request.intentId === requestId
      || !Number.isSafeInteger(request.expectedRevision)
      || (request.expectedRevision ?? -1) < 0) {
    throw new BridgeRefused("普通写入需要项目、稳定业务意图和准确的预期修订");
  }
  return dispatchCommand(python, workspaceRoot, request, requestId, timeoutMs, false, 180_000);
}

async function dispatchCommand(
  python: string, workspaceRoot: string, request: DispatchRequest, requestId: string,
  timeoutMs: number, nativeConfirmation: boolean, maxTimeoutMs?: number,
): Promise<DispatchOutcome> {
  const budget = maxTimeoutMs ?? (nativeConfirmation ? 270_000 : 60_000);
  if (!python.trim() || !Number.isFinite(timeoutMs) || timeoutMs <= 0
      || timeoutMs > budget) {
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
  } catch (error) {
    return {
      status: "unavailable",
      code: "ENTRY_SPAWN_FAILED",
      message: `无法启动已配置的本地核心入口（${spawnFailure(error, python)}）`,
    };
  }
  if (result.timedOut) {
    return { status: "unavailable", code: "ENTRY_TIMEOUT",
      message: "核心入口在限定时间内无回执；请查询原业务意图，勿自动创建新意图重试" };
  }
  if (result.spawnFailure) {
    return { status: "unavailable", code: "ENTRY_SPAWN_FAILED",
      message: `无法启动已配置的本地核心入口（${result.spawnFailure}）` };
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
