let fallbackCounter = 0;

function randomHex(length: number): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
    return cryptoApi.randomUUID().replace(/-/g, "").slice(0, length);
  }
  fallbackCounter += 1;
  const value = `${Date.now().toString(16)}${fallbackCounter.toString(16)}${Math.random()
    .toString(16)
    .slice(2)}`;
  return value.padEnd(length, "0").slice(0, length);
}

export function newId(prefix: string): string {
  return `${prefix}_${randomHex(12)}`;
}

export function isoNow(): string {
  return new Date().toISOString();
}

export function isoFrom(epochMs: number): string {
  return new Date(epochMs).toISOString();
}

/** 稳定的短指纹，用于计划契约与确认绑定；不是加密摘要。 */
export function hashText(text: string): string {
  let first = 0x811c9dc5;
  let second = 0x01000193;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    first ^= code;
    first = Math.imul(first, 0x01000193) >>> 0;
    second ^= code + index;
    second = Math.imul(second, 0x85ebca6b) >>> 0;
  }
  return `${first.toString(16).padStart(8, "0")}${second.toString(16).padStart(8, "0")}`;
}

export function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

export function durationText(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "0 秒";
  }
  if (seconds < 60) {
    return `${Math.floor(seconds)} 秒`;
  }
  return `${Math.floor(seconds / 60)} 分 ${pad2(Math.floor(seconds % 60))} 秒`;
}

export function clockText(milliseconds: number): string {
  const total = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours > 0
    ? `${pad2(hours)}:${pad2(minutes)}:${pad2(seconds)}`
    : `${pad2(minutes)}:${pad2(seconds)}`;
}
