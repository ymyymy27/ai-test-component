import { describe, expect, it } from "vitest";

import { reportMarkdown } from "@/services/exportService";
import type { Project, Report, Task } from "@/domain/types";

const report = {
  id: "rep_1",
  projectId: "p",
  taskId: "t",
  runId: "r",
  version: 1,
  generatedAt: "2026-11-21T10:00:00+08:00",
  verdict: "failed",
  counts: { selected: 1, applicable: 1, covered: 1, passed: 0, failed: 1, incomplete: 0 },
  conclusion: "失败",
  coverage: { selected: 1, applicable: 1, covered: 1, unselected: 0 },
  itemDetails: [
    {
      itemId: "item_1",
      no: "01",
      text: "第一行 | 含竖线\n第二行",
      outcome: "failed",
      caseIds: ["case_1"],
      selected: true,
    },
  ],
  caseDetails: [],
  l1: [],
  layers: {},
  issues: [],
  mock: {},
  analysis: { status: "not_generated", reason: "no_failures" },
  link: null,
  templates: [],
  modules: [],
  unmapped: [],
  uncoveredItems: [],
  unverifiedItems: [],
  planWarnings: [],
  evidenceIndex: [],
  source: {},
  summary: "",
  opinion: null,
  status: "final",
} as unknown as Report;

const project = { name: "演示", issues: [] } as unknown as Project;
const task = { title: "任务" } as Task;

describe("report markdown export", () => {
  it("escapes table separators and flattens newlines in acceptance item text", () => {
    const markdown = reportMarkdown(project, task, report);
    const row = markdown
      .split("\n")
      .find((line) => line.startsWith("| 01 |"));
    expect(row).toContain("第一行 \\| 含竖线 第二行");
    expect(row).not.toContain("\n");
  });
});
