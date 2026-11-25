import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Api } from "@/host/commands";
import type { ProjectSnapshot, RunDetail } from "@/host/dto";
import { mapPlan, mapRun, mapTask } from "@/host/map";
import { HostSidecar } from "@/host/sidecar";
import { HostRepository } from "@/repositories/HostRepository";

function makeSnapshot(overrides: Partial<ProjectSnapshot> = {}): ProjectSnapshot {
  return {
    project: {
      project_id: "proj_1",
      name: "Stage1A 宿主验证",
      root: "D:/repo",
      goal: "验证宿主同步",
      modules: [],
      revision: 1,
      updated_at: "2026-11-24T00:00:00Z",
    },
    modules: [],
    map: null,
    map_stale: false,
    config: {},
    rules: [],
    tasks: [],
    runs: [],
    evidence: {},
    reports: [],
    issues: [],
    github: null,
    ...overrides,
  };
}

describe("stage 1A host consistency & snapshot regressions", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("补-FD25: retains orphan runs under a readonly archive task instead of dropping them", async () => {
    const orphanRun: RunDetail = {
      run: {
        run_id: "run_orphan",
        plan_id: "plan_deleted",
        project_id: "proj_1",
        scope: { preset: "selected", module_ids: [], acceptance_item_ids: ["i_deleted"] },
        status: "completed",
        started_at: "2026-11-24T01:00:00Z",
        cases: [{ case_id: "c1", result: "passed", attempts: [], evidence_refs: [] }],
        evidence_refs: [],
        analysis: [],
      },
      plan: {
        plan_id: "plan_deleted",
        task_id: "task_deleted",
        scope: { preset: "selected", module_ids: [], acceptance_item_ids: ["i_deleted"] },
        applicable_count: 1,
        cases: [],
        item_texts: { i_deleted: "已删任务验收项" },
        status: "published",
        revision: 1,
      },
      task: null,
      outcome: "passed",
    };

    const client = {
      projectSnapshot: vi.fn(async () => makeSnapshot({ runs: [orphanRun] })),
    } as unknown as Api;
    const repo = new HostRepository(client);
    const project = await repo.getProject("proj_1");

    expect(project).not.toBeNull();
    const archiveTask = project?.tasks.find((t) => t.id === "__archived_runs__");
    expect(archiveTask).toBeDefined();
    expect(archiveTask?.runs.map((r) => r.id)).toEqual(["run_orphan"]);
    expect(archiveTask?.acceptanceItems[0]?.text).toBe("已删任务验收项");
  });

  it("补-FD42: empty sidecar scope does not silence plan or applicable scope on map or edit", async () => {
    const sidecar = new HostSidecar();
    sidecar.setScope("task_1", []);

    const mapped = mapTask(
      {
        task_id: "task_1",
        project_id: "proj_1",
        title: "任务",
        acceptance_items: [
          { item_id: "i1", text: "项 1", observable: true, status: "active" },
          { item_id: "i2", text: "项 2", observable: true, status: "active" },
        ],
        revision: 1,
      },
      [],
      [],
      null,
      [],
      [],
      sidecar,
    );
    expect(mapped.selectedItemIds).toEqual(["i1", "i2"]);

    const client = {
      projectSnapshot: vi.fn(async () =>
        makeSnapshot({
          tasks: [
            {
              task: {
                task_id: "task_1",
                project_id: "proj_1",
                title: "任务",
                acceptance_items: [
                  { item_id: "i_new", text: "新项", observable: true, status: "active" },
                ],
                revision: 2,
              },
              plans: [],
              delivery: null,
            },
          ],
        }),
      ),
      saveTask: vi.fn(async () => ({})),
    } as unknown as Api;
    sidecar.setScope("task_1", ["i_old_removed"]);
    const repo = new HostRepository(client);
    const updated = await repo.updateTask("proj_1", "task_1", { title: "任务" });
    expect(updated.selectedItemIds).toEqual(["i_new"]);
    const persisted = JSON.parse(localStorage.getItem("aitest.host.ui.v1") ?? "{}") as {
      scopes?: Record<string, string[]>;
    };
    expect(persisted.scopes?.["task_1"]).toBeUndefined();
  });

  it("补-FD23 & 补-FD29: markRunInterrupted calls cancelRun and saveGithubSnapshot persists", async () => {
    const cancelCalls: Array<{ pid: string; rid: string; opts: unknown }> = [];
    const savedGithub: Array<{ pid: string; snapshot: Record<string, unknown> }> = [];
    const client = {
      projectSnapshot: vi.fn(async () =>
        makeSnapshot({
          github: {
            repo: "team/app",
            branch: "main",
            commit: "commit_from_backend",
            files: ["a.py"],
          },
          runs: [
            {
              run: {
                run_id: "run_1",
                plan_id: "p1",
                project_id: "proj_1",
                scope: { preset: "all_applicable", module_ids: [], acceptance_item_ids: [] },
                status: "interrupted",
                started_at: "2026-11-24T00:00:00Z",
                cases: [],
                evidence_refs: [],
                analysis: [],
              },
              outcome: "incomplete",
            },
          ],
        }),
      ),
      cancelRun: vi.fn(async (pid: string, rid: string, opts: unknown) => {
        cancelCalls.push({ pid, rid, opts });
        return { cancel_requested: true, status: "interrupted" };
      }),
      runStatus: vi.fn(async () => ({
        run: {
          run_id: "run_1",
          plan_id: "p1",
          project_id: "proj_1",
          scope: { preset: "all_applicable", module_ids: [], acceptance_item_ids: [] },
          status: "interrupted" as const,
          started_at: "2026-11-24T00:00:00Z",
          cases: [],
          evidence_refs: [],
          analysis: [],
        },
        outcome: "incomplete" as const,
      })),
      listEvidence: vi.fn(async () => ({ evidence: [] })),
      saveGithubSnapshot: vi.fn(async (pid: string, snapshot: Record<string, unknown>) => {
        savedGithub.push({ pid, snapshot });
        return { repo: "team/app", commit: String(snapshot.commit), path: "github/abc.json" };
      }),
    } as unknown as Api;

    const repo = new HostRepository(client);
    const loaded = await repo.getProject("proj_1");
    expect(loaded?.github?.commit).toBe("commit_from_backend");

    const interrupted = await repo.markRunInterrupted("proj_1", "run_1");
    expect(interrupted.status).toBe("interrupted");
    expect(cancelCalls).toEqual([
      { pid: "proj_1", rid: "run_1", opts: { markInterrupted: true } },
    ]);

    await repo.saveGithubSnapshot("proj_1", {
      repo: "team/app",
      branch: "main",
      commit: "sha999",
      files: ["b.py"],
      prs: ["#10"],
      checks: ["pytest"],
      deployment: "",
      account: "alice",
      host: "github.com",
      fetchedAt: "2026-11-24T02:00:00Z",
      cached: true,
      unavailableReason: "",
    });
    expect(savedGithub).toHaveLength(1);
    expect(savedGithub[0]?.snapshot.commit).toBe("sha999");
  });

  it("补-FV28/FD30: setFeatureIgnored persists backend and cascades catalog & candidates", async () => {
    const persistedIgnored: string[][] = [];
    const client = {
      projectSnapshot: vi.fn(async () =>
        makeSnapshot({
          map: {
            project_revision: 1,
            source_commit: "abc",
            generated_at: "2026-11-24T00:00:00Z",
            ignored_features: ["旧报表"],
            business_functions: [
              {
                name: "登录认证",
                modules: ["auth"],
                entry_points: ["auth.py"],
                test_commands: ["pytest"],
                testable_items: ["用户可以登录"],
                evidence: ["auth.py"],
                confidence: "high",
              },
              {
                name: "旧报表",
                modules: ["legacy"],
                entry_points: ["legacy.py"],
                test_commands: ["pytest"],
                testable_items: ["导出旧 Excel"],
                evidence: ["legacy.py"],
                confidence: "medium",
              },
            ],
            unknowns: [],
          },
        }),
      ),
      setIgnoredFeatures: vi.fn(async (_pid: string, list: string[]) => {
        persistedIgnored.push(list);
        return { map: { ignored_features: list }, stale: false };
      }),
    } as unknown as Api;

    const repo = new HostRepository(client);
    const project = await repo.getProject("proj_1");
    expect(project?.map?.ignoredFeatures).toEqual(["旧报表"]);
    expect(project?.catalog.map((item) => item.featureName)).toEqual(["登录认证"]);

    const restored = await repo.setFeatureIgnored("proj_1", "旧报表", false);
    expect(persistedIgnored).toEqual([[]]);
    expect(restored.catalog.map((item) => item.featureName)).toEqual(["登录认证", "旧报表"]);
  });

  it("F-21 & tokenUsage: maps excluded_items (including empty list) and token_usage accurately", () => {
    const sidecar = new HostSidecar();
    const mappedPlan = mapPlan(
      {
        plan: {
          plan_id: "plan_1",
          task_id: "task_1",
          scope: { preset: "selected", module_ids: [], acceptance_item_ids: ["i1"] },
          applicable_count: 2,
          cases: [],
          excluded_items: [{ item_id: "i2", no: "02", text: "删除记录" }],
          status: "published",
          revision: 1,
        },
        coverage: { selected: 1, applicable: 2 },
      },
      "proj_1",
      "task_1",
      sidecar,
    );
    expect(mappedPlan.excludedItems).toEqual([
      { id: "i2", no: "02", text: "删除记录", moduleId: null },
    ]);

    const mappedRun = mapRun(
      {
        run: {
          run_id: "run_1",
          plan_id: "plan_1",
          project_id: "proj_1",
          scope: { preset: "all_applicable", module_ids: [], acceptance_item_ids: ["i1"] },
          status: "completed",
          started_at: "2026-11-24T00:00:00Z",
          cases: [],
          evidence_refs: [],
          analysis: [],
          excluded_items: [],
          token_usage: { prompt_tokens: 100, completion_tokens: 40, total_tokens: 140, calls: 2 },
        },
        outcome: "passed",
      },
      [],
      [],
    );
    expect(mappedRun.excludedItems).toEqual([]);
    expect(mappedRun.tokenUsage).toEqual({
      promptTokens: 100,
      completionTokens: 40,
      totalTokens: 140,
      calls: 2,
    });
  });
});
