/** 运行环境发现：按设置 → 工作区 .venv → py -3 → python 顺序找可用解释器。
 *
 * 只做发现与校验（版本 ≥3.11、`import aitest`），不下载、不创建 venv、不改用户
 * 配置。找不到时如实返回每个候选的失败原因与安装提示。
 *
 * TODO（一期后续）：自动下载便携 Python 到 `%APPDATA%/aitest/runtime/<version>/`，
 * 创建 venv，并用 VSIX 自带 wheel 执行 `pip install --no-index`。当前明确提示，
 * 不自动下载，也不静默改用其他解释器。
 */
import { existsSync } from "node:fs";
import { join } from "node:path";

import { cleanEnvironment, spawnFailureCode, spawnProcess, type SpawnFn } from "./process";

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
  "请安装 Python 3.11 或更高版本，并执行 `python -m pip install aitest`；",
  "然后设置 aitest.pythonPath 指向该解释器，",
  "或把含 aitest 的 .venv 放在 aitest.workspacePath 目录下。",
  "插件当前不会自动下载运行时。",
].join("");

export type CandidateSource = "setting" | "workspace-venv" | "py -3" | "python";

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
  const candidates: RuntimeCandidate[] = [];
  const setting = (settings.pythonPath ?? "").trim();
  if (setting) {
    candidates.push({ source: "setting", command: setting, args: [] });
  }
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

function runProbe(spawnFn: SpawnFn, command: string, args: string[]): Promise<ProbeOutput> {
  return new Promise<ProbeOutput>((resolve) => {
    let child: ReturnType<SpawnFn>;
    try {
      child = spawnFn(command, args, {
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
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
      child.kill();
      finish({ timedOut: true, oversized: false, code: null, stdout });
    }, PROBE_TIMEOUT_MS);

    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      bytes += Buffer.byteLength(chunk, "utf8");
      if (bytes > MAX_PROBE_OUTPUT_BYTES) {
        child.kill();
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
): Promise<CandidateCheck> {
  const base = { source: candidate.source, command: candidate.command };
  if (candidate.missing) {
    return { ...base, ok: false, detail: "工作区 .venv 解释器不存在" };
  }
  const probe = await runProbe(spawnFn, candidate.command, [
    ...candidate.args,
    "-c",
    PROBE_SCRIPT,
  ]);
  if (probe.spawnError) {
    return { ...base, ok: false, detail: `无法启动（${probe.spawnError}）` };
  }
  if (probe.timedOut) {
    return { ...base, ok: false, detail: "环境检查超时" };
  }
  if (probe.oversized) {
    return { ...base, ok: false, detail: "检查输出超出限制" };
  }
  const info = parseProbe(probe.stdout);
  if (info === null) {
    return {
      ...base,
      ok: false,
      detail: probe.code === 0 ? "检查输出不是合法 JSON" : `退出码 ${probe.code}`,
    };
  }
  const version = typeof info["version"] === "string" ? info["version"] : "unknown";
  const major = typeof info["major"] === "number" ? info["major"] : 0;
  const minor = typeof info["minor"] === "number" ? info["minor"] : 0;
  const packageVersion = typeof info["aitest"] === "string" ? info["aitest"] : undefined;
  if (major < 3 || (major === 3 && minor < 11)) {
    return { ...base, ok: false, version, versionOk: false, detail: `Python ${version} 低于 3.11` };
  }
  const importError = info["import_error"];
  if (typeof importError === "string") {
    return {
      ...base,
      ok: false,
      version,
      versionOk: true,
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

/** 按固定顺序发现解释器；第一个通过全部校验的候选即停止。 */
export async function findPython(
  settings: RuntimeSettings,
  spawnFn: SpawnFn = spawnProcess,
): Promise<RuntimeCheck> {
  const checks: CandidateCheck[] = [];
  for (const candidate of pythonCandidates(settings)) {
    const check = await checkCandidate(spawnFn, candidate);
    checks.push(check);
    if (check.ok) {
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

  const versionFound = checks.some((check) => check.versionOk === true);
  return {
    ok: false,
    pythonPath: null,
    pythonArgs: [],
    version: null,
    packageVersion: null,
    candidates: checks,
    missing: versionFound ? ["aitest 包"] : ["Python 3.11+", "aitest 包"],
    installHint: INSTALL_HINT,
  };
}
