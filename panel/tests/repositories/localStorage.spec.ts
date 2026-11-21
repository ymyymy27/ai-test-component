import { describe, expect, it } from "vitest";

import { buildProjectAnalysis } from "@/domain/analysis";
import { issueCloseGate, sideEffectsFor } from "@/domain/derive";
import type { EvidenceRef } from "@/domain/types";
import { LocalStorageRepository } from "@/repositories/LocalStorageRepository";
import { buildEnvironmentChecks } from "@/services/environmentService";
import { completedRun, finishRun, seedPlannedTask, seedProject } from "../fixtures";

function evidence(name: string): EvidenceRef {
  return {
    id: `ev_${name}`,
    name,
    mediaType: "LOG",
    bytes: 12,
    sha256: "a".repeat(64),
    note: "测试证据",
    missing: false,
    truncated: false,
    addedAt: new Date().toISOString(),
    sourcePath: `evidence/${name}`,
  };
}

describe("LocalStorageRepository", () => {
  it("creates a project, stores analysis and rejects duplicate roots", async () => {
    const seeded = await seedProject();
    expect(seeded.project.analysisStatus).toBe("ready");
    expect(seeded.project.catalog).toHaveLength(5);
    expect(seeded.project.candidates).toHaveLength(3);
    await expect(
      seeded.repo.createProject({ root: "D:/work/demo", name: "重复" }),
    ).rejects.toMatchObject({ code: "VALIDATION" });
  });

  it("creates task, plan and run with one active run per project", async () => {
    const seeded = await seedPlannedTask(["01"]);
    const plan = seeded.task.plans[0];
    expect(plan).toBeDefined();
    expect(plan?.scope.coverage.selected).toBe(1);
    expect(plan?.cases).toHaveLength(1);

    const run = await seeded.repo.startRun(seeded.projectId, seeded.task.id, plan?.id as string);
    await expect(
      seeded.repo.startRun(seeded.projectId, seeded.task.id, plan?.id as string),
    ).rejects.toMatchObject({ code: "RUN_ACTIVE" });
    await finishRun(seeded.repo, seeded.projectId, seeded.task.id, run.id);

    const project = await seeded.repo.getProject(seeded.projectId);
    const task = project?.tasks.find((entry) => entry.id === seeded.task.id);
    expect(task?.stage).toBe("result");
    expect(task?.runs[0]?.verdict).toBe("passed");
    expect(project?.reports).toHaveLength(1);
  });

  it("deletes a task and its reports and issues", async () => {
    const seeded = await completedRun(["03"]);
    expect(seeded.project.issues.length).toBeGreaterThan(0);
    await seeded.repo.deleteTask(seeded.projectId, seeded.task.id);
    const project = await seeded.repo.getProject(seeded.projectId);
    expect(project?.tasks.some((entry) => entry.id === seeded.task.id)).toBe(false);
    expect(project?.reports.some((report) => report.taskId === seeded.task.id)).toBe(false);
    expect(project?.issues.some((issue) => issue.taskId === seeded.task.id)).toBe(false);
    expect(project?.activeTaskId).not.toBe(seeded.task.id);
  });

  it("merges a failed case into one issue and enforces the close gate", async () => {
    const seeded = await completedRun(["03"]);
    const issue = seeded.project.issues[0];
    expect(issue).toBeDefined();
    expect(issue?.status).toBe("pending");
    expect(issue?.occurrences).toHaveLength(1);

    await seeded.repo.updateIssue(seeded.projectId, issue?.id as string, {
      status: "in_progress",
      reason: "开始处理",
    });
    await seeded.repo.updateIssue(seeded.projectId, issue?.id as string, {
      status: "retest",
      reason: "已修复，提交复测",
    });

    const plan = await seeded.repo.prepareRerun(seeded.projectId, seeded.task.id, [
      issue?.itemId as string,
    ]);
    const current = await seeded.repo.getProject(seeded.projectId);
    if (!current) {
      throw new Error("project missing");
    }
    await seeded.repo.confirmPlan(seeded.projectId, seeded.task.id, plan.id, {
      operator: "测试用户",
      environment: buildEnvironmentChecks(),
      sideEffects: sideEffectsFor(current),
    });
    const run = await seeded.repo.startRun(seeded.projectId, seeded.task.id, plan.id);
    await finishRun(seeded.repo, seeded.projectId, seeded.task.id, run.id);

    const refreshed = await seeded.repo.getProject(seeded.projectId);
    const refreshedIssue = refreshed?.issues.find((entry) => entry.id === issue?.id);
    if (!refreshed || !refreshedIssue) {
      throw new Error("refreshed issue missing");
    }
    const gate = issueCloseGate(refreshed, refreshedIssue);
    expect(gate.ok).toBe(true);
    if (gate.ok) {
      const closed = await seeded.repo.closeIssue(
        seeded.projectId,
        issue?.id as string,
        gate.run.id,
        "复测通过",
      );
      expect(closed.status).toBe("closed");
      expect(closed.verificationRunId).toBe(gate.run.id);
    }
  });

  it("keeps report versions and only allows opinions on the latest", async () => {
    const seeded = await completedRun(["01"]);
    const runId = seeded.task.runs[0]?.id as string;
    const first = seeded.project.reports[0];
    const second = await seeded.repo.regenerateReport(seeded.projectId, runId);
    expect(second.version).toBe(2);

    const project = await seeded.repo.getProject(seeded.projectId);
    expect(project?.reports.find((entry) => entry.id === first?.id)?.status).toBe("superseded");
    await seeded.repo.saveOpinion(seeded.projectId, second.id, "王工", "可以上线");
    await expect(
      seeded.repo.saveOpinion(seeded.projectId, first?.id as string, "王工", "旧版本"),
    ).rejects.toMatchObject({ code: "CONFLICT" });
  });

  it("records a manual result and regenerates the report", async () => {
    const seeded = await completedRun(["03"]);
    const manual = seeded.task.runs[0]?.cases.find((testCase) => testCase.layer === "L3");
    expect(manual).toBeDefined();
    const updated = await seeded.repo.recordManual(
      seeded.projectId,
      seeded.task.runs[0]?.id as string,
      manual?.id as string,
      {
        result: "passed",
        note: "人工确认错误提示可读",
        evidence: [evidence("manual.log")],
      },
    );
    const manualAfter = updated.cases.find((entry) => entry.id === manual?.id);
    expect(manualAfter?.result).toBe("passed");
    const project = await seeded.repo.getProject(seeded.projectId);
    expect(project?.reports.filter((report) => report.runId === updated.id).length).toBe(2);
  });

  it("ignores a business feature and preserves the choice across reanalysis", async () => {
    const seeded = await seedProject("D:/work/ignore", "忽略验证");
    const updated = await seeded.repo.setFeatureIgnored(seeded.projectId, "数据写入与查询", true);
    expect(updated.map?.ignoredFeatures).toContain("数据写入与查询");
    const analysis = buildProjectAnalysis(updated);
    const refreshed = await seeded.repo.saveAnalysis(seeded.projectId, analysis);
    expect(refreshed.map?.ignoredFeatures).toContain("数据写入与查询");
    const restored = await seeded.repo.setFeatureIgnored(seeded.projectId, "数据写入与查询", false);
    expect(restored.map?.ignoredFeatures).not.toContain("数据写入与查询");
  });

  it("stores and clears a manually registered GitHub cache snapshot", async () => {
    const seeded = await seedProject("D:/work/github", "GitHub 验证");
    const snapshot = {
      repo: "team/service",
      branch: "main",
      commit: "abc123",
      files: ["src/main.py"],
      prs: [],
      checks: ["CI / pytest"],
      deployment: "",
      account: "tester",
      host: "github.com",
      fetchedAt: new Date().toISOString(),
      cached: true,
      unavailableReason: "",
    };
    const saved = await seeded.repo.saveGithubSnapshot(seeded.projectId, snapshot);
    expect(saved.github?.commit).toBe("abc123");
    expect(saved.github?.cached).toBe(true);
    const cleared = await seeded.repo.saveGithubSnapshot(seeded.projectId, null);
    expect(cleared.github).toBeNull();
  });

  it("backs up, clears and restores the workspace", async () => {
    const seeded = await seedProject("D:/work/backup", "备份项目");
    const payload = await seeded.repo.backup();
    expect(payload.result.projects).toBe(1);
    await seeded.repo.clear();
    expect(await seeded.repo.listProjects()).toHaveLength(0);
    expect(payload.result.format).toBe("zip");
    await seeded.repo.restore(payload.data);
    expect(await seeded.repo.listProjects()).toHaveLength(1);
  });

  it("deduplicates the same evidence name and digest in one run", async () => {
    const seeded = await completedRun(["01"]);
    const caseId = seeded.task.runs[0]?.cases[0]?.id ?? "";
    const input = {
      caseId,
      name: "server.log",
      mediaType: "LOG",
      bytes: 12,
      sha256: "b".repeat(64),
      note: "重试不应重复登记",
    };
    await seeded.repo.addEvidence(seeded.projectId, seeded.runId, input);
    await seeded.repo.addEvidence(seeded.projectId, seeded.runId, input);
    const project = await seeded.repo.getProject(seeded.projectId);
    const run = project?.tasks.flatMap((task) => task.runs).find((entry) => entry.id === seeded.runId);
    expect(run?.evidence.filter((entry) => entry.name === "server.log")).toHaveLength(1);
  });

  it("puts time in backup filenames so same-day backups do not overwrite", async () => {
    const seeded = await seedProject("D:/work/backup-time", "备份时间");
    const payload = await seeded.repo.backup();
    expect(payload.result.filename).toMatch(
      /^aitest-backup-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.zip$/,
    );
  });

  it("rejects incomplete backup shapes without touching existing data", async () => {
    const seeded = await seedProject("D:/work/backup-shape", "备份形状");
    const malformed = JSON.stringify({
      schemaVersion: 1,
      savedAt: new Date().toISOString(),
      lastProjectId: null,
      projects: [{ id: "p", name: "缺字段", root: "D:/work/x", tasks: [], issues: [], reports: [] }],
    });
    await expect(seeded.repo.restore(malformed)).rejects.toMatchObject({ code: "FORMAT" });
    expect(await seeded.repo.listProjects()).toHaveLength(1);
  });

  it("rejects unsupported local data instead of migrating silently", async () => {
    localStorage.setItem("aitest.panel.v1", "{not-json");
    const repo = new LocalStorageRepository();
    await expect(repo.listProjects()).rejects.toMatchObject({ code: "FORMAT" });
  });
});
