/** 中文文案：枚举只在这里翻译，界面不出现英文状态值。 */
import type {
  CaseResult,
  IssueStatus,
  Layer,
  RunStatus,
  Severity,
  Verdict,
} from "@/api/types";

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  running: "运行中",
  completed: "已完成",
  cancelled: "已取消",
  interrupted: "已中断",
  execution_error: "执行异常",
};

export const RUN_STATUS_BADGE: Record<
  RunStatus,
  "running" | "passed" | "failed" | "incomplete" | "neutral"
> = {
  running: "running",
  completed: "passed",
  cancelled: "neutral",
  interrupted: "incomplete",
  execution_error: "failed",
};

export const VERDICT_LABEL: Record<Verdict, string> = {
  passed: "通过",
  failed: "失败",
  incomplete: "未完成",
  not_applicable: "不适用",
};

export const VERDICT_TONE: Record<Verdict, "passed" | "failed" | "incomplete" | "neutral"> = {
  passed: "passed",
  failed: "failed",
  incomplete: "incomplete",
  not_applicable: "neutral",
};

export const VERDICT_SENTENCE: Record<Verdict, string> = {
  passed: "所选范围内全部适用用例有效通过。",
  failed: "所选范围内存在有效失败，先看失败用例与证据。",
  incomplete: "没有有效失败，但仍有阻塞、未执行或待人工记录的用例。",
  not_applicable: "所选范围没有适用的检查。",
};

export const CASE_RESULT_LABEL: Record<CaseResult, string> = {
  passed: "通过",
  failed: "失败",
  blocked: "阻塞",
  error: "执行异常",
  not_run: "未执行",
  manual_pending: "待人工记录",
};

export const CASE_RESULT_TONE: Record<
  CaseResult,
  "passed" | "failed" | "incomplete" | "neutral"
> = {
  passed: "passed",
  failed: "failed",
  blocked: "incomplete",
  error: "incomplete",
  not_run: "neutral",
  manual_pending: "incomplete",
};

export const LAYER_LABEL: Record<Layer, string> = {
  L1: "源码可运行",
  L2: "函数与接口",
  L3: "用户场景",
};

export const ISSUE_STATUS_LABEL: Record<IssueStatus, string> = {
  pending: "待确认",
  in_progress: "处理中",
  retest: "待复测",
  closed: "已关闭",
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  P0: "最高",
  P1: "高",
  P2: "中",
  P3: "低",
};

const SOURCE_LABEL: Record<string, string> = {
  git: "近期提交",
  issue: "未关闭问题",
  issues: "未关闭问题",
  history: "历史任务",
  module: "模块依赖",
  modules: "模块依赖",
  docs: "项目文档",
  readme: "README",
  ai: "模型建议",
};

export function sourceLabel(source?: string): string {
  if (!source) {
    return "";
  }
  return SOURCE_LABEL[source.toLowerCase()] ?? source;
}

export const IMPORTANCE_LABEL: Record<string, string> = {
  high: "重要",
  medium: "一般",
  low: "次要",
};

export const CONFIDENCE_LABEL: Record<string, string> = {
  high: "把握较大",
  medium: "把握一般",
  low: "把握有限",
};

export function methodLabel(method?: string): string {
  if (!method) {
    return "未说明";
  }
  if (method.startsWith("script:") || method.startsWith("command:")) {
    return "命令执行";
  }
  if (method.startsWith("http:")) {
    return "接口请求";
  }
  if (method.startsWith("playwright:")) {
    return "浏览器脚本";
  }
  if (method.startsWith("manual:")) {
    return "人工核对";
  }
  if (method.startsWith("sql:")) {
    return "数据库查询";
  }
  if (method.startsWith("junit:")) {
    return "JUnit 报告";
  }
  if (method.startsWith("pytest:")) {
    return "pytest";
  }
  return "既定方式";
}

export function dependencyLabel(dep?: "build" | "service" | null): string {
  if (dep === "build") {
    return "依赖构建检查";
  }
  if (dep === "service") {
    return "依赖服务启动";
  }
  return "";
}

export function formatDateTime(value?: string | null): string {
  if (!value) {
    return "时间未记录";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatFullDateTime(value?: string | null): string {
  if (!value) {
    return "时间未记录";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}
