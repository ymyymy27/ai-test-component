import { describe, expect, it } from "vitest";

import { compareItemNo, mapRun, toHostConfig } from "@/host/map";
import type { RunDetail } from "@/host/dto";
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
    expect(dto.data).toEqual({ cleanup: "npm run clean" });
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
});
