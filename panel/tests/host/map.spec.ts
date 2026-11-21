import { describe, expect, it } from "vitest";

import { projectStatus } from "@/domain/derive";
import type { Plan } from "@/domain/types";
import {
  compareItemNo,
  mapConfirmation,
  mapPlanCase,
  mapProjectMap,
  mapProjectSummary,
  mapQuickCheck,
  mapReport,
  mapRun,
  toHostConfig,
} from "@/host/map";
import type { RunDetail } from "@/host/dto";
import { HostSidecar } from "@/host/sidecar";
import type { ProjectConfig } from "@/domain/types";

describe("host config mapping", () => {
  it("writes the CLI snake_case keys instead of panel camelCase keys", () => {
    const config: ProjectConfig = {
      services: { healthUrl: "http://127.0.0.1:8000/health", baseUrl: "http://127.0.0.1:8000" },
      evidence: { singleFileMb: 10, mediaMb: 200 },
      link: { logPath: "a.log, b.log" },
      data: { cleanupCommand: "npm run clean" },
    };
    const dto = toHostConfig(config);
    expect(dto.services).toEqual({
      health: "http://127.0.0.1:8000/health",
      base_url: "http://127.0.0.1:8000",
    });
    expect(dto.evidence).toEqual({
      file_cap_mb: 10,
      media_budget_mb: 200,
      video: false,
    });
    expect(dto.link).toEqual({ log_paths: ["a.log", "b.log"] });
    expect(dto.data).toEqual({ cleanup: "npm run clean", data_prefix: "" });
    expect(JSON.stringify(dto)).not.toContain("healthUrl");
    expect(JSON.stringify(dto)).not.toContain("singleFileMb");
  });

  it("sorts acceptance items by their numeric number", () => {
    expect(["04", "02", "10", "03"].sort(compareItemNo)).toEqual(["02", "03", "04", "10"]);
  });

  it("keeps the environment fingerprint on the mapped run", () => {
    const detail: RunDetail = {
      run: {
        run_id: "run_1",
        plan_id: "plan_1",
        project_id: "proj_1",
        scope: {
          preset: "selected",
          module_ids: [],
          acceptance_item_ids: [],
        },
        status: "completed",
        started_at: "2026-11-20T20:00:00+08:00",
        cases: [],
        evidence_refs: [],
        analysis: [],
        environment: {
          python_version: "3.13.13",
          venv: "E:/repo/.venv",
          node: "v24.14.1",
        },
        dependency_suggestions: [
          {
            module: "factory",
            package: "factory-boy>=3.2",
            source: "declared",
            reason: "项目测试依赖已声明",
            case_id: "case_1",
            installed: "false",
          },
        ],
        dependency_repairs: [],
      },
      outcome: "passed",
      counts: { selected: 0, applicable: 0, covered: 0, passed: 0, failed: 0, incomplete: 0 },
      item_outcomes: [],
      uncovered_items: [],
      link: null,
    };
    const run = mapRun(detail, [], []);
    expect(run.environment?.["python_version"]).toBe("3.13.13");
    expect(run.environment?.["venv"]).toBe("E:/repo/.venv");
    expect(run.dependencySuggestions?.[0]).toMatchObject({
      module: "factory",
      package: "factory-boy>=3.2",
      source: "declared",
      caseId: "case_1",
      installed: false,
    });
  });

  it("does not mark expectations as verified when the plan snapshot is missing", () => {
    const detail: RunDetail = {
      run: {
        run_id: "run_1",
        plan_id: "plan_1",
        project_id: "proj_1",
        scope: { preset: "selected", module_ids: [], acceptance_item_ids: ["item_1"] },
        status: "completed",
        started_at: "2026-11-20T20:00:00+08:00",
        cases: [
          {
            case_id: "case_1",
            attempts: [{ attempt: 1, result: "passed", message: "ok" }],
            evidence_refs: [],
            result: "passed",
          },
        ],
        evidence_refs: [],
        analysis: [],
        dependency_repairs: [],
      },
      outcome: "passed",
      counts: { selected: 1, applicable: 1, covered: 1, passed: 1, failed: 0, incomplete: 0 },
      item_outcomes: [{ item_id: "item_1", result: "passed", case_ids: ["case_1"] }],
      uncovered_items: [],
      link: null,
    };
    const run = mapRun(detail, [], []);
    expect(run.cases[0]?.itemId).toBe("");
    expect(run.cases[0]?.expectationVerified).toBe(false);
    expect(run.items[0]?.outcome).toBe("passed");
  });

  it("keeps cancelled and execution_error quick check statuses", () => {
    const base = {
      run: {
        run_id: "run_1",
        plan_id: "plan_1",
        project_id: "proj_1",
        scope: { preset: "selected", module_ids: [], acceptance_item_ids: [] },
        started_at: "2026-11-20T20:00:00+08:00",
        cases: [],
        evidence_refs: [],
        analysis: [],
        dependency_repairs: [],
      },
      outcome: "incomplete",
      counts: { selected: 0, applicable: 0, covered: 0, passed: 0, failed: 0, incomplete: 0 },
      item_outcomes: [],
      uncovered_items: [],
      link: null,
    };
    expect(mapQuickCheck({ ...base, run: { ...base.run, status: "cancelled" } } as RunDetail, "p").status).toBe(
      "cancelled",
    );
    expect(
      mapQuickCheck(
        { ...base, run: { ...base.run, status: "execution_error" } } as RunDetail,
        "p",
      ).status,
    ).toBe("execution_error");
    // 中断不是完成：面板必须按终态显示，不能显示“已完成”。
    expect(
      mapQuickCheck({ ...base, run: { ...base.run, status: "interrupted" } } as RunDetail, "p").status,
    ).toBe("cancelled");
  });

  it("marks project list summaries as not loaded instead of claiming no tasks", () => {
    const summary = mapProjectSummary(
      { project_id: "proj_1", name: "demo", root: "C:/demo" },
      new HostSidecar(),
    );
    expect(summary.summaryOnly).toBe(true);
    expect(projectStatus(summary)).toEqual({ tone: "neutral", label: "未加载" });
  });

  it("maps the CLI confirmation returned by run.start without inventing environment or credentials", () => {
    const plan = {
      id: "plan_1",
      revision: 3,
      scope: { acceptanceItemIds: ["item_1"] },
    } as unknown as Plan;
    const confirmation = mapConfirmation(
      {
        confirmation_id: "conf_1",
        confirmed_by: "单元测试",
        confirmed_at: "2026-11-21T10:00:00+08:00",
        fingerprint: "abc",
        plan_revision: 3,
        side_effects: ["写入运行目录"],
        dependencies: ["pytest"],
      },
      plan,
    );
    expect(confirmation.id).toBe("conf_1");
    expect(confirmation.confirmedBy).toBe("单元测试");
    expect(confirmation.fingerprint).toBe("abc");
    expect(confirmation.acceptanceItemIds).toEqual(["item_1"]);
    expect(confirmation.environment).toEqual({});
    expect(confirmation.credentials).toEqual({});
  });

  it("reads project_revision for the map and never defaults the plan expectation to verified", () => {
    const sidecar = new HostSidecar();
    const map = mapProjectMap(
      { project_revision: 7, source_commit: "abc", business_functions: [] },
      "proj_1",
      sidecar,
    );
    expect(map?.revision).toBe(7);
    const planCase = mapPlanCase({
      case_id: "case_1",
      acceptance_item_id: "item_1",
      layer: "L2",
      expected: "通过",
    });
    expect(planCase.expectationVerified).toBe(false);
  });

  it("tolerates invalid timestamps and missing link arrays", () => {
    const detail = {
      run: {
        run_id: "run_bad_date",
        plan_id: "plan_1",
        project_id: "proj_1",
        scope: { preset: "selected", module_ids: [], acceptance_item_ids: [] },
        status: "completed",
        started_at: "",
        cases: [],
        evidence_refs: [],
        analysis: [],
        dependency_repairs: [],
      },
      outcome: "incomplete",
      counts: { selected: 0, applicable: 0, covered: 0, passed: 0, failed: 0, incomplete: 0 },
      item_outcomes: [],
      uncovered_items: [],
      link: {
        first_anomaly: null,
        location: { known: false, reason: "无法定位" },
      },
    } as unknown as RunDetail;
    const run = mapRun(detail, [], []);
    expect(Number.isFinite(new Date(run.startedAt).getTime())).toBe(true);
    expect(run.link?.nodes).toEqual([]);
  });

  it("keeps a not_generated analysis status instead of claiming generated", () => {
    const report = mapReport(
      {
        report_id: "rep_1",
        run_id: "run_1",
        outcome: "incomplete",
        conclusion: "未完成",
        counts: {},
        coverage: {},
        changed_modules: [],
        unmapped_files: [],
        evidence_refs: [],
        mock_judgement: [],
        real_chain: false,
        issues: [],
        issue_count: 0,
        failure_occurrences: 0,
        analysis: [{ status: "not_generated", reason: "model_missing" }],
        layers: { L2: 1 },
        templates: [],
        environment: {},
        uncovered_items: [],
        unverified_items: [],
        item_details: [],
        case_details: [],
        evidence_index: [],
        revision: 1,
      },
      "p",
      "t",
      null,
      null,
      [],
      "未绑定规则",
    );
    expect(report.analysis.status).toBe("not_generated");
    expect(report.analysis.reason).toBe("model_missing");
    expect(report.layers.L2.total).toBe(1);
  });
});
