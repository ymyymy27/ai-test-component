import type { EvidenceRef } from "@/domain/types";

export type ReportDetail =
  | { kind: "command"; title: string; command: string; root: string }
  | { kind: "evidence"; evidence: EvidenceRef };

export function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

export function coverageLabel(kind: string | null | undefined): string {
  return (
    {
      direct: "直接",
      file: "文件级",
      shared: "共用节点",
      manual: "人工",
      unverified: "未验证",
    }[kind ?? ""] ?? "未记录"
  );
}

export function coverageClass(kind: string | null | undefined): string {
  if (kind === "direct") {
    return "is-pass";
  }
  if (kind === "manual") {
    return "is-info";
  }
  if (!kind) {
    return "is-neutral";
  }
  return "is-warn";
}

export function coverageDescription(kind: string | null | undefined): string {
  return (
    {
      direct: "直接定位到具体测试节点，可作为该项的通过证据",
      file: "只定位到测试文件，证据不足以单独证明该项",
      shared: "与其他验收项共用测试节点",
      manual: "由人工记录结果与证据",
      unverified: "预期或证据未核实，不能据此判定通过",
    }[kind ?? ""] ?? "这一版没有记录覆盖方式"
  );
}

export function commandText(method: string): string {
  const value = (method || "").trim();
  if (value.startsWith("pytest:")) {
    return `python -m pytest ${value.slice("pytest:".length).trim()}`.trim();
  }
  return value.replace(/^command:\s*/, "").trim();
}

export function parseUncovered(value: string): { id: string; text: string } {
  const separator = value.search(/[：:]/);
  if (separator < 0) {
    return { id: "", text: value };
  }
  const id = value.slice(0, separator).trim();
  const text = value.slice(separator + 1).trim();
  return id.startsWith("ai_") ? { id, text } : { id: "", text: value };
}

export function shortMethod(method: string): string {
  const value = (method || "").trim();
  if (!value || value === "manual" || value.startsWith("manual:")) {
    return "人工验证";
  }
  if (value.startsWith("script:")) {
    return `脚本 ${value.slice("script:".length).trim()}`;
  }
  if (value.startsWith("http:")) {
    return "HTTP 请求";
  }
  const command = value.replace(/^(?:command|pytest):\s*/, "");
  const pythonNode = command.match(/([^\\/]+\.py)::([A-Za-z0-9_:]+)/);
  if (pythonNode) {
    return `${pythonNode[1]}::${pythonNode[2]}`;
  }
  const playwrightNode = command.match(/([^\\/]+\.spec\.ts)\s+--grep\s+"([^"]+)"/);
  if (playwrightNode) {
    return `${playwrightNode[1]} · ${playwrightNode[2]}`;
  }
  const bunTest = command.match(/([^\\/]+\.test\.ts)\b/);
  if (bunTest) {
    return bunTest[1];
  }
  const pythonFile = command.match(/([^\\/]+\.py)\b/);
  if (pythonFile) {
    return pythonFile[1];
  }
  const compact = command.split("&&").pop()?.trim() || command;
  return compact.length > 62 ? `${compact.slice(0, 61)}…` : compact;
}

export function evidenceLabel(evidence: EvidenceRef): string {
  const name = evidence.name.split(/[\\/]/).pop() || evidence.name;
  if (name.length > 30 || name.startsWith("run_")) {
    return "查看证据";
  }
  return name;
}

export function bytesText(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} 字节`;
  }
  return `${(bytes / 1024).toFixed(1)} KB`;
}
