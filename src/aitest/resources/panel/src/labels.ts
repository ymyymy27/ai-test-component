/** Chinese presentation labels for core vocabulary. Raw values stay in receipts. */

import type { Tone } from "./types";

const LABELS: Record<string, string> = {
  passed: "通过",
  failed: "失败",
  incomplete: "未完成",
  not_applicable: "不适用",
  full: "完整验证",
  quick: "快速检查",
  on_demand: "所选检查",
  planned: "按计划执行",
  stepwise: "逐步执行",
  running: "运行中",
  paused: "已暂停",
  pause_requested: "暂停请求中",
  cancel_requested: "取消请求中",
  cancelling: "取消中",
  recovering: "恢复中",
  completed: "已完成",
  succeeded: "成功",
  started: "已开始",
  pending: "待处理",
  blocked: "受阻",
  cancelled: "已取消",
  execution_error: "执行异常",
  ready: "准备就绪",
  verified: "已核验",
  unverified: "待核验",
  matched: "匹配",
  mismatched: "不匹配",
  no_result: "无结果",
  pending_verification: "待核验",
  not_started: "未开始",
  invalidated: "已过期",
  missing: "未填",
  present_unconfirmed: "未确认",
  confirmed: "已确认",
  pre_run: "运行前",
  runtime: "运行时",
  post_run: "运行后",
  L1: "L1 · 静态检查",
  L2: "L2 · 模块检查",
  L3: "L3 · 业务链路",
  open: "开放",
  closed: "已关闭",
  fixed: "已修复",
  published: "已发布",
  draft: "草稿",
  not_configured: "未配置",
  degraded: "降级",
  covered: "已覆盖",
  uncovered: "未覆盖",
  deferred: "已推迟",
  partial: "部分覆盖",
  unknown: "未知",
  READY: "就绪",
  NOT_READY: "未就绪",
  required: "必测",
  optional: "选用",
  command: "命令",
  manual_evidence: "人工证据",
  agent: "智能体",
  http: "HTTP",
  python: "Python",
  "pure-function-stdout": "纯函数输出",
  critical: "严重",
  major: "重要",
  minor: "次要",
  trivial: "轻微",
  markdown_summary: "Markdown 摘要",
  evidence_bundle: "证据包",
};

export function label(value: unknown): string {
  if (value === null || value === undefined || value === "") return "待核实";
  return LABELS[String(value)] ?? String(value);
}

export function toneForState(value: unknown): Tone {
  switch (String(value)) {
    case "passed":
    case "completed":
    case "matched":
    case "verified":
    case "confirmed":
    case "closed":
    case "full":
      return "success";
    case "failed":
    case "mismatched":
    case "execution_error":
    case "invalidated":
      return "danger";
    case "blocked":
    case "pending":
    case "pending_verification":
    case "present_unconfirmed":
    case "incomplete":
    case "paused":
    case "pause_requested":
    case "cancel_requested":
    case "cancelling":
    case "recovering":
      return "warning";
    case "running":
      return "info";
    default:
      return "neutral";
  }
}

const GAP_TEXT: Record<string, string> = {
  source_identity_mismatch: "源码与登记身份不一致",
  no_valid_execution_basis: "没有可用的执行依据",
  source_identity_unverified: "源码身份尚未核实",
  template_required_below_floor: "必测项低于模板要求",
  full_required_scope_missing: "完整验证缺少必测范围",
  required_case_unresolved: "必测用例尚未得出结论",
  required_case_pending_verification: "必测用例待核验",
  required_case_unverified: "必测用例本轮没有取得有效验证",
  outdated_basis: "断言依据已过期",
  required_evidence_invalid: "必需证据缺失、损坏或无效",
  environment_evidence_incomplete: "环境证据不完整",
  critical_path_missing: "缺少关键链路覆盖",
  critical_unknown: "关键链路结果未知",
  critical_mock: "关键链路使用了模拟",
  full_skipped_scope: "完整验证跳过了部分范围",
  assertion_basis_unconfirmed: "断言依据尚未确认",
  noncritical_unknown: "非关键项结果未知",
  noncritical_verification_gap: "非关键项缺少验证",
  unclassified_gap: "未分类的缺口",
};

const GAP_HINT: Record<string, string> = {
  source_identity_mismatch: "重新分析源码，确认被测内容与登记一致后再执行。",
  no_valid_execution_basis: "先确认计划与断言依据，再开始执行。",
  source_identity_unverified: "重新分析源码，让核心登记当前来源。",
  template_required_below_floor: "按模板补齐必测项后再发布计划。",
  full_required_scope_missing: "检查必测范围是否被排除，恢复后再执行。",
  required_case_unresolved: "先执行该用例并记录实际结果。",
  required_case_pending_verification: "打开该步骤，执行“核验这次执行结果”。",
  required_case_unverified: "核验通过后再保存一次报告。",
  outdated_basis: "上游结果已变化，重新执行受影响的用例。",
  required_evidence_invalid: "重新执行以产生新的证据，再核验。",
  environment_evidence_incomplete: "在环境说明中补充信息，然后重新准备本次运行。",
  critical_path_missing: "补上关键链路的执行记录。",
  critical_unknown: "关键链路结果未知，需要重新执行确认。",
  critical_mock: "关键链路使用了模拟，改用真实执行。",
  full_skipped_scope: "把被跳过的范围重新纳入本轮。",
  assertion_basis_unconfirmed: "先确认断言依据，再进行核验。",
  noncritical_unknown: "重新执行该用例以取得明确结果。",
  noncritical_verification_gap: "补充该用例的核验。",
  unclassified_gap: "按缺口说明补齐对应材料。",
};

export function gapText(code: unknown, fallback?: unknown): string {
  if (typeof code === "string" && GAP_TEXT[code]) return GAP_TEXT[code];
  return typeof fallback === "string" && fallback.trim() ? fallback : "缺口";
}

export function gapHint(code: unknown): string {
  return typeof code === "string" ? (GAP_HINT[code] ?? "") : "";
}

export const EVIDENCE_OMITTED: Record<string, string> = {
  redaction_unsafe: "这条证据未通过脱敏校验，只提供元数据，不返回正文。",
  object_unavailable: "对象仓不可用，暂时读取不到正文。",
  body_too_large: "正文超过 4MB，不在界面上直接展示，可导出证据包查看。",
  object_unreadable: "正文对象读取失败，可能已损坏或被占用。",
  object_corrupted: "正文对象与登记摘要不一致，已停止展示。",
  binary_content: "这条证据是二进制材料，不支持文本展示。",
};

export function omittedReason(reason: unknown): string {
  return typeof reason === "string" ? (EVIDENCE_OMITTED[reason] ?? "正文不可用。") : "正文不可用。";
}

export function stageTitle(stage: unknown): string {
  switch (String(stage)) {
    case "pre_run":
      return "准备下一步运行";
    case "runtime":
      return "本次测试进行中";
    case "post_run":
      return "本次测试已有结果";
    default:
      return "本次测试";
  }
}

export function stageDescription(stage: unknown): string {
  switch (String(stage)) {
    case "pre_run":
      return "计划与环境已经保存。开始执行后，面板会按步骤显示进度和需要你处理的事项。";
    case "runtime":
      return "核心正在按计划推进。自动步骤继续运行，需要你确认或补证的步骤会在这里提示。";
    case "post_run":
      return "执行已经结束。查看报告、证据和未完成范围；需要补证时回到工作台。";
    default:
      return "从项目准备开始，按提示完成计划、执行、核验和报告。";
  }
}

export function shortId(value: unknown, length = 8): string {
  const text = typeof value === "string" ? value : "";
  if (text.length <= length * 2 + 1) return text || "—";
  return `${text.slice(0, length)}…${text.slice(-4)}`;
}

export function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function asText(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

export function asRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.map(asRecord).filter((item): item is Record<string, unknown> => item !== undefined)
    : [];
}

export function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function formatTime(value: unknown): string {
  if (typeof value !== "string" || !value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}
