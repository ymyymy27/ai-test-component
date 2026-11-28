import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { h } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";

import CodeGraphViewer from "@/components/CodeGraphViewer.vue";
import FormField from "@/components/FormField.vue";
import GitStatusBar from "@/components/GitStatusBar.vue";
import PreflightAndProbesCard from "@/components/PreflightAndProbesCard.vue";
import ReportCaseTable from "@/components/report/ReportCaseTable.vue";
import ReportScopeAndChecks from "@/components/report/ReportScopeAndChecks.vue";
import SettingsProfileSection from "@/components/settings/SettingsProfileSection.vue";
import type { CodeGraph, LocalGitInfo, PreflightReport, ProbeResult, Report } from "@/domain/types";
import { BREAKPOINT_COMPACT, BREAKPOINT_DRAWER, BREAKPOINT_MEDIUM } from "@/stores/ui";
import { setRepository, useWorkspaceStore } from "@/stores/workspace";
import SettingsView from "@/views/SettingsView.vue";

import { finishRun, seedRunningTask } from "../fixtures";

function collectCssFiles(dir: string): string[] {
  const results: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      results.push(...collectCssFiles(full));
    } else if (full.endsWith(".css")) {
      results.push(full);
    }
  }
  return results;
}

const sampleGraph: CodeGraph = {
  generatedAt: "2026-11-24T12:00:00Z",
  sourceHash: "h1",
  stats: {
    totalFiles: 3,
    frontend: 1,
    api: 1,
    backend: 1,
    storage: 0,
    edges: 2,
    conflicts: 1,
  },
  nodes: [
    {
      id: "fe:app.ts",
      label: "app.ts",
      layer: "frontend",
      kind: "ts",
      file: "src/app.ts",
      line: 1,
      module: "frontend",
      meta: {},
      hasConflict: false,
      impact: "transitive",
    },
    {
      id: "api:GET:/api/health",
      label: "GET /api/health",
      layer: "api",
      kind: "get",
      file: "api/health.py",
      line: 5,
      module: "api",
      meta: {},
      hasConflict: true,
      impact: "direct",
    },
    {
      id: "be:health.py",
      label: "health.py",
      layer: "backend",
      kind: "python",
      file: "api/health.py",
      line: 1,
      module: "api",
      meta: {},
      hasConflict: false,
      impact: "direct",
    },
  ],
  edges: [
    {
      id: "e1",
      source: "fe:app.ts",
      target: "api:GET:/api/health",
      kind: "calls_api",
      label: "GET /api/health",
      conflict: false,
    },
    {
      id: "e2",
      source: "api:GET:/api/health",
      target: "be:health.py",
      kind: "handled_by",
      label: "处理",
      conflict: false,
    },
  ],
  conflicts: [
    {
      kind: "contract_mismatch",
      severity: "error",
      detail: "GET /api/health 返回字段不一致",
      file: "src/app.ts",
      nodeIds: ["fe:app.ts", "api:GET:/api/health"],
    },
  ],
  moduleChunks: [],
  impact: {
    changedFiles: ["api/health.py"],
    directNodeIds: ["api:GET:/api/health", "be:health.py"],
    affectedNodeIds: ["fe:app.ts", "api:GET:/api/health", "be:health.py"],
    affectedModules: ["api", "frontend"],
    affectedRoutes: ["GET /api/health"],
  },
};

const sampleGit: LocalGitInfo = {
  isGit: true,
  available: true,
  reason: "",
  branch: "develop",
  head: "abcdef1234567890",
  shortHead: "abcdef1",
  headSubject: "polish panel styling",
  headAuthor: "dev",
  headTime: "2026-11-24T12:00:00Z",
  baseBranch: "main",
  remoteUrl: "",
  latestTag: "",
  ahead: 1,
  behind: 0,
  dirty: true,
  detached: false,
  stagedFiles: [],
  unstagedFiles: ["api/health.py"],
  untrackedFiles: [],
  changedFiles: ["api/health.py"],
  diffStat: [{ path: "api/health.py", additions: 4, deletions: 1, status: "modified" }],
  changedHunks: [{ path: "api/health.py", start: 5, count: 3 }],
  recentCommits: [],
  hotspots: [],
};

const samplePreflight: PreflightReport = {
  ok: true,
  orderedServices: ["db", "api"],
  conflictedServices: [],
  reusedServices: [],
  portChecks: [{ port: 8000, available: true, detail: "端口空闲" }],
  envChecks: [{ key: "PORT", ok: true, detail: "已设定" }],
  toolChecks: [{ tool: "pytest", available: true, detail: "ok" }],
  blockingReasons: [],
  warnings: [],
};

const sampleProbes: ProbeResult[] = [
  {
    probeId: "contract_reconciliation",
    name: "前后端契约自动对账",
    status: "failed",
    checked: 1,
    summary: "发现 1 处不一致",
    findings: [
      {
        kind: "missing_route",
        detail: "缺少 /api/legacy",
        source: "src/app.ts",
        path: "/api/legacy",
        line: 10,
        method: "GET",
        conventionMatched: false,
        conventionReason: "",
      },
    ],
    suppressedFindings: [],
  },
];

describe("Stage 4 Design Token, Breakpoint & A11y Gate", () => {
  it("forbids hardcoded colors outside tokens.css and enforces exactly 3 responsive breakpoints synced with ui.ts", () => {
    const stylesRoot = resolve(process.cwd(), "src/styles");
    const cssFiles = collectCssFiles(stylesRoot);
    expect(cssFiles.length).toBeGreaterThan(3);

    const colorHits: string[] = [];
    const breakpoints = new Set<string>();
    const colorPattern = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g;
    const mediaPattern = /@media\s*\(\s*max-width\s*:\s*([^)]+)\)/g;

    for (const file of cssFiles) {
      const content = readFileSync(file, "utf8");
      const mediaMatches = content.matchAll(mediaPattern);
      for (const m of mediaMatches) {
        breakpoints.add((m[1] ?? "").trim());
      }
      if (!file.endsWith("tokens.css")) {
        const lines = content.split(/\r?\n/);
        lines.forEach((line, idx) => {
          const matches = line.match(colorPattern);
          if (matches) {
            colorHits.push(`${file}:${idx + 1}: ${matches.join(", ")}`);
          }
        });
      }
    }

    expect(colorHits).toEqual([]);
    expect([...breakpoints].sort()).toEqual(["620px", "768px", "900px"]);
    expect(BREAKPOINT_DRAWER).toBe(900);
    expect(BREAKPOINT_MEDIUM).toBe(768);
    expect(BREAKPOINT_COMPACT).toBe(620);

    const layoutCss = readFileSync(join(stylesRoot, "layout.css"), "utf8");
    expect(layoutCss).toContain(".side-nav button.is-active");
    expect(layoutCss).toContain(".side-task.is-active");
    expect(layoutCss).toContain(".settings-index button.is-active");
    expect(layoutCss).not.toMatch(/\.side-task\.is-active\s+\.side-task-title\s*\{\s*color:\s*var\(--ink\)/);
  });

  it("renders FormField with accessible label, hint, error alert, aria-invalid, and aria-describedby", () => {
    const wrapper = mount(FormField, {
      props: {
        id: "demo-input",
        label: "服务端口",
        optional: "可选",
        hint: "输入 1024-65535 之间端口",
        error: "端口超出合法范围",
        required: true,
      },
      slots: {
        default: ({ inputId, describedBy, invalid }: { inputId: string; describedBy?: string; invalid?: string }) =>
          h("input", {
            id: inputId,
            "aria-describedby": describedBy,
            "aria-invalid": invalid,
          }),
      },
    });

    expect(wrapper.find(".field").classes()).toContain("has-error");
    expect(wrapper.find("label").attributes("for")).toBe("demo-input");
    const hint = wrapper.find("#demo-input-hint");
    const err = wrapper.find("#demo-input-error");
    expect(hint.text()).toContain("1024-65535");
    expect(err.attributes("role")).toBe("alert");
    expect(err.text()).toBe("端口超出合法范围");

    const input = wrapper.find("#demo-input");
    expect(input.attributes("aria-invalid")).toBe("true");
    expect(input.attributes("aria-describedby")).toBe("demo-input-hint demo-input-error");
  });

  it("demotes 数据边界 out of top-level settings tabs while preserving deep-link expansion", async () => {
    const seeded = await seedRunningTask(["01"], "D:/work/stage4-settings", "设置测试项目");
    await finishRun(seeded.repo, seeded.projectId, seeded.task.id, seeded.runId);
    setRepository(seeded.repo);
    const workspace = useWorkspaceStore();
    await workspace.bootstrap();
    await workspace.setCurrentProject(seeded.projectId);

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/", component: { template: "<div />" } },
        { path: "/projects/:projectId/settings", component: SettingsView },
        { path: "/projects/:projectId/overview", component: { template: "<div />" } },
      ],
    });
    await router.push(`/projects/${seeded.projectId}/settings?section=settings-boundary`);
    await router.isReady();

    const wrapper = mount(
      { template: "<router-view />" },
      { global: { plugins: [router] } },
    );

    const tabs = wrapper.findAll('[role="tab"]');
    expect(tabs).toHaveLength(6);
    expect(tabs.map((tab) => tab.text())).not.toContain("数据边界");

    const boundaryDetails = wrapper.find("details#settings-boundary");
    expect(boundaryDetails.exists()).toBe(true);
    expect((boundaryDetails.element as HTMLDetailsElement).open).toBe(true);
    setRepository(null);
  });

  it("ensures tables have data-label cells for 390px cards and buttons/inputs expose accessible names", async () => {
    const seeded = await seedRunningTask(["01"], "D:/work/stage4-a11y", "无障碍门禁项目");
    await finishRun(seeded.repo, seeded.projectId, seeded.task.id, seeded.runId);
    const latestProject = await seeded.repo.getProject(seeded.projectId);
    const task = latestProject?.tasks.find((t) => t.id === seeded.task.id);
    const run = task?.runs[0] ?? null;
    const report = latestProject?.reports[0] ?? null;
    expect(report).not.toBeNull();
    const enrichedReport: Report = {
      ...(report as Report),
      preflight: samplePreflight,
      probeResults: sampleProbes,
    };

    const graphWrapper = mount(CodeGraphViewer, {
      props: { graph: sampleGraph, canOpenSource: true },
    });
    const listBtn = graphWrapper.findAll("button").find((b) => b.text().trim() === "列表");
    expect(listBtn).toBeDefined();
    await listBtn!.trigger("click");

    const gitWrapper = mount(GitStatusBar, {
      props: { localGit: sampleGit, codeGraph: sampleGraph, dirtyFiles: ["api/health.py"] },
    });
    const preflightWrapper = mount(PreflightAndProbesCard, {
      props: { preflight: samplePreflight, probeResults: sampleProbes, canOpenSource: true },
    });
    const scopeWrapper = mount(ReportScopeAndChecks, {
      props: { report: enrichedReport, activeItemId: "", canOpenSource: true },
    });
    const caseWrapper = mount(ReportCaseTable, {
      props: { report: enrichedReport, run, activeItemId: "", canOpenSource: true },
    });
    const profileWrapper = mount(SettingsProfileSection, {
      props: { project: latestProject!, active: true },
    });

    for (const w of [graphWrapper, gitWrapper, preflightWrapper, scopeWrapper, caseWrapper, profileWrapper]) {
      for (const td of w.findAll("table td")) {
        if (!td.attributes("colspan")) {
          expect(td.attributes("data-label")?.trim().length).toBeGreaterThan(0);
        }
      }
      for (const btn of w.findAll("button")) {
        const accessibleName =
          btn.text().trim() ||
          btn.attributes("aria-label")?.trim() ||
          btn.attributes("title")?.trim() ||
          "";
        expect(accessibleName.length).toBeGreaterThan(0);
      }
      for (const control of w.findAll("input, textarea, select")) {
        const id = control.attributes("id");
        const hasLabel = id ? w.find(`label[for="${id}"]`).exists() : false;
        const hasAriaLabel = Boolean(control.attributes("aria-label")?.trim());
        expect(hasLabel || hasAriaLabel).toBe(true);
      }
    }
  });
});
