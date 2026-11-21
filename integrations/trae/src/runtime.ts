/** 运行环境发现与自动准备。
 *
 * 发现顺序：设置 → 工作区 .venv → `py -3` → `python`，校验版本 ≥3.11 且 `import aitest`
 * 成功，任一通过即停。发现全部失败时 `ensureRuntime` 才准备便携运行时：
 * 下载 python-build-standalone（Windows x64 install_only）到 `%APPDATA%/aitest/runtime/<版本>/`，
 * 校验发布摘要 → 用系统 `tar` 解压 → 建 venv → 用 VSIX 自带 wheel 执行 `pip install --no-index`，
 * 最后按同一份探测脚本核验。任一步失败即停并返回阶段与错误码，不自动降级、
 * 不静默改用其他解释器；已准备好的运行目录后续启动只做本地核验，不再联网。
 *
 * 面板只看到阶段/错误码与可操作提示，不回显下载 URL、绝对路径或 stderr。
 */
import { createHash } from "node:crypto";
import {
  createWriteStream,
  existsSync,
  mkdirSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { request as httpRequest } from "node:http";
import { homedir } from "node:os";
import { basename, dirname, join, sep } from "node:path";
import { request as httpsRequest } from "node:https";

import {
  cleanEnvironment,
  safeWorkingDirectory,
  spawnFailureCode,
  spawnProcess,
  stopChild,
  type SpawnFn,
} from "./process";

const PROBE_TIMEOUT_MS = 15_000;
const MAX_PROBE_OUTPUT_BYTES = 64 * 1024;

const PROBE_SCRIPT = [
  "import json, sys",
  "info = {'version': '%d.%d.%d' % sys.version_info[:3],",
  "        'major': sys.version_info[0], 'minor': sys.version_info[1]}",
  "try:",
  "    import aitest",
  "    info['aitest'] = str(getattr(aitest, '__version__', ''))",
  "except Exception as exc:",
  "    info['import_error'] = type(exc).__name__",
  "print(json.dumps(info))",
].join("\n");

export const INSTALL_HINT = [
  "未找到可用的 Python 3.11+ 解释器。",
  "插件会先尝试自动准备便携运行时；自动准备失败时，",
  "请安装 Python 3.11 或更高版本并执行 `python -m pip install aitest`，",
  "然后设置 aitest.pythonPath 指向该解释器，",
  "或把含 aitest 的 .venv 放在 aitest.workspacePath 目录下。",
].join("");

export type CandidateSource = "setting" | "workspace-venv" | "portable" | "py -3" | "python";

export interface RuntimeSettings {
  readonly pythonPath?: string;
  readonly workspacePath?: string;
}

export interface RuntimeCandidate {
  readonly source: CandidateSource;
  readonly command: string;
  readonly args: readonly string[];
  /** 文件型候选不存在时直接记失败，不启动进程。 */
  readonly missing?: boolean;
}

export interface CandidateCheck {
  readonly source: CandidateSource;
  readonly command: string;
  readonly ok: boolean;
  readonly version?: string;
  readonly packageVersion?: string;
  /** 版本 ≥3.11；用于区分“缺 Python”与“缺 aitest 包”。 */
  readonly versionOk?: boolean;
  /** 给人看的失败原因；版本过低与 import 失败分开。 */
  readonly detail?: string;
  /** 失败类别：超时/不存在/版本/缺包/输出异常，供调用方给出不同结论。 */
  readonly failure?: "timeout" | "missing" | "version" | "package" | "output" | "spawn";
}

export interface RuntimeCheck {
  readonly ok: boolean;
  readonly pythonPath: string | null;
  readonly pythonArgs: readonly string[];
  readonly version: string | null;
  readonly packageVersion: string | null;
  readonly candidates: readonly CandidateCheck[];
  /** 缺项清单，例如 ["Python 3.11+"] 或 ["aitest 包"]。 */
  readonly missing: readonly string[];
  readonly installHint: string;
}

function workspaceVenv(workspacePath: string): string {
  return process.platform === "win32"
    ? join(workspacePath, ".venv", "Scripts", "python.exe")
    : join(workspacePath, ".venv", "bin", "python");
}

export function pythonCandidates(settings: RuntimeSettings): RuntimeCandidate[] {
  const setting = (settings.pythonPath ?? "").trim();
  if (setting) {
    // 显式设置的解释器不会被自动替换：失败即停并返回原因，
    // 否则用户以为在用指定环境，实际却跑在别的解释器上。
    return [{ source: "setting", command: setting, args: [] }];
  }
  const candidates: RuntimeCandidate[] = [];
  const workspace = (settings.workspacePath ?? "").trim();
  if (workspace) {
    const path = workspaceVenv(workspace);
    candidates.push({
      source: "workspace-venv",
      command: path,
      args: [],
      ...(existsSync(path) ? {} : { missing: true }),
    });
  }
  candidates.push({ source: "py -3", command: "py", args: ["-3"] });
  candidates.push({ source: "python", command: "python", args: [] });

  const seen = new Set<string>();
  return candidates.filter((candidate) => {
    const key = `${candidate.command}\u0000${candidate.args.join("\u0000")}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

interface ProbeOutput {
  readonly timedOut: boolean;
  readonly oversized: boolean;
  readonly spawnError?: string;
  readonly code: number | null;
  readonly stdout: string;
}

function parseProbe(raw: string): Record<string, unknown> | null {
  for (const line of raw.split(/\r?\n/)) {
    const text = line.trim();
    if (!text) {
      continue;
    }
    try {
      const value: unknown = JSON.parse(text);
      if (typeof value === "object" && value !== null && !Array.isArray(value)) {
        return value as Record<string, unknown>;
      }
    } catch {
      // 探测脚本理论上只输出一行 JSON；其他内容忽略。
    }
  }
  return null;
}

function runProbe(
  spawnFn: SpawnFn,
  command: string,
  args: string[],
  timeoutMs: number = PROBE_TIMEOUT_MS,
): Promise<ProbeOutput> {
  return new Promise<ProbeOutput>((resolve) => {
    let child: ReturnType<SpawnFn>;
    try {
      child = spawnFn(command, args, {
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
        cwd: safeWorkingDirectory(),
        env: cleanEnvironment(),
      });
    } catch (error) {
      resolve({
        timedOut: false,
        oversized: false,
        spawnError: spawnFailureCode(error),
        code: null,
        stdout: "",
      });
      return;
    }

    let stdout = "";
    let bytes = 0;
    let settled = false;
    const finish = (result: ProbeOutput): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };
    const timer = setTimeout(() => {
      stopChild(child);
      finish({ timedOut: true, oversized: false, code: null, stdout });
    }, timeoutMs);

    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      bytes += Buffer.byteLength(chunk, "utf8");
      if (bytes > MAX_PROBE_OUTPUT_BYTES) {
        stopChild(child);
        finish({ timedOut: false, oversized: true, code: null, stdout: "" });
        return;
      }
      stdout += chunk;
    });
    // stderr 可能含路径或宿主诊断；既不需要也不回显。
    child.stderr?.on("data", () => undefined);
    child.on("error", (error: Error) => {
      finish({
        timedOut: false,
        oversized: false,
        spawnError: spawnFailureCode(error),
        code: null,
        stdout: "",
      });
    });
    child.on("close", (code: number | null) => {
      finish({ timedOut: false, oversized: false, code, stdout });
    });
  });
}

async function checkCandidate(
  spawnFn: SpawnFn,
  candidate: RuntimeCandidate,
  probeTimeoutMs: number = PROBE_TIMEOUT_MS,
): Promise<CandidateCheck> {
  const base = { source: candidate.source, command: candidate.command };
  if (candidate.missing) {
    return { ...base, ok: false, failure: "missing", detail: "工作区 .venv 解释器不存在" };
  }
  const probeOnce = () =>
    runProbe(spawnFn, candidate.command, [...candidate.args, "-c", PROBE_SCRIPT], probeTimeoutMs);
  let probe = await probeOnce();
  if (probe.timedOut) {
    // 首次冷启动（杀毒/磁盘）可能超过 15 秒；重试一次再判断，避免误判成“没有解释器”。
    probe = await probeOnce();
  }
  if (probe.spawnError) {
    return {
      ...base,
      ok: false,
      failure: "spawn",
      detail: `无法启动（${probe.spawnError}）`,
    };
  }
  if (probe.timedOut) {
    return {
      ...base,
      ok: false,
      failure: "timeout",
      detail: "环境检查超时（已重试）",
    };
  }
  if (probe.oversized) {
    return {
      ...base,
      ok: false,
      failure: "output",
      detail: "检查输出超出限制",
    };
  }
  const info = parseProbe(probe.stdout);
  if (info === null) {
    return {
      ...base,
      ok: false,
      failure: "output",
      detail: probe.code === 0 ? "检查输出不是合法 JSON" : `退出码 ${probe.code}`,
    };
  }
  const version = typeof info["version"] === "string" ? info["version"] : "unknown";
  const major = typeof info["major"] === "number" ? info["major"] : 0;
  const minor = typeof info["minor"] === "number" ? info["minor"] : 0;
  const packageVersion = typeof info["aitest"] === "string" ? info["aitest"] : undefined;
  if (major < 3 || (major === 3 && minor < 11)) {
    return {
      ...base,
      ok: false,
      version,
      versionOk: false,
      failure: "version",
      detail: `Python ${version} 低于 3.11`,
    };
  }
  if (major > 3 || (major === 3 && minor >= 14)) {
    return {
      ...base,
      ok: false,
      version,
      versionOk: false,
      failure: "version",
      detail: `Python ${version} 不在支持的 3.11-3.13 范围`,
    };
  }
  const importError = info["import_error"];
  if (typeof importError === "string") {
    return {
      ...base,
      ok: false,
      version,
      versionOk: true,
      failure: "package",
      detail: `Python ${version} 可用，但 import aitest 失败（${importError}）`,
    };
  }
  return {
    ...base,
    ok: true,
    version,
    versionOk: true,
    ...(packageVersion ? { packageVersion } : {}),
  };
}

/** 按数字段比较版本（3.9 < 3.13，0.9.0 < 0.10.0）；忽略 pre-release 后缀。 */
export function compareVersions(left: string, right: string): number {
  const parse = (value: string): number[] =>
    value
      .split(/[+]/)[0]
      .split(/[.-]/)
      .map((part) => {
        const match = /^\d+/.exec(part);
        return match ? Number.parseInt(match[0], 10) : 0;
      });
  const a = parse(left);
  const b = parse(right);
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const diff = (a[index] ?? 0) - (b[index] ?? 0);
    if (diff !== 0) {
      return diff > 0 ? 1 : -1;
    }
  }
  return 0;
}

/** 按固定顺序发现解释器；第一个通过全部校验的候选即停止。 */
export async function findPython(
  settings: RuntimeSettings,
  spawnFn: SpawnFn = spawnProcess,
  options: { minPackageVersion?: string | null; probeTimeoutMs?: number } = {},
): Promise<RuntimeCheck> {
  const checks: CandidateCheck[] = [];
  for (const candidate of pythonCandidates(settings)) {
    const check = await checkCandidate(spawnFn, candidate, options.probeTimeoutMs);
    checks.push(check);
    if (check.ok) {
      const minimum = (options.minPackageVersion ?? "").trim();
      if (
        minimum &&
        check.packageVersion !== undefined &&
        compareVersions(check.packageVersion, minimum) < 0
      ) {
        // 旧 CLI 缺少新命令或参数不兼容；明确报版本不符，不把旧包当成可用环境。
        checks[checks.length - 1] = {
          ...check,
          ok: false,
          failure: "package",
          detail: `aitest ${check.packageVersion} 低于扩展要求的 ${minimum}`,
        };
        continue;
      }
      return {
        ok: true,
        pythonPath: candidate.command,
        pythonArgs: candidate.args,
        version: check.version ?? null,
        packageVersion: check.packageVersion ?? null,
        candidates: checks,
        missing: [],
        installHint: INSTALL_HINT,
      };
    }
  }

  const settingOnly = (settings.pythonPath ?? "").trim() !== "";
  const versionFound = checks.some((check) => check.versionOk === true);
  const timedOut = checks.length > 0 && checks.every((check) => check.failure === "timeout");
  const missing = timedOut
    ? ["Python 3.11+（探测超时）", "aitest 包"]
    : versionFound
      ? ["aitest 包"]
      : ["Python 3.11+", "aitest 包"];
  return {
    ok: false,
    pythonPath: null,
    pythonArgs: [],
    version: null,
    packageVersion: null,
    candidates: checks,
    missing,
    installHint: settingOnly
      ? `${INSTALL_HINT} 已显式设置 aitest.pythonPath；修正该路径或清空设置后重试。`
      : INSTALL_HINT,
  };
}

/* ------------------------------------------------------------------ *
 * 自动准备便携运行时
 * ------------------------------------------------------------------ */

const RELEASE_API = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest";
const USER_AGENT = "aitest-trae";
const MAX_METADATA_BYTES = 8 * 1024 * 1024;
const MAX_ARCHIVE_BYTES = 512 * 1024 * 1024;
const MAX_REDIRECTS = 5;
const METADATA_TIMEOUT_MS = 30_000;
const EXTRACT_TIMEOUT_MS = 300_000;
const VENV_TIMEOUT_MS = 180_000;
const INSTALL_TIMEOUT_MS = 600_000;
/** 首次自动准备的硬上限；超过即停，不无限占着面板等待。 */
const PREPARE_DEADLINE_MS = 20 * 60 * 1000;
/** 运行时目录锁过期时间；崩溃遗留的锁在半小时后可以接管。 */
const RUNTIME_LOCK_STALE_MS = 30 * 60 * 1000;

/** 便携解释器候选：3.11–3.13 的 x86_64 Windows install_only 包（不含 free-threaded 变体）。 */
const ASSET_PATTERN = /^cpython-(3\.(?:11|12|13)\.\d+)\+(\d+)-x86_64-pc-windows-msvc-install_only\.tar\.gz$/;
const SHA256_HEX = /^[0-9a-f]{64}$/i;

export type RuntimeStage = "download" | "extract" | "venv" | "install" | "verify";

export interface RuntimeStageResult {
  readonly stage: RuntimeStage;
  readonly ok: boolean;
  /** 稳定错误码；成功时缺省。 */
  readonly code?: string;
  /** 给人看的原因；不含 URL、绝对路径或 stderr 原文。 */
  readonly detail?: string;
  /** 仅影响诊断、不影响最终结果时用 warning（例如缓存过期后重建）。 */
  readonly severity?: "info" | "warning" | "error";
}

export interface RuntimeFailure {
  readonly stage: RuntimeStage;
  readonly code: string;
  readonly detail: string;
  /** 本地诊断文件；仅用于人工排查，不渲染到面板。 */
  readonly diagnosticsPath: string | null;
}

export type RuntimeSource = "existing" | "portable-cache" | "portable-new";

export interface PreparedRuntime {
  readonly ok: boolean;
  readonly source: RuntimeSource;
  readonly pythonPath: string | null;
  readonly pythonArgs: readonly string[];
  readonly version: string | null;
  readonly packageVersion: string | null;
  readonly missing: readonly string[];
  readonly installHint: string;
  readonly stages: readonly RuntimeStageResult[];
  readonly failure: RuntimeFailure | null;
  /** 原始发现结果；面板的“候选失败原因”仍来自这里。 */
  readonly check: RuntimeCheck;
}

export interface PortableAsset {
  /** 形如 `3.13.7+20250818`；也用作运行目录名。 */
  readonly version: string;
  readonly url: string;
  readonly sha256: string;
}

export interface DownloadOutcome {
  readonly sha256: string;
  readonly bytes: number;
}

export type DownloadFn = (url: string, destination: string) => Promise<DownloadOutcome>;

export interface RuntimeDeps {
  /** 启动解释器/命令；测试注入假进程。 */
  readonly spawn?: SpawnFn;
  /** 解析下载源；默认读环境变量或 GitHub 最新 release。 */
  readonly resolveAsset?: () => Promise<PortableAsset>;
  /** 下载到目标文件并回报摘要；默认流式写盘并计算 SHA256。 */
  readonly download?: DownloadFn;
  readonly env?: NodeJS.ProcessEnv;
  readonly platform?: NodeJS.Platform;
  /** 运行目录根；默认 `%APPDATA%/aitest/runtime`。 */
  readonly runtimeHome?: string;
  /** VSIX 自带 wheel 目录；扩展传 `<extension>/media/runtime`。 */
  readonly wheelDir?: string;
  readonly log?: (event: Record<string, unknown>) => void;
  /** 每个阶段结束（成功或失败）时回调，便于宿主记录进度。 */
  readonly onStage?: (stage: RuntimeStageResult) => void;
  readonly now?: () => Date;
  /** 首次准备的总预算；默认 20 分钟。 */
  readonly deadlineMs?: number;
  /** 探测超时；测试可缩短，产品默认 15 秒。 */
  readonly probeTimeoutMs?: number;
}

interface ReleaseAsset {
  readonly name?: string;
  readonly browser_download_url?: string;
  readonly digest?: string | null;
}

export type AssetSelection =
  | { readonly ok: true; readonly asset: PortableAsset }
  | { readonly ok: false; readonly code: "ASSET_NOT_FOUND" | "DIGEST_MISSING"; readonly detail: string };

class RuntimeStepError extends Error {
  readonly stage: RuntimeStage;
  readonly code: string;

  constructor(stage: RuntimeStage, code: string, message: string) {
    super(message);
    this.name = "RuntimeStepError";
    this.stage = stage;
    this.code = code;
  }
}

interface CommandOutcome {
  readonly code: number | null;
  readonly timedOut: boolean;
  readonly spawnError?: string;
}

function executableName(platform: NodeJS.Platform): string {
  return platform === "win32" ? "python.exe" : "python3";
}

function venvPython(runtimeDir: string, platform: NodeJS.Platform): string {
  return platform === "win32"
    ? join(runtimeDir, "venv", "Scripts", "python.exe")
    : join(runtimeDir, "venv", "bin", "python");
}

/** 只保留文件系统安全字符，避免把 release 名直接拼进路径。 */
function safeSegment(value: string): string {
  const cleaned = value.replace(/[<>:"/\|?*\u0000-\u001f]/g, "_").replace(/^\.+/, "_").trim();
  return cleaned || "portable";
}

export function defaultRuntimeHome(
  platform: NodeJS.Platform = process.platform,
  env: NodeJS.ProcessEnv = process.env,
): string {
  if (platform === "win32") {
    const appData = (env.APPDATA ?? "").trim() || join(homedir(), "AppData", "Roaming");
    return join(appData, "aitest", "runtime");
  }
  if (platform === "darwin") {
    return join(homedir(), "Library", "Application Support", "aitest", "runtime");
  }
  const dataHome = (env.XDG_DATA_HOME ?? "").trim() || join(homedir(), ".local", "share");
  return join(dataHome, "aitest", "runtime");
}

/** 从 GitHub release 的资产列表里挑一个可校验的 Windows x64 install_only 包。 */
export function selectPortableAsset(assets: readonly ReleaseAsset[]): AssetSelection {
  const candidates: { asset: PortableAsset; minor: number; patch: number; build: number }[] = [];
  for (const item of assets) {
    const name = typeof item.name === "string" ? item.name : "";
    const url = typeof item.browser_download_url === "string" ? item.browser_download_url : "";
    const match = ASSET_PATTERN.exec(name);
    if (match === null || !url) {
      continue;
    }
    const digest = typeof item.digest === "string" ? item.digest.trim() : "";
    const sha = /^sha256:([0-9a-f]{64})$/i.exec(digest);
    if (sha === null) {
      continue;
    }
    const parts = match[1].split(".").map((text) => Number.parseInt(text, 10));
    candidates.push({
      asset: { version: `${match[1]}+${match[2]}`, url, sha256: sha[1].toLowerCase() },
      minor: Number.isFinite(parts[1]) ? parts[1] : 0,
      patch: Number.isFinite(parts[2]) ? parts[2] : 0,
      build: Number.parseInt(match[2], 10) || 0,
    });
  }
  if (candidates.length === 0) {
    // 资产存在但都没有 digest 时给出不同结论：发布摘要是安装前提，不能跳过。
    const hasAsset = assets.some((item) => typeof item.name === "string" && ASSET_PATTERN.test(item.name));
    return hasAsset
      ? { ok: false, code: "DIGEST_MISSING", detail: "发布资产未提供 sha256 摘要，拒绝安装" }
      : { ok: false, code: "ASSET_NOT_FOUND", detail: "发布中没有 x86_64 Windows install_only 包" };
  }
  candidates.sort((left, right) =>
    right.minor - left.minor || right.patch - left.patch || right.build - left.build,
  );
  return { ok: true, asset: candidates[0].asset };
}

function versionFromUrl(url: string): string {
  const name = basename(url.split("?")[0]);
  const match = /^cpython-([0-9A-Za-z.]+(?:\+[0-9]+)?)-x86_64/.exec(name);
  return match === null ? "custom" : match[1];
}

/** 默认下载源：环境变量覆盖优先，否则取 GitHub 最新 release 的 install_only 包。 */
export async function resolvePortableAsset(env: NodeJS.ProcessEnv = process.env): Promise<PortableAsset> {
  const url = (env.AITEST_PYTHON_URL ?? "").trim();
  const sha = (env.AITEST_PYTHON_SHA256 ?? "").trim();
  if (url) {
    if (!SHA256_HEX.test(sha)) {
      throw new RuntimeStepError(
        "verify",
        "SHA256_MISSING",
        "已设置 AITEST_PYTHON_URL，但 AITEST_PYTHON_SHA256 缺失或不是 64 位十六进制",
      );
    }
    return { version: versionFromUrl(url), url, sha256: sha.toLowerCase() };
  }
  if (sha) {
    throw new RuntimeStepError("verify", "URL_MISSING", "已设置 AITEST_PYTHON_SHA256，但缺少 AITEST_PYTHON_URL");
  }
  const release = await getJson(RELEASE_API);
  const assets = Array.isArray(release["assets"]) ? (release["assets"] as ReleaseAsset[]) : [];
  const selection = selectPortableAsset(assets);
  if (!selection.ok) {
    throw new RuntimeStepError(
      selection.code === "DIGEST_MISSING" ? "verify" : "download",
      selection.code,
      selection.detail,
    );
  }
  return selection.asset;
}

type HttpClientResponse = import("node:http").IncomingMessage;

/** 发起 GET 并跟随重定向；超时/网络错误统一折叠成下载阶段错误。 */
function openStream(url: string, redirects = 0): Promise<HttpClientResponse> {
  return new Promise<HttpClientResponse>((resolve, reject) => {
    let target: URL;
    try {
      target = new URL(url);
    } catch {
      reject(new RuntimeStepError("download", "INVALID_URL", "下载地址不合法"));
      return;
    }
    const request = target.protocol === "http:" ? httpRequest : httpsRequest;
    const req = request(
      target,
      {
        method: "GET",
        headers: { "user-agent": USER_AGENT, accept: "application/vnd.github+json, */*" },
        timeout: METADATA_TIMEOUT_MS,
      },
      (response) => {
        const status = response.statusCode ?? 0;
        const location = response.headers.location;
        if (status >= 300 && status < 400 && typeof location === "string" && redirects < MAX_REDIRECTS) {
          response.resume();
          openStream(new URL(location, target).toString(), redirects + 1).then(resolve, reject);
          return;
        }
        resolve(response);
      },
    );
    req.on("timeout", () => req.destroy());
    req.on("error", () => reject(new RuntimeStepError("download", "NETWORK_FAILED", "无法连接下载地址")));
    req.end();
  });
}

async function getJson(url: string): Promise<Record<string, unknown>> {
  const response = await openStream(url);
  const status = response.statusCode ?? 0;
  if (status !== 200) {
    response.resume();
    throw new RuntimeStepError("download", `HTTP_${status}`, `获取发布信息失败（HTTP ${status}）`);
  }
  const chunks: Buffer[] = [];
  let bytes = 0;
  await new Promise<void>((resolve, reject) => {
    response.on("data", (chunk: Buffer) => {
      bytes += chunk.length;
      if (bytes > MAX_METADATA_BYTES) {
        response.destroy();
        reject(new RuntimeStepError("download", "METADATA_TOO_LARGE", "发布信息超出大小限制"));
        return;
      }
      chunks.push(chunk);
    });
    response.on("end", () => resolve());
    response.on("error", () =>
      reject(new RuntimeStepError("download", "NETWORK_FAILED", "读取发布信息时网络中断")),
    );
  });
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new RuntimeStepError("download", "INVALID_METADATA", "发布信息不是合法 JSON");
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new RuntimeStepError("download", "INVALID_METADATA", "发布信息不是 JSON 对象");
  }
  return parsed as Record<string, unknown>;
}

/** 默认下载器：跟随重定向流式写盘并同时计算 SHA256；不把压缩包读进内存。 */
export async function downloadArchive(url: string, destination: string): Promise<DownloadOutcome> {
  const response = await openStream(url);
  const status = response.statusCode ?? 0;
  if (status !== 200) {
    response.resume();
    throw new RuntimeStepError("download", `HTTP_${status}`, `下载运行时失败（HTTP ${status}）`);
  }
  const declared = Number.parseInt(String(response.headers["content-length"] ?? ""), 10);
  const expected = Number.isFinite(declared) && declared > 0 ? declared : null;
  const hash = createHash("sha256");
  const stream = createWriteStream(destination);
  let bytes = 0;
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    const fail = (error: Error): void => {
      if (settled) {
        return;
      }
      settled = true;
      stream.close();
      rmSync(destination, { force: true });
      reject(error);
    };
    response.on("data", (chunk: Buffer) => {
      bytes += chunk.length;
      if (bytes > MAX_ARCHIVE_BYTES) {
        response.destroy();
        fail(new RuntimeStepError("download", "ARCHIVE_TOO_LARGE", "运行时压缩包超出大小限制"));
        return;
      }
      hash.update(chunk);
      // 尊重写盘背压：慢磁盘时暂停响应流，避免把整包堆进内存。
      if (!stream.write(chunk)) {
        response.pause();
      }
    });
    stream.on("drain", () => {
      response.resume();
    });
    response.on("end", () => {
      if (expected !== null && bytes !== expected) {
        fail(
          new RuntimeStepError(
            "download",
            "DOWNLOAD_INCOMPLETE",
            `下载字节数与 Content-Length 不一致（${bytes}/${expected}）`,
          ),
        );
        return;
      }
      stream.end(() => {
        if (!settled) {
          settled = true;
          resolve();
        }
      });
    });
    response.on("error", () =>
      fail(
        expected !== null && bytes !== expected
          ? new RuntimeStepError(
              "download",
              "DOWNLOAD_INCOMPLETE",
              `下载中断（${bytes}/${expected} 字节）`,
            )
          : new RuntimeStepError("download", "NETWORK_FAILED", "下载中断"),
      ),
    );
    stream.on("error", (error: Error) => {
      response.destroy();
      fail(new RuntimeStepError("download", "WRITE_FAILED", `写入临时文件失败（${error.name}）`));
    });
  });
  return { sha256: hash.digest("hex"), bytes };
}

interface CommandOutcome {
  readonly code: number | null;
  readonly timedOut: boolean;
  readonly spawnError?: string;
}

function runCommand(
  spawnFn: SpawnFn,
  command: string,
  args: string[],
  timeoutMs: number,
): Promise<CommandOutcome> {
  return new Promise<CommandOutcome>((resolve) => {
    let child: ReturnType<SpawnFn>;
    try {
      child = spawnFn(command, args, {
        stdio: ["ignore", "ignore", "ignore"],
        windowsHide: true,
        cwd: safeWorkingDirectory(),
        env: cleanEnvironment(),
      });
    } catch (error) {
      resolve({ code: null, timedOut: false, spawnError: spawnFailureCode(error) });
      return;
    }
    let settled = false;
    const finish = (outcome: CommandOutcome): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      resolve(outcome);
    };
    const timer = setTimeout(() => {
      stopChild(child);
      finish({ code: null, timedOut: true });
    }, timeoutMs);
    child.on("error", (error: Error) => {
      finish({ code: null, timedOut: false, spawnError: spawnFailureCode(error) });
    });
    child.on("close", (code: number | null) => {
      finish({ code, timedOut: false });
    });
  });
}

/** 已准备好的便携运行时：按目录名语义版本倒序取最新一个存在的 venv 解释器。 */
export function findPreparedPython(
  runtimeHome: string,
  platform: NodeJS.Platform = process.platform,
): string | null {
  let entries: import("node:fs").Dirent[];
  try {
    entries = readdirSync(runtimeHome, { withFileTypes: true });
  } catch {
    return null;
  }
  const names = entries
    .filter((entry) => entry.isDirectory())
    // 点开头是锁、隔离区与暂存目录，不是可用运行时。
    .map((entry) => entry.name)
    .filter((name) => !name.startsWith("."))
    .sort((left, right) => compareVersions(right, left));
  for (const name of names) {
    const candidate = venvPython(join(runtimeHome, name), platform);
    if (existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

/** install_only 包顶层通常是 `python/`；也接受直接把解释器放在解压根的情况。 */
function locatePortablePython(runtimeDir: string, platform: NodeJS.Platform): string | null {
  const exe = executableName(platform);
  const direct =
    platform === "win32"
      ? [join(runtimeDir, "python", exe), join(runtimeDir, exe)]
      : [
          join(runtimeDir, "python", "bin", "python3"),
          join(runtimeDir, "python", "bin", "python"),
          join(runtimeDir, "bin", "python3"),
        ];
  for (const candidate of direct) {
    if (existsSync(candidate)) {
      return candidate;
    }
  }
  try {
    for (const entry of readdirSync(runtimeDir, { withFileTypes: true })) {
      if (!entry.isDirectory()) {
        continue;
      }
      const nested =
        platform === "win32"
          ? join(runtimeDir, entry.name, exe)
          : join(runtimeDir, entry.name, "bin", "python3");
      if (existsSync(nested)) {
        return nested;
      }
    }
  } catch {
    // 目录不可读时由调用方按“解压后缺解释器”处理。
  }
  return null;
}

/** VSIX 内 `media/runtime/*.whl`：只接受 aitest 自身的 wheel，不把依赖当安装目标。 */
export function findBundledWheel(wheelDir: string): string | null {
  let names: string[];
  try {
    names = readdirSync(wheelDir).filter((name) => name.toLowerCase().endsWith(".whl"));
  } catch {
    return null;
  }
  const own = names
    .filter((name) => {
      // wheel 文件名首段是规范化发行名：ai_test_component-0.5.0-...
      const distribution = name.split("-")[0].toLowerCase().replace(/[._]/g, "-");
      return distribution === "ai-test-component" || distribution === "aitest";
    })
    .sort((left, right) => compareVersions(wheelVersionOf(right) ?? "", wheelVersionOf(left) ?? ""));
  return own.length > 0 ? join(wheelDir, own[0]) : null;
}

function wheelVersionOf(name: string): string | null {
  const match = /^[^-]+-(\d+\.\d+(?:\.\d+)?[^-]*)-/.exec(name);
  return match === null ? null : match[1];
}

/** 从 wheel 文件名取版本；用于判断已准备的运行目录是否落后于插件自带 wheel。 */
export function bundledWheelVersion(wheelDir: string): string | null {
  const wheel = findBundledWheel(wheelDir);
  if (wheel === null) {
    return null;
  }
  return wheelVersionOf(basename(wheel));
}

function failureCode(error: unknown, fallback: string): string {
  if (error instanceof RuntimeStepError) {
    return error.code;
  }
  const code = (error as NodeJS.ErrnoException | null)?.code;
  return typeof code === "string" ? code : fallback;
}

function failureDetail(error: unknown, fallback: string): string {
  return error instanceof RuntimeStepError ? error.message : fallback;
}

export async function preparePortableRuntime(
  check: RuntimeCheck,
  deps: RuntimeDeps = {},
): Promise<PreparedRuntime> {
  const spawnFn = deps.spawn ?? spawnProcess;
  const env = deps.env ?? process.env;
  const platform = deps.platform ?? process.platform;
  const runtimeHome = deps.runtimeHome ?? defaultRuntimeHome(platform, env);
  const wheelDir = deps.wheelDir ?? join(__dirname, "..", "media", "runtime");
  const log = deps.log ?? (() => undefined);
  const now = deps.now ?? (() => new Date());
  const stages: RuntimeStageResult[] = [];
  const deadline = Date.now() + (deps.deadlineMs ?? PREPARE_DEADLINE_MS);

  const record = (stage: RuntimeStageResult): void => {
    stages.push(stage);
    deps.onStage?.(stage);
    log({
      phase: "runtime-stage",
      stage: stage.stage,
      ok: stage.ok,
      code: stage.code ?? "",
      severity: stage.severity ?? (stage.ok ? "info" : "error"),
    });
  };

  const fail = (stage: RuntimeStage, code: string, detail: string): PreparedRuntime => {
    record({ stage, ok: false, code, detail, severity: "error" });
    const diagnosticsPath = writeDiagnostics(runtimeHome, now(), stage, code, detail);
    return {
      check,
      ok: false,
      source: "portable-new",
      pythonPath: null,
      pythonArgs: [],
      version: null,
      packageVersion: null,
      missing: check.missing,
      installHint: check.installHint,
      stages,
      failure: { stage, code, detail, diagnosticsPath },
    };
  };

  const ready = (
    pythonPath: string,
    source: RuntimeSource,
    version: string | null,
    packageVersion: string | null,
  ): PreparedRuntime => ({
    check,
    ok: true,
    source,
    pythonPath,
    pythonArgs: [],
    version,
    packageVersion,
    missing: [],
    installHint: check.installHint,
    stages,
    failure: null,
  });

  const remaining = (): number => deadline - Date.now();
  const stageTimeout = (budget: number): number => Math.max(1_000, Math.min(budget, remaining()));

  // 1. 已经准备过的运行目录：只做本地核验，不联网。
  const prepared = findPreparedPython(runtimeHome, platform);
  let preparedDir: string | null = null;
  if (prepared !== null) {
    const segments = prepared.split(/[\\/]/);
    const venvIndex = segments.lastIndexOf("venv");
    // `<runtimeDir>/venv/{Scripts|bin}/python(.exe)` → `<runtimeDir>`
    preparedDir =
      venvIndex > 0 ? segments.slice(0, venvIndex).join(sep) : dirname(dirname(prepared));
    const verified = await checkCandidate(spawnFn, { source: "portable", command: prepared, args: [] });
    const expected = bundledWheelVersion(wheelDir);
    const current =
      verified.ok &&
      (expected === null || verified.packageVersion === undefined || verified.packageVersion === expected);
    if (current) {
      record({ stage: "verify", ok: true });
      clearDiagnostics(runtimeHome);
      return ready(prepared, "portable-cache", verified.version ?? null, verified.packageVersion ?? null);
    }
    // 过期/损坏只是重建的原因，不是业务错误：不弹错误对话框，等重建结果再说。
    record({
      stage: "verify",
      ok: false,
      severity: "warning",
      code: verified.ok ? "CACHED_RUNTIME_OUTDATED" : "CACHED_RUNTIME_INVALID",
      detail: verified.ok
        ? `已安装 aitest ${verified.packageVersion ?? "unknown"}，插件自带 ${expected ?? "unknown"}`
        : (verified.detail ?? "运行目录不可用"),
    });
  }

  // 2. 自带 wheel 必须在下载前确认，避免下载完才发现安装材料缺失。
  const wheel = findBundledWheel(wheelDir);
  if (wheel === null) {
    return fail("install", "WHEEL_MISSING", "VSIX 内未找到 aitest wheel（media/runtime/*.whl）");
  }

  // 3. 跨窗口/跨命令互斥：同一运行目录只允许一个准备进程。
  const lock = acquireRuntimeLock(runtimeHome);
  if (lock === null) {
    return fail("download", "RUNTIME_LOCKED", "另一个窗口正在准备运行时，请等待其完成");
  }

  let staging: string | null = null;
  let attemptedRuntimeDir: string | null = null;
  let committed = false;
  try {
    if (remaining() <= 0) {
      return fail("download", "PREPARE_TIMEOUT", "准备运行时的总时间预算已用完");
    }

    // 4. 解析下载源（含摘要）。缺摘要直接拒绝，不降级到未校验的包。
    let asset: PortableAsset;
    try {
      asset = await (deps.resolveAsset ?? (() => resolvePortableAsset(env)))();
    } catch (error) {
      return fail(
        error instanceof RuntimeStepError ? error.stage : "download",
        failureCode(error, "ASSET_RESOLVE_FAILED"),
        failureDetail(error, "无法确定便携运行时下载源"),
      );
    }

    const runtimeDir = join(runtimeHome, safeSegment(asset.version));
    attemptedRuntimeDir = runtimeDir;
    // 每次准备都在隔离目录完成；成功后才原子替换，失败不动已有运行时。
    staging = join(
      runtimeHome,
      `.staging-${safeSegment(asset.version)}-${process.pid}-${Date.now()}`,
    );
    const archive = join(staging, `python-${safeSegment(asset.version)}.tar.gz`);
    const partial = `${archive}.part`;
    try {
      mkdirSync(staging, { recursive: true });
    } catch (error) {
      return fail("download", failureCode(error, "RUNTIME_DIR_FAILED"), "无法创建运行时目录");
    }

    // 5. 下载 + 摘要校验。摘要不符立即删除临时文件并停止。
    let downloaded: DownloadOutcome;
    try {
      log({ phase: "runtime-download", host: safeHost(asset.url), version: asset.version });
      downloaded = await (deps.download ?? downloadArchive)(asset.url, partial);
    } catch (error) {
      return fail("download", failureCode(error, "DOWNLOAD_FAILED"), failureDetail(error, "下载运行时失败"));
    }
    if (downloaded.bytes <= 0) {
      return fail("download", "EMPTY_DOWNLOAD", "下载结果为空");
    }
    if (!SHA256_HEX.test(downloaded.sha256) || downloaded.sha256.toLowerCase() !== asset.sha256.toLowerCase()) {
      return fail("verify", "SHA256_MISMATCH", "下载文件的 SHA256 与发布摘要不一致");
    }
    try {
      renameSync(partial, archive);
    } catch (error) {
      return fail("download", failureCode(error, "ARCHIVE_RENAME_FAILED"), "无法保存下载的运行时压缩包");
    }
    record({ stage: "download", ok: true });
    record({ stage: "verify", ok: true });

    if (remaining() <= 0) {
      return fail("extract", "PREPARE_TIMEOUT", "准备运行时的总时间预算已用完");
    }

    // 6. 解压：Windows 10+ 自带 bsdtar；不为 tar.gz 另找 PowerShell 兼容路径。
    try {
      const extracted = await runCommand(
        spawnFn,
        "tar",
        ["-xf", archive, "-C", staging],
        stageTimeout(EXTRACT_TIMEOUT_MS),
      );
      if (extracted.spawnError) {
        return fail("extract", "TAR_MISSING", "系统 tar 不可用（需要 Windows 10+ 自带的 tar）");
      }
      if (extracted.timedOut) {
        return fail("extract", "EXTRACT_TIMEOUT", "解压超时");
      }
      if (extracted.code !== 0) {
        return fail("extract", "EXTRACT_FAILED", `解压失败（tar 退出码 ${extracted.code ?? "unknown"}）`);
      }
    } catch (error) {
      return fail("extract", failureCode(error, "EXTRACT_FAILED"), failureDetail(error, "解压失败"));
    }
    const basePython = locatePortablePython(staging, platform);
    if (basePython === null) {
      return fail("extract", "PYTHON_MISSING", "解压后未找到 python 解释器");
    }
    record({ stage: "extract", ok: true });
    rmSync(archive, { force: true }); // 解压成功即回收临时压缩包。
    log({ phase: "runtime-extract", version: asset.version });

    // 7. 在便携运行时内建 venv；失败不退回系统解释器。
    const targetPython = venvPython(staging, platform);
    try {
      const created = await runCommand(
        spawnFn,
        basePython,
        ["-m", "venv", join(staging, "venv")],
        stageTimeout(VENV_TIMEOUT_MS),
      );
      if (created.spawnError) {
        return fail("venv", `VENV_${created.spawnError}`, "无法启动便携解释器创建 venv");
      }
      if (created.timedOut) {
        return fail("venv", "VENV_TIMEOUT", "创建 venv 超时");
      }
      if (created.code !== 0) {
        return fail("venv", "VENV_FAILED", `创建 venv 失败（退出码 ${created.code ?? "unknown"}）`);
      }
    } catch (error) {
      return fail("venv", failureCode(error, "VENV_FAILED"), failureDetail(error, "创建 venv 失败"));
    }
    if (!existsSync(targetPython)) {
      return fail("venv", "VENV_PYTHON_MISSING", "venv 创建完成但未找到解释器");
    }
    record({ stage: "venv", ok: true });

    // 8. 离线安装自带 wheel：--no-index 只允许本地 wheel 目录，绝不回退到 PyPI。
    try {
      const installed = await runCommand(
        spawnFn,
        targetPython,
        [
          "-m",
          "pip",
          "install",
          "--no-index",
          "--find-links",
          wheelDir,
          "--disable-pip-version-check",
          wheel,
        ],
        stageTimeout(INSTALL_TIMEOUT_MS),
      );
      if (installed.spawnError) {
        return fail("install", `PIP_${installed.spawnError}`, "无法启动 venv 内的 pip");
      }
      if (installed.timedOut) {
        return fail("install", "INSTALL_TIMEOUT", "离线安装超时");
      }
      if (installed.code !== 0) {
        return fail("install", "INSTALL_FAILED", `pip install 失败（退出码 ${installed.code ?? "unknown"}）`);
      }
    } catch (error) {
      return fail("install", failureCode(error, "INSTALL_FAILED"), failureDetail(error, "离线安装失败"));
    }
    record({ stage: "install", ok: true });

    // 9. 用与发现阶段相同的探测脚本核验，避免“装完但不可用”被当成成功。
    const verified = await checkCandidate(spawnFn, { source: "portable", command: targetPython, args: [] });
    if (!verified.ok) {
      return fail("verify", "RUNTIME_UNUSABLE", verified.detail ?? "便携运行时安装后不可用");
    }
    record({ stage: "verify", ok: true });

    // 10. 原子切换：旧目录保留到新目录就位；切换失败回滚。
    try {
      commitRuntime(staging, runtimeDir);
    } catch (error) {
      return fail("install", failureCode(error, "RUNTIME_SWITCH_FAILED"), "无法切换到新运行时目录");
    }
    staging = null;
    committed = true;
    clearDiagnostics(runtimeHome);
    log({ phase: "runtime-ready", version: verified.version ?? "", source: "portable-new" });

    return ready(
      venvPython(runtimeDir, platform),
      "portable-new",
      verified.version ?? null,
      verified.packageVersion ?? null,
    );
  } finally {
    if (staging !== null) {
      // 失败路径回收暂存目录（含压缩包），不留下半成品或 500MB 临时材料。
      rmSync(staging, { recursive: true, force: true });
    }
    if (preparedDir !== null && !committed && attemptedRuntimeDir !== null && preparedDir === attemptedRuntimeDir) {
      // 同一版本目录已损坏/过期且重建失败：隔离它，避免下次仍命中同一个坏目录。
      quarantineRuntime(preparedDir, log);
    }
    releaseRuntimeLock(lock);
  }
}

/** 删除上次失败的诊断文件；只有成功准备后才清理，失败时保留供排查。 */
function clearDiagnostics(runtimeHome: string): void {
  rmSync(join(runtimeHome, "prepare-failure.json"), { force: true });
}

/** 用 mkdir 做跨进程锁；遗留锁超过半小时视为可接管。 */
function acquireRuntimeLock(runtimeHome: string): string | null {
  const lock = join(runtimeHome, ".runtime.lock");
  const create = (): boolean => {
    try {
      mkdirSync(lock);
      writeFileSync(join(lock, "owner"), String(process.pid), "utf8");
      return true;
    } catch {
      return false;
    }
  };
  try {
    mkdirSync(runtimeHome, { recursive: true });
  } catch {
    return null;
  }
  if (create()) {
    return lock;
  }
  try {
    if (Date.now() - statSync(lock).mtimeMs > RUNTIME_LOCK_STALE_MS) {
      rmSync(lock, { recursive: true, force: true });
      if (create()) {
        return lock;
      }
    }
  } catch {
    // 锁在检查与接管之间被释放：按占用处理。
  }
  return null;
}

function releaseRuntimeLock(lock: string): void {
  rmSync(lock, { recursive: true, force: true });
}

/** 把暂存目录换到最终版本目录；失败时恢复旧目录。 */
function commitRuntime(staging: string, runtimeDir: string): void {
  const previous = `${runtimeDir}.previous`;
  rmSync(previous, { recursive: true, force: true });
  const hadPrevious = existsSync(runtimeDir);
  if (hadPrevious) {
    renameSync(runtimeDir, previous);
  }
  try {
    renameSync(staging, runtimeDir);
  } catch (error) {
    if (hadPrevious) {
      renameSync(previous, runtimeDir);
    }
    throw error;
  }
  rmSync(previous, { recursive: true, force: true });
}

/** 坏目录改名隔离；findPreparedPython 会跳过点开头目录。 */
function quarantineRuntime(runtimeDir: string, log: (event: Record<string, unknown>) => void): void {
  try {
    if (!existsSync(runtimeDir)) {
      return;
    }
    const target = join(dirname(runtimeDir), `.broken-${basename(runtimeDir)}-${Date.now()}`);
    renameSync(runtimeDir, target);
    log({ phase: "runtime-quarantine", name: basename(runtimeDir) });
  } catch {
    // 隔离失败不影响本次结论；下次仍会重建。
  }
}

/** 先发现已合格解释器；都没有才准备便携运行时。成功路径不会触发下载。 */
export async function ensureRuntime(
  settings: RuntimeSettings,
  deps: RuntimeDeps = {},
): Promise<PreparedRuntime> {
  const spawnFn = deps.spawn ?? spawnProcess;
  const platform = deps.platform ?? process.platform;
  const env = deps.env ?? process.env;
  const runtimeHome = deps.runtimeHome ?? defaultRuntimeHome(platform, env);
  const wheelDir = deps.wheelDir ?? join(__dirname, "..", "media", "runtime");
  // 扩展自带 wheel 的版本就是本次要求的最低 CLI 版本：旧包缺新命令/参数。
  const minPackageVersion = bundledWheelVersion(wheelDir);
  const check = await findPython(settings, spawnFn, {
    minPackageVersion,
    ...(deps.probeTimeoutMs ? { probeTimeoutMs: deps.probeTimeoutMs } : {}),
  });
  if (check.ok && check.pythonPath !== null) {
    // 上次失败留下的诊断会误导排查；找到可用解释器后清理。
    clearDiagnostics(runtimeHome);
    return {
      check,
      ok: true,
      source: "existing",
      pythonPath: check.pythonPath,
      pythonArgs: check.pythonArgs,
      version: check.version,
      packageVersion: check.packageVersion,
      missing: [],
      installHint: check.installHint,
      stages: [],
      failure: null,
    };
  }
  const timedOut =
    check.candidates.length > 0 && check.candidates.every((item) => item.failure === "timeout");
  if (timedOut) {
    // 探测超时说明“不知道有没有解释器”，不是“没有”；此时下载运行时可能白装一套。
    return {
      check,
      ok: false,
      source: "existing",
      pythonPath: null,
      pythonArgs: [],
      version: null,
      packageVersion: null,
      missing: check.missing,
      installHint: check.installHint,
      stages: [],
      failure: {
        stage: "verify",
        code: "PYTHON_PROBE_TIMEOUT",
        detail: "解释器探测超时（已重试）；请检查磁盘/杀毒/负载后重试，或显式设置 aitest.pythonPath",
        diagnosticsPath: null,
      },
    };
  }
  return preparePortableRuntime(check, {
    ...deps,
    spawn: spawnFn,
    runtimeHome,
    wheelDir,
    platform,
    env,
  });
}

function safeHost(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return "unknown";
  }
}

/** 失败留痕：只记阶段、错误码与摘要文本；不写下载 URL、绝对路径或 stderr。 */
function writeDiagnostics(
  runtimeHome: string,
  at: Date,
  stage: RuntimeStage,
  code: string,
  detail: string,
): string | null {
  try {
    mkdirSync(runtimeHome, { recursive: true });
    const path = join(runtimeHome, "prepare-failure.json");
    const content = JSON.stringify({ at: at.toISOString(), stage, code, detail }, null, 2) + "\n";
    const temp = `${path}.tmp`;
    rmSync(temp, { force: true });
    writeFileSync(temp, content, "utf8");
    rmSync(path, { force: true });
    renameSync(temp, path);
    return path;
  } catch {
    return null;
  }
}


