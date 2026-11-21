import type { Project, ProjectConfig } from "./types";

export interface ProjectFormErrors {
  root?: string;
  name?: string;
}

export interface ProjectFormResult {
  ok: boolean;
  root: string;
  name: string;
  errors: ProjectFormErrors;
}

const ABSOLUTE_PATH = /^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+|\/)/;
const CONFIG_SECTIONS = new Set(["model", "services", "l1", "runners", "evidence", "link", "data"]);
const SECRET_KEY =
  /(password|passwd|token|secret|api[-_]?key|authorization|credential|private[-_]?key)/i;

export function isAbsolutePath(root: string): boolean {
  return ABSOLUTE_PATH.test(root.trim());
}

function looksWindowsPath(root: string): boolean {
  return /^[A-Za-z]:[\\/]/.test(root) || root.startsWith("\\\\");
}

export function normalizeRoot(root: string): string {
  const trimmed = root.trim().replace(/[\\/]+$/, "");
  return looksWindowsPath(trimmed) ? trimmed.toLowerCase() : trimmed;
}

export function basename(root: string): string {
  const parts = root
    .trim()
    .replace(/[\\/]+$/, "")
    .split(/[\\/]/)
    .filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

function isNested(first: string, second: string): boolean {
  const separator = looksWindowsPath(first) ? "\\" : "/";
  return first.startsWith(`${second}${separator}`);
}

export function validateProjectForm(
  input: { root: string; name: string },
  existing: Project[],
): ProjectFormResult {
  const root = input.root.trim();
  const name = input.name.trim() || basename(root);
  const errors: ProjectFormErrors = {};

  if (!root) {
    errors.root = "填写项目目录的绝对路径。";
  } else if (!isAbsolutePath(root)) {
    errors.root = "使用绝对路径，例如 D:\\work\\service 或 /home/me/service。";
  } else {
    const normalized = normalizeRoot(root);
    const duplicate = existing.find((project) => normalizeRoot(project.root) === normalized);
    if (duplicate) {
      errors.root = `这个目录已经登记为「${duplicate.name}」。`;
    } else {
      const overlap = existing.find((project) => {
        const other = normalizeRoot(project.root);
        return isNested(normalized, other) || isNested(other, normalized);
      });
      if (overlap) {
        errors.root = `与已登记项目「${overlap.name}」的目录重叠；一个目录只能登记一次。`;
      }
    }
  }

  if (!name && root) {
    errors.name = "无法从目录名推断项目名，请填写项目名。";
  } else if (name.length > 40) {
    errors.name = "项目名最多 40 个字。";
  }

  return { ok: Object.keys(errors).length === 0, root, name, errors };
}

export function validateGoal(goal: string): string | null {
  const text = goal.trim();
  if (!text) {
    return "先写一句要验证的目标。";
  }
  if (text.length > 200) {
    return "目标最多 200 个字。";
  }
  return null;
}

export function parseLineList(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function isSecretKey(key: string): boolean {
  return SECRET_KEY.test(key);
}

function findSecretKey(value: unknown, path: string): string {
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      const found = findSecretKey(value[index], `${path}[${index}]`);
      if (found) {
        return found;
      }
    }
    return "";
  }
  if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      if (isSecretKey(key)) {
        return path ? `${path}.${key}` : key;
      }
      const found = findSecretKey(child, path ? `${path}.${key}` : key);
      if (found) {
        return found;
      }
    }
  }
  return "";
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

export type ConfigValidation = { ok: true; config: ProjectConfig } | { ok: false; message: string };

export function validateProjectConfig(raw: unknown): ConfigValidation {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, message: "配置必须是 JSON 对象。" };
  }
  const value = raw as Record<string, unknown>;
  for (const [section, sectionValue] of Object.entries(value)) {
    if (!CONFIG_SECTIONS.has(section)) {
      return {
        ok: false,
        message: `未知配置段「${section}」；只允许 ${[...CONFIG_SECTIONS].join(" / ")}。`,
      };
    }
    if (sectionValue === null) {
      continue;
    }
    if (typeof sectionValue !== "object" || Array.isArray(sectionValue)) {
      return { ok: false, message: `配置段「${section}」必须是对象或 null。` };
    }
  }
  const secret = findSecretKey(value, "");
  if (secret) {
    return { ok: false, message: `配置里不能出现凭据字段（${secret}）。` };
  }
  const evidence = value.evidence;
  if (evidence && typeof evidence === "object" && !Array.isArray(evidence)) {
    for (const key of ["singleFileMb", "mediaMb"] as const) {
      const limit = (evidence as Record<string, unknown>)[key];
      if (limit !== undefined && (typeof limit !== "number" || !Number.isFinite(limit) || limit <= 0)) {
        return { ok: false, message: `证据上限 ${key} 必须是大于 0 的数字。` };
      }
    }
  }
  const services = value.services;
  if (services && typeof services === "object" && !Array.isArray(services)) {
    const service = services as Record<string, unknown>;
    const port = service.port;
    if (port !== undefined && (typeof port !== "number" || !Number.isInteger(port) || port < 1 || port > 65535)) {
      return { ok: false, message: "服务端口必须是 1–65535 的整数。" };
    }
    for (const key of ["timeoutSeconds", "timeout_seconds"] as const) {
      const timeout = service[key];
      if (timeout !== undefined && (typeof timeout !== "number" || !Number.isFinite(timeout) || timeout <= 0)) {
        return { ok: false, message: "服务超时必须是大于 0 的秒数。" };
      }
    }
    for (const key of ["healthUrl", "health", "baseUrl", "base_url"] as const) {
      const url = service[key];
      if (url === undefined || url === null || url === "") {
        continue;
      }
      if (typeof url !== "string" || !isHttpUrl(url)) {
        return { ok: false, message: `服务地址 ${key} 必须是 http(s) URL。` };
      }
    }
  }
  const l1 = value.l1;
  if (l1 && typeof l1 === "object" && !Array.isArray(l1)) {
    const section = l1 as Record<string, unknown>;
    for (const key of ["build", "entry", "smokeCommand", "smoke_command"] as const) {
      const command = section[key];
      if (command !== undefined && command !== null && typeof command !== "string") {
        return { ok: false, message: `项目级检查 ${key} 必须是字符串或 null。` };
      }
    }
  }
  return { ok: true, config: value as ProjectConfig };
}

export function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function parseJsonObject(
  text: string,
): { ok: true; value: unknown } | { ok: false; message: string } {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (error) {
    const message = error instanceof Error ? error.message : "JSON 解析失败";
    return { ok: false, message: `JSON 不合法：${message}` };
  }
}
