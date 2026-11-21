import {
  ANALYSIS_REASON_TEXT,
  ISSUE_STATUS_LABEL,
  LAYER_LABEL,
  LINK_STATE_LABEL,
  statusText,
  VERDICT_LABEL,
} from "@/domain/labels";
import { normalizeReport } from "@/domain/derive";
import type { Project, Report, Task } from "@/domain/types";
import { sha256HexBytes } from "./sha256";

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

/** Markdown 表格单元格：转义分隔符并把换行压成空格，避免用户文本破坏表结构。 */
function mdCell(value: string): string {
  return (value || "—").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

export function reportMarkdown(project: Project, task: Task, rawReport: Report): string {
  // 旧报告可能缺少后来新增的字段；导出前统一兜底，避免直接读 undefined。
  const report = normalizeReport(rawReport);
  const lines: string[] = [];
  lines.push(`# 测试报告 · ${project.name}`);
  lines.push("");
  lines.push(`- 任务：${task.title}`);
  lines.push(`- 运行：${report.source.runTime}`);
  lines.push(`- 生成：${report.generatedAt} · 第 ${report.version} 版`);
  lines.push(`- 结论：${VERDICT_LABEL[report.verdict]}`);
  lines.push("- 来源：本地模拟运行（未连接 CLI）");
  lines.push("");
  lines.push("## 结论");
  lines.push("");
  lines.push(report.conclusion);
  lines.push("");
  lines.push("## 范围与覆盖");
  lines.push("");
  lines.push(
    `本次 ${report.coverage.selected} 项 / 共 ${report.coverage.applicable} 项；已覆盖 ${report.coverage.covered} 项；未纳入 ${report.coverage.unselected} 项。`,
  );
  lines.push("");
  lines.push("## 验收项");
  lines.push("");
  lines.push("| 编号 | 验收项 | 最差结果 | 用例数 |");
  lines.push("| --- | --- | --- | --- |");
  report.itemDetails
    .filter((item) => item.selected)
    .forEach((item) => {
      lines.push(
        `| ${mdCell(item.no)} | ${mdCell(item.text)} | ${statusText(item.outcome)} | ${item.caseIds.length} |`,
      );
    });
  lines.push("");
  if (report.uncoveredItems.length > 0) {
    lines.push("### 未纳入本次");
    lines.push("");
    report.uncoveredItems.forEach((item) => lines.push(`- ${item}`));
    lines.push("");
  }
  if (report.unverifiedItems.length > 0) {
    lines.push("### 未核实预期");
    lines.push("");
    report.unverifiedItems.forEach((item) => lines.push(`- ${item}（执行通过也按阻塞计）`));
    lines.push("");
  }
  if (report.planWarnings.length > 0) {
    lines.push("### 计划警告");
    lines.push("");
    report.planWarnings.forEach((item) => lines.push(`- ${item}`));
    lines.push("");
  }
  lines.push("## 项目级检查");
  lines.push("");
  report.l1.forEach((check) => {
    const reason = check.reason ? ` · 原因：${check.reason}` : "";
    lines.push(`- ${check.name} · ${statusText(check.result)} · ${check.method}${reason}`);
  });
  lines.push("");
  lines.push("## 分层计数");
  lines.push("");
  (Object.keys(report.layers) as Array<"L1" | "L2" | "L3">).forEach((layer) => {
    const entry = report.layers[layer];
    lines.push(
      `- ${LAYER_LABEL[layer]}：共 ${entry.total} 条；通过 ${entry.passed}；失败 ${entry.failed}；未完成 ${entry.incomplete}`,
    );
  });
  lines.push("");
  lines.push("## 用例明细");
  lines.push("");
  report.caseDetails.forEach((testCase) => {
    lines.push(`- ${testCase.title} · ${testCase.method} · ${statusText(testCase.result)}`);
    lines.push(`  - 预期：${testCase.expected}`);
    lines.push(`  - 实际：${testCase.actual || "未记录"}`);
    if (testCase.evidence.length > 0) {
      lines.push(`  - 证据：${testCase.evidence.map((entry) => entry.name).join("、")}`);
    }
  });
  lines.push("");
  lines.push("## 问题");
  lines.push("");
  if (report.issues.length > 0) {
    report.issues.forEach((issueId) => {
      const issue = project.issues.find((entry) => entry.id === issueId);
      if (issue) {
        lines.push(`- ${issue.severity} · ${issue.title} · ${ISSUE_STATUS_LABEL[issue.status]}`);
      }
    });
  } else {
    lines.push("本次没有归并问题。");
  }
  lines.push("");
  lines.push("## 模拟数据判断");
  lines.push("");
  const mockEvaluated =
    report.mock.real + report.mock.mock + report.mock.mixed + report.mock.unknown > 0;
  const realChain = report.mock.realChain ? "是" : mockEvaluated ? "否" : "未验证";
  lines.push(
    `真实 ${report.mock.real} · 模拟 ${report.mock.mock} · 混合 ${report.mock.mixed} · 无法判断 ${report.mock.unknown}。真实链路：${realChain}。${report.mock.note}`,
  );
  lines.push("");
  lines.push("## 失败分析");
  lines.push("");
  if (report.analysis.status === "generated") {
    lines.push(`- 事实：${report.analysis.observed}`);
    if (report.analysis.evidenceRefs.length > 0) {
      lines.push(`- 证据：${report.analysis.evidenceRefs.join("、")}`);
    }
    report.analysis.candidateCauses.forEach((cause) => {
      lines.push(`- 候选原因（把握：${cause.confidence}）：${cause.cause}`);
    });
    report.analysis.unverified.forEach((item) => lines.push(`- 未验证：${item}`));
    report.analysis.nextSteps.forEach((item) => lines.push(`- 下一步：${item}`));
  } else {
    lines.push(ANALYSIS_REASON_TEXT[report.analysis.reason] || "分析未生成。");
  }
  lines.push("");
  if (report.link) {
    lines.push("## 调用链");
    lines.push("");
    report.link.nodes.forEach((node) => {
      lines.push(`- ${node.name} · ${LINK_STATE_LABEL[node.state]} · ${node.detail}`);
    });
    if (report.link.location.known) {
      lines.push(`- 定位：${report.link.location.path}:${report.link.location.line}`);
    }
    if (report.link.gaps.length > 0) {
      lines.push(`- 缺口：${report.link.gaps.map((gap) => gap.reason).join("；")}`);
    }
    lines.push("");
  }
  if (report.templates.length > 0) {
    lines.push("## 检查模板");
    lines.push("");
    report.templates.forEach((template) => {
      lines.push(
        `- ${template.name} v${template.version}：适用 ${template.summary.applicable} · 不适用 ${template.summary.not_applicable} · 不支持 ${template.summary.unsupported} · 无法判断 ${template.summary.unknown}`,
      );
    });
    lines.push("");
  }
  lines.push("## 来源与环境");
  lines.push("");
  lines.push(`- 计划：${report.source.planLabel}`);
  lines.push(`- 规则版本：${report.source.rulesLabel}`);
  lines.push(`- 源码提交：${report.source.commit}`);
  lines.push(`- 模型：${report.source.model}`);
  lines.push(`- GitHub：${report.source.github}`);
  lines.push("");
  lines.push("## 验收意见");
  lines.push("");
  lines.push(
    report.opinion
      ? `${report.opinion.by} · ${formatTime(report.opinion.at)}\n\n${report.opinion.text}`
      : "未填写验收意见。",
  );
  lines.push("");
  lines.push("---");
  lines.push("本报告由本地运行记录派生；本地模拟运行不代表真实 CLI 验收结论。");
  return lines.join("\n");
}

export async function sha256Hex(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  const buffer = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
  return sha256HexBytes(buffer);
}

export async function exportMetrics(
  filename: string,
  markdown: string,
): Promise<{ filename: string; bytes: number; lines: number; sha256: string; markdown: string }> {
  const bytes = new TextEncoder().encode(markdown).length;
  const lines = markdown.split("\n").length;
  return {
    filename,
    bytes,
    lines,
    sha256: await sha256Hex(markdown),
    markdown,
  };
}

export function downloadBytes(filename: string, bytes: Uint8Array, mime: string): boolean {
  if (typeof document === "undefined" || typeof URL.createObjectURL !== "function") {
    return false;
  }
  const buffer = bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
  const blob = new Blob([buffer], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  return true;
}

export function downloadText(filename: string, text: string, mime: string): boolean {
  if (typeof document === "undefined" || typeof URL.createObjectURL !== "function") {
    return false;
  }
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  return true;
}
