import type { ConfidenceTier, Issue, RunCase, RunItem } from "@/domain/types";

export function confidenceTierLabel(tier?: ConfidenceTier): string {
  if (tier === "confirmed") return "铁证缺陷";
  if (tier === "suspected") return "疑似问题";
  if (tier === "blocked") return "环境阻塞";
  return "";
}

/** 根据用例方式、pytest 节点或源码文件提炼最短单行最小复现命令。 */
export function buildMinimalReproCommand(input: {
  method?: string;
  testNodes?: string[];
  projectRoot?: string;
  file?: string;
}): string {
  const nodes = (input.testNodes ?? []).filter((n) => typeof n === "string" && n.trim());
  if (nodes.length > 0) {
    return `python -m pytest ${nodes.join(" ")} -v --tb=short`;
  }
  const raw = (input.method ?? "").trim();
  if (raw) {
    const cleaned = raw
      .replace(/^(?:pytest|command|cmd|api|playwright)\s*:\s*/i, "")
      .trim();
    if (/^python\b|^pytest\b|^uv\b|^npm\b|^pnpm\b|^npx\b|^bun\b|^node\b|^curl\b/i.test(cleaned)) {
      return cleaned;
    }
    if (cleaned.endsWith(".py")) {
      return `python -m pytest ${cleaned} -v --tb=short`;
    }
    if (cleaned.includes(".ts") || cleaned.includes(".js")) {
      return `npx playwright test ${cleaned}`;
    }
    if (cleaned) {
      return cleaned;
    }
  }
  if (input.file?.trim()) {
    if (input.file.endsWith(".py")) {
      return `python -m pytest -k "${input.file}" -v --tb=short`;
    }
    return `# 定位文件：${input.file}`;
  }
  return "";
}

export interface CaseFixPromptInput {
  projectName?: string;
  projectRoot?: string;
  taskTitle?: string;
  item?: Pick<RunItem, "no" | "text">;
  testCase: Pick<
    RunCase,
    | "id"
    | "title"
    | "method"
    | "expected"
    | "actual"
    | "message"
    | "errorType"
    | "stackKey"
    | "evidence"
    | "confidence"
    | "confidenceReason"
    | "phaseFailed"
    | "serviceLogIssues"
    | "judgement"
    | "file"
    | "line"
  > & { testNodes?: string[] };
}

export interface IssueFixPromptInput {
  projectName?: string;
  projectRoot?: string;
  issue: Pick<
    Issue,
    | "id"
    | "title"
    | "severity"
    | "status"
    | "itemNo"
    | "itemText"
    | "expected"
    | "actual"
    | "steps"
    | "signature"
    | "evidenceRefs"
    | "confidence"
    | "confidenceReason"
    | "file"
    | "line"
  >;
  reproCommand?: string;
}

/**
 * 构造可一键粘贴给 Trae AI 的用例失败“AI 修复任务包”（仅引用已脱敏的展示字段）。
 */
export function buildCaseFixPrompt(input: CaseFixPromptInput): string {
  const { testCase, item } = input;
  const lines: string[] = [
    `请帮我定位并修复以下测试失败问题（先阅读对应源码与验证方式确定根因，再修复代码并补充或确认回归测试通过）：`,
    "",
  ];
  if (input.projectName || input.projectRoot) {
    lines.push(
      `- **项目**：${input.projectName || "当前项目"}${input.projectRoot ? `（根目录 \`${input.projectRoot}\`）` : ""}`,
    );
  }
  if (input.taskTitle) {
    lines.push(`- **任务**：${input.taskTitle}`);
  }
  if (item) {
    lines.push(`- **对应验收项**：${item.no ? `${item.no} ` : ""}${item.text}`);
  }
  lines.push(`- **失败用例**：${testCase.title || testCase.id}（ID: \`${testCase.id}\`）`);
  const tierLabel = confidenceTierLabel(testCase.confidence);
  if (tierLabel) {
    lines.push(
      `- **可信度判定**：${tierLabel}${testCase.confidenceReason ? `（${testCase.confidenceReason}）` : ""}`,
    );
  }
  if (testCase.phaseFailed) {
    lines.push(`- **失败阶段**：\`${testCase.phaseFailed}\``);
  }
  if (testCase.file) {
    lines.push(
      `- **源码定位**：\`${testCase.file}${testCase.line ? `:${testCase.line}` : ""}\``,
    );
  }
  if (testCase.method) {
    lines.push(`- **验证方式**：\`${testCase.method}\``);
  }
  if (testCase.testNodes?.length) {
    lines.push(`- **测试节点**：${testCase.testNodes.map((n) => `\`${n}\``).join("、")}`);
  }
  const repro = buildMinimalReproCommand({
    method: testCase.method,
    testNodes: testCase.testNodes,
    projectRoot: input.projectRoot,
    file: testCase.file,
  });
  if (repro) {
    lines.push("", "### 最小复现命令", "```powershell", repro, "```");
  }
  lines.push("");
  lines.push("### 预期与实际现象");
  lines.push(`- **预期**：${testCase.expected || "（未记录）"}`);
  lines.push(`- **实际**：${testCase.actual || testCase.message || "（未记录）"}`);
  if (testCase.message && testCase.actual && testCase.message !== testCase.actual) {
    lines.push(`- **执行信息**：${testCase.message}`);
  }
  if (testCase.errorType) {
    lines.push(`- **错误类型**：\`${testCase.errorType}\``);
  }
  if (testCase.judgement?.reason) {
    lines.push("", "### 独立裁判与根因线索");
    lines.push(`- **裁判结论**：${testCase.judgement.reason}`);
    if (testCase.judgement.suggestedFix) {
      lines.push(`- **建议方向**：${testCase.judgement.suggestedFix}`);
    }
  }
  if (testCase.serviceLogIssues?.length) {
    lines.push("", "### 窗口期后台服务异常日志", "```text");
    lines.push(...testCase.serviceLogIssues.slice(0, 8));
    lines.push("```");
  }
  if (testCase.stackKey) {
    lines.push("", "### 关键堆栈 / 断言位置", "```text", testCase.stackKey, "```");
  }
  if (testCase.evidence.length) {
    lines.push("", "### 证据引用");
    for (const ev of testCase.evidence.slice(0, 6)) {
      lines.push(`- \`${ev.sourcePath || ev.name}\``);
    }
  }
  return lines.join("\n");
}

/**
 * 构造可一键粘贴给 Trae AI 的问题单“AI 修复任务包”。
 */
export function buildIssueFixPrompt(input: IssueFixPromptInput): string {
  const { issue } = input;
  const lines: string[] = [
    `请帮我分析并修复以下测试记录的问题（先定位业务代码根因，不要绕过或削弱测试断言，修复后说明验证方式）：`,
    "",
  ];
  if (input.projectName || input.projectRoot) {
    lines.push(
      `- **项目**：${input.projectName || "当前项目"}${input.projectRoot ? `（根目录 \`${input.projectRoot}\`）` : ""}`,
    );
  }
  lines.push(`- **问题标题**：[${issue.severity}] ${issue.title}（状态：${issue.status}）`);
  const tierLabel = confidenceTierLabel(issue.confidence);
  if (tierLabel) {
    lines.push(
      `- **可信度**：${tierLabel}${issue.confidenceReason ? `（${issue.confidenceReason}）` : ""}`,
    );
  }
  if (issue.file) {
    lines.push(`- **疑似源码位置**：\`${issue.file}${issue.line ? `:${issue.line}` : ""}\``);
  }
  if (issue.itemText || issue.itemNo) {
    lines.push(`- **关联验收项**：${issue.itemNo ? `${issue.itemNo} ` : ""}${issue.itemText}`);
  }
  if (input.reproCommand?.trim()) {
    lines.push("", "### 最小复现命令", "```powershell", input.reproCommand.trim(), "```");
  }
  lines.push("", "### 预期与实际");
  lines.push(`- **预期**：${issue.expected || "（未记录）"}`);
  lines.push(`- **实际**：${issue.actual || "（未记录）"}`);
  if (issue.steps.length) {
    lines.push("", "### 复现步骤");
    issue.steps.forEach((step, idx) => {
      lines.push(`${idx + 1}. ${step}`);
    });
  }
  if (issue.signature) {
    lines.push("", "### 关键错误签名", "```text", issue.signature, "```");
  }
  if (issue.evidenceRefs.length) {
    lines.push("", "### 证据引用");
    for (const ref of issue.evidenceRefs.slice(0, 6)) {
      lines.push(`- \`${ref}\``);
    }
  }
  return lines.join("\n");
}
