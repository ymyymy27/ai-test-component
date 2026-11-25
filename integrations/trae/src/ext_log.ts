/** 扩展诊断日志、异常截断与凭据脱敏辅助模块。 */
import { appendFileSync, renameSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const PANEL_TITLE = "AI 辅助测试";
export const PANEL_ERROR_MAX_CHARS = 2000;
const EXT_LOG = join(tmpdir(), "aitest-extension.log");
const EXT_LOG_MAX_BYTES = 1024 * 1024;
let logWrites = 0;

export function truncate(value: unknown, limit = PANEL_ERROR_MAX_CHARS): string {
  const text = typeof value === "string" ? value : String(value ?? "");
  return text.length <= limit ? text : `${text.slice(0, limit)}…（已截断）`;
}

const _LOG_SECRET_PATTERNS: readonly RegExp[] = [
  /\bsk-[A-Za-z0-9_-]{12,}\b/g,
  /\bgh[pousr]_[A-Za-z0-9]{16,}\b/g,
  /\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi,
  /\bBasic\s+[A-Za-z0-9+/=]{8,}/gi,
  /\b[A-Za-z][A-Za-z0-9+.-]*:\/\/[^\s:@/]+:[^\s@/]+@/g,
];

export function redactLogText(text: string): string {
  let out = text;
  for (const pattern of _LOG_SECRET_PATTERNS) {
    out = out.replace(pattern, "***");
  }
  return out;
}

export function sanitizeLogValue(value: unknown, key = ""): unknown {
  if (
    key &&
    key !== "keys" &&
    /(?:key|token|secret|password|passwd|passphrase|credential|authorization|cookie)/i.test(key)
  ) {
    return value === "" || value === null || value === undefined ? value : "***";
  }
  if (typeof value === "string") {
    return redactLogText(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeLogValue(item));
  }
  if (typeof value === "object" && value !== null) {
    const cleaned: Record<string, unknown> = {};
    for (const [entryKey, entryValue] of Object.entries(value)) {
      cleaned[entryKey] = sanitizeLogValue(entryValue, entryKey);
    }
    return cleaned;
  }
  return value;
}

export function logExt(event: Record<string, unknown>): void {
  try {
    logWrites += 1;
    if (logWrites % 50 === 0) {
      try {
        if (statSync(EXT_LOG).size > EXT_LOG_MAX_BYTES) {
          renameSync(EXT_LOG, `${EXT_LOG}.1`);
        }
      } catch {
        // 文件不存在或无法轮转时继续写新日志。
      }
    }
    const sanitized = sanitizeLogValue(event) as Record<string, unknown>;
    appendFileSync(
      EXT_LOG,
      JSON.stringify({ at: new Date().toISOString(), ...sanitized }) + "\n",
      "utf8",
    );
  } catch {
    // 诊断日志不能影响业务调用。
  }
}
