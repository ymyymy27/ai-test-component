import type {
  AnalysisRunStatus,
  CaseResult,
  IssueDisposition,
  IssueStatus,
  Layer,
  MockKind,
  RunStatus,
  Severity,
  TemplateDisposition,
  Verdict,
} from "./types";

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  running: "运行中",
  completed: "已完成",
  cancelled: "已取消",
  interrupted: "已中断",
  execution_error: "执行出错",
};

export const VERDICT_LABEL: Record<Verdict, string> = {
  passed: "通过",
  failed: "没通过",
  incomplete: "没测完",
  not_applicable: "这次不用测",
};

export const RESULT_LABEL: Record<CaseResult, string> = {
  passed: "通过",
  failed: "失败",
  blocked: "阻塞",
  error: "出错",
  not_run: "未运行",
  manual_pending: "待人工",
};

export const ISSUE_STATUS_LABEL: Record<IssueStatus, string> = {
  pending: "待确认",
  in_progress: "处理中",
  retest: "待复测",
  closed: "已关闭",
};

export const DISPOSITION_LABEL: Record<IssueDisposition, string> = {
  fixed: "已修复",
  non_defect: "非缺陷",
  duplicate: "重复",
  deferred: "延期",
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  P0: "P0 · 阻断",
  P1: "P1 · 影响主流程",
  P2: "P2 · 影响局部",
  P3: "P3 · 待观察",
};

export const LAYER_LABEL: Record<Layer, string> = {
  L1: "项目级检查",
  L2: "自动检查",
  L3: "人工检查",
};

export const LINK_STATE_LABEL: Record<"passed" | "failed" | "blocked" | "gap" | "not_run", string> =
  {
    passed: "通过",
    failed: "失败",
    blocked: "阻塞",
    gap: "缺口",
    not_run: "未运行",
  };

export const MOCK_LABEL: Record<MockKind, string> = {
  real: "真实",
  mock: "模拟",
  mixed: "混合",
  unknown: "无法判断",
};

export const TEMPLATE_DISPOSITION_LABEL: Record<TemplateDisposition, string> = {
  applicable: "适用",
  not_applicable: "不适用",
  unsupported: "不支持",
  unknown: "无法判断",
};

export const ANALYSIS_REASON_LABEL: Record<Exclude<AnalysisRunStatus, "generated">, string> = {
  not_generated: "未生成",
  invalid: "输出不合格",
};

export const ANALYSIS_REASON_TEXT: Record<string, string> = {
  no_failures: "本次没有失败用例，无需分析。",
  model_missing: "没有可用模型，分析未生成；测试与报告不受影响。",
  model_invalid: "模型输出缺少必要字段，分析未采用。",
  model_error: "模型调用异常，分析未生成。",
  "": "",
};

export function statusText(result: CaseResult): string {
  return RESULT_LABEL[result];
}

export function verdictText(verdict: Verdict): string {
  return VERDICT_LABEL[verdict];
}
