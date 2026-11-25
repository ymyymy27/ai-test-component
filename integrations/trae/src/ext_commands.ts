/** 扩展命令白名单、路径打开校验与稳定请求键生成。 */
import { realpathSync, statSync } from "node:fs";
import { extname, isAbsolute, relative, resolve } from "node:path";

/** 面板可调用的命令白名单；新增命令先在这里显式放行。 */
export const PANEL_COMMANDS: ReadonlySet<string> = new Set([
  "doctor",
  "project.add",
  "project.config",
  "project.list",
  "project.map",
  "project.snapshot",
  "project.map.refresh",
  "project.show",
  "project.update",
  "task.add",
  "task.list",
  "task.show",
  "task.suggest",
  "module.suggest",
  "module.add",
  "module.update",
  "task.update",
  "task.delete",
  "delivery.draft",
  "delivery.save",
  "delivery.show",
  "template.list",
  "template.apply",
  "plan.generate",
  "plan.show",
  "plan.update",
  "run.start",
  "run.quick",
  "run.status",
  "run.list",
  "run.cancel",
  "run.manual",
  "run.repair",
  "analysis.run",
  "evidence.list",
  "evidence.add",
  "rules.save",
  "rules.list",
  "rules.show",
  "rules.import",
  "rules.export",
  "rules.bind",
  "github.read",
  "link.import",
  "link.assert",
  "issue.add",
  "issue.list",
  "issue.show",
  "issue.update",
  "issue.close",
  "report.generate",
  "report.show",
  "report.list",
  "report.export",
  "report.opinion",
  "backup.create",
  "backup.restore",
]);

/** 只有用户显式确认的命令才接受 `--confirm`。 */
export const CONFIRMABLE_COMMANDS: ReadonlySet<string> = new Set([
  "run.start",
  "run.quick",
  "run.repair",
]);

export const COMMAND_TIMEOUT_MS: Readonly<Record<string, number>> = {
  "run.start": 60_000,
  "run.quick": 60_000,
  "run.repair": 630_000,
  "project.map.refresh": 210_000,
  "plan.generate": 210_000,
  "task.add": 210_000,
  "task.suggest": 60_000,
  "module.suggest": 60_000,
  "delivery.draft": 210_000,
  "template.list": 30_000,
  "template.apply": 120_000,
  "analysis.run": 120_000,
  "report.generate": 330_000,
  "report.export": 60_000,
  "rules.import": 30_000,
  "rules.export": 30_000,
  "github.read": 150_000,
  "link.import": 150_000,
  "project.config": 30_000,
  "backup.create": 630_000,
  "backup.restore": 630_000,
  doctor: 30_000,
};

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 可执行/脚本类文件不允许用系统默认程序直接打开。 */
export const BLOCKED_OPEN_EXTENSIONS: ReadonlySet<string> = new Set([
  ".exe",
  ".bat",
  ".cmd",
  ".com",
  ".scr",
  ".msi",
  ".ps1",
  ".vbs",
  ".vbe",
  ".js",
  ".jse",
  ".wsf",
  ".wsh",
  ".jar",
  ".lnk",
  ".reg",
  ".hta",
]);

export interface OpenPathDenied {
  readonly ok: false;
  readonly code: string;
  readonly message: string;
  readonly nextStep: string;
}

/**
 * 证据路径只允许落在用户数据工作空间内，且必须是普通文件。
 * 拒绝 UNC、相对路径、越界路径与可执行文件，避免把任意宿主路径交给系统打开。
 */
export function resolveOpenablePath(
  raw: string,
  workspacePath: string,
): { ok: true; path: string } | OpenPathDenied {
  const text = raw.trim();
  if (!text) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "没有提供要打开的文件路径",
      nextStep: "刷新证据列表后重试",
    };
  }
  if (/^[\\/]{2}/.test(text) || /^\\\\[.?]\\/.test(text)) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "不允许打开网络共享（UNC）路径",
      nextStep: "把证据导出到工作空间后再打开",
    };
  }
  if (!isAbsolute(text)) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "只允许打开工作空间内的绝对路径",
      nextStep: "刷新证据列表后重试",
    };
  }
  const workspace = (workspacePath ?? "").trim();
  if (!workspace) {
    return {
      ok: false,
      code: "WORKSPACE_NOT_CONFIGURED",
      message: "尚未设置 aitest.workspacePath，无法校验证据路径",
      nextStep: "在设置中填写用户数据工作空间",
    };
  }
  let target: string;
  try {
    target = realpathSync(resolve(text));
  } catch {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "文件不存在或无法读取",
      nextStep: "刷新证据列表后重试",
    };
  }
  let root: string;
  try {
    root = realpathSync(resolve(workspace));
  } catch {
    root = resolve(workspace);
  }
  const rel = relative(root, target);
  const outside =
    rel === "" ||
    rel.startsWith("..") ||
    isAbsolute(rel) ||
    (process.platform === "win32" && relative(root.toLowerCase(), target.toLowerCase()).startsWith(".."));
  if (outside) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "证据路径不在用户数据工作空间内",
      nextStep: "只打开工作空间内的证据或报告",
    };
  }
  let stat;
  try {
    stat = statSync(target);
  } catch {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "文件不存在或无法读取",
      nextStep: "刷新证据列表后重试",
    };
  }
  if (!stat.isFile()) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "只能打开文件，不能打开目录",
      nextStep: "选择具体证据文件",
    };
  }
  const extension = extname(target).toLowerCase();
  if (BLOCKED_OPEN_EXTENSIONS.has(extension)) {
    return {
      ok: false,
      code: "INVALID_PATH",
      message: "不允许用系统程序直接打开可执行或脚本文件",
      nextStep: "改用文本方式查看证据内容",
    };
  }
  return { ok: true, path: target };
}

/** 稳定的请求键：去重与取消都按“命令 + 参数 + 是否确认”匹配。 */
export function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

export function callKey(
  command: string,
  payload: Record<string, unknown>,
  confirm: boolean,
): string {
  return `${command}\u0000${confirm ? "1" : "0"}\u0000${stableStringify(payload)}`;
}
