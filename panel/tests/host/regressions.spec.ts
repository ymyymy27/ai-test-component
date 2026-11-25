import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createDraftTask, createProjectRecord } from "@/domain/factory";
import type { Project } from "@/domain/types";
import type { Api } from "@/host/commands";
import type { DoctorFacts, Issue as IssueDto, RunDetail } from "@/host/dto";
import { mapDoctorFacts } from "@/host/environment";
import { mapIssue, mapRun } from "@/host/map";
import { HostSidecar } from "@/host/sidecar";
import { HostRepository } from "@/repositories/HostRepository";
import type { ProjectRepository } from "@/repositories/ProjectRepository";
import { setRepository, useWorkspaceStore } from "@/stores/workspace";

function projectRecord(id: string, name = id): Project {
  const record = createProjectRecord({ id, name, root: `D:/work/${id}` });
  return { ...record, analysisStatus: "ready", analysisStartedAt: "" };
}

describe("audit regressions: host, sidecar, store & mappers", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    setRepository(null);
  });

  it("HostSidecar.prune keeps other projects' scopes and confirmations isolated", () => {
    const sidecar = new HostSidecar();
    sidecar.setScope("task_a1", ["i1"]);
    sidecar.setScope("task_a_old", ["i_old"]);
    sidecar.setScope("task_b1", ["j1"]);
    sidecar.setConfirmation("plan_a_old", {
      id: "conf_old",
      planId: "plan_a_old",
      planRevision: 1,
      confirmedBy: "u",
      confirmedAt: "2026-11-20T00:00:00Z",
      environment: {},
      credentials: {},
      acceptanceItemIds: [],
      dependencies: [],
      sideEffects: [],
      fingerprint: "fp1",
    });
    sidecar.setConfirmation("plan_b1", {
      id: "conf_b",
      planId: "plan_b1",
      planRevision: 1,
      confirmedBy: "u",
      confirmedAt: "2026-11-20T00:00:00Z",
      environment: {},
      credentials: {},
      acceptanceItemIds: [],
      dependencies: [],
      sideEffects: [],
      fingerprint: "fp2",
    });

    // 先建基线登记两个项目各自归属的任务和计划
    sidecar.prune("proj_a", ["task_a1", "task_a_old"], ["plan_a_old"]);
    sidecar.prune("proj_b", ["task_b1"], ["plan_b1"]);

    // 清理 proj_a 的旧任务/旧计划时，绝不能误删 proj_b 的 sidecar 条目
    sidecar.prune("proj_a", ["task_a1"], []);
    expect(sidecar.scopes["task_a_old"]).toBeUndefined();
    expect(sidecar.confirmations["plan_a_old"]).toBeUndefined();
    expect(sidecar.scopes["task_a1"]).toEqual(["i1"]);
    expect(sidecar.scopes["task_b1"]).toEqual(["j1"]);
    expect(sidecar.confirmations["plan_b1"]?.id).toBe("conf_b");
  });

  it("workspace.refreshAll merges summaries without wiping loaded full project details", async () => {
    const fullP1 = projectRecord("p1", "旧名称");
    fullP1.tasks = [
      {
        ...createDraftTask(fullP1),
        id: "t1",
        title: "已加载任务",
        stage: "planned",
      },
    ];
    const summaryP1: Project = {
      ...projectRecord("p1", "新名称"),
      tasks: [],
      archived: true,
      summaryOnly: true,
    };

    let firstCall = true;
    const repo = {
      mode: "host" as const,
      capabilities: {
        hostGithub: true,
        evidenceFiles: false,
        browserBackup: false,
        linkJson: false,
      },
      listProjects: vi.fn(async () => (firstCall ? ((firstCall = false), [fullP1]) : [summaryP1])),
      getProject: vi.fn(async () => fullP1),
      getLastProjectId: vi.fn(async () => "p1"),
      setLastProjectId: vi.fn(async () => undefined),
      environment: vi.fn(async () => []),
      workspacePath: vi.fn(() => "D:/ws"),
    } as unknown as ProjectRepository;

    setRepository(repo);
    const store = useWorkspaceStore();
    await store.bootstrap();
    await store.refreshAll();

    expect(store.projects).toHaveLength(1);
    expect(store.projects[0]?.name).toBe("新名称");
    expect(store.projects[0]?.archived).toBe(true);
    expect(store.projects[0]?.tasks).toHaveLength(1);
    expect(store.projects[0]?.summaryOnly).toBeUndefined();
  });

  it("HostRepository.addModule forwards status and updateModule omits name", async () => {
    const calls: Array<{ fn: string; payload: unknown }> = [];
    const client = {
      addModule: vi.fn(async (_pid: string, input: unknown) => {
        calls.push({ fn: "addModule", payload: input });
        return { module_id: "m1" };
      }),
      updateModule: vi.fn(async (_pid: string, _mid: string, patch: unknown) => {
        calls.push({ fn: "updateModule", payload: patch });
      }),
      projectSnapshot: vi.fn(async () => ({
        project: {
          project_id: "p1",
          name: "p1",
          root: "D:/repo",
          goal: "",
          created_at: "2026-11-20T00:00:00Z",
        },
        modules: [
          {
            module_id: "m1",
            name: "鉴权模块",
            responsibility: "登录认证",
            owner: "alice",
            status: "deprecated",
            deps: [],
          },
        ],
        rules: [],
        tasks: [],
        runs: [],
        issues: [],
        reports: [],
        evidence: {},
        map: null,
        map_stale: false,
        config: {},
      })),
    } as unknown as Api;

    const repo = new HostRepository(client);
    await repo.addModule("p1", {
      name: "鉴权模块",
      responsibility: "登录认证",
      owner: "alice",
      status: "deprecated",
      dependencies: [],
    });
    await repo.updateModule("p1", "m1", {
      name: "尝试改名应被忽略",
      responsibility: "新职责",
      status: "active",
    });

    expect(calls[0]?.payload).toMatchObject({ name: "鉴权模块", status: "deprecated" });
    expect(calls[1]?.payload).toEqual({ responsibility: "新职责", status: "active" });
    expect("name" in (calls[1]?.payload as Record<string, unknown>)).toBe(false);
  });

  it("mapDoctorFacts distinguishes broken proxy and formats object arrays without [object Object]", () => {
    const facts = {
      workspace: { ok: true, path: "D:/ws" },
      python: { ok: true, version: "3.13" },
      package: { ok: true, version: "0.5.0" },
      model: { ok: true, source: "env" },
      github: { ok: true, account: "alice" },
      proxy: { ok: false, configured: { HTTP_PROXY: "invalid" }, error: "代理端口非法" },
      project_config: {
        ok: false,
        problems: [{ project_id: "p1", kind: "invalid_json", detail: "config 解析失败" }],
      },
      references: {
        ok: false,
        missing: [{ project_id: "p1", path: "runs/r1/run.json", message: "文件缺失" }],
      },
      repairs: [],
    } as unknown as DoctorFacts;

    const checks = mapDoctorFacts(facts);
    const proxy = checks.find((entry) => entry.key === "proxy");
    const config = checks.find((entry) => entry.key === "config");
    const refs = checks.find((entry) => entry.key === "references");

    expect(proxy?.ok).toBe(false);
    expect(proxy?.value).toBe("配置错误");
    expect(proxy?.detail).toContain("代理端口非法");
    expect(config?.detail).toContain("config 解析失败");
    expect(config?.detail).not.toContain("[object Object]");
    expect(refs?.detail).toContain("文件缺失");
    expect(refs?.detail).not.toContain("[object Object]");
  });

  it("mapRun falls back to finished_at delta and mapIssue keeps historical occurrences failed", () => {
    const detail = {
      run: {
        run_id: "r1",
        plan_id: "pl1",
        project_id: "p1",
        scope: { preset: "selected", module_ids: [], acceptance_item_ids: [] },
        status: "completed",
        started_at: "2026-11-23T10:00:00.000Z",
        finished_at: "2026-11-23T10:00:12.500Z",
        cases: [],
        evidence_refs: [],
        analysis: [],
        dependency_repairs: [],
      },
      outcome: "passed",
      counts: { selected: 0, applicable: 0, covered: 0, passed: 0, failed: 0, incomplete: 0 },
      item_outcomes: [],
      uncovered_items: [],
      link: null,
    } as unknown as RunDetail;

    const mappedRun = mapRun(detail, [], []);
    expect(mappedRun.elapsedMs).toBe(12500);
    expect(mappedRun.finishedAt).toBe("2026-11-23T10:00:12.500Z");

    const issue = mapIssue(
      {
        issue_id: "iss_1",
        signature: "c1:err",
        title: "失败记录",
        expected: "pass",
        actual: "fail",
        steps: [],
        severity: "P1",
        status: "pending",
        last_result: "passed",
        reason: "",
        occurrences: [{ run_id: "r1", case_id: "c1", at: "2026-11-23T10:00:00Z" }],
        unstable: true,
      } as unknown as IssueDto,
      "p1",
      new Map(),
    );
    expect(issue.occurrences[0]?.result).toBe("failed");
  });
});
