import { buildProjectAnalysis } from "@/domain/analysis";
import { sideEffectsFor } from "@/domain/derive";
import type { Project, Task } from "@/domain/types";
import { RepositoryError } from "@/repositories/errors";
import { LocalStorageRepository } from "@/repositories/LocalStorageRepository";
import { buildEnvironmentChecks } from "@/services/environmentService";
import { simulateOutcome } from "@/services/execution";

export interface SeededProject {
  repo: LocalStorageRepository;
  projectId: string;
  project: Project;
}

export interface SeededTask extends SeededProject {
  task: Task;
}

export async function seedProject(
  root = "D:/work/demo",
  name = "演示服务",
): Promise<SeededProject> {
  const repo = new LocalStorageRepository();
  const created = await repo.createProject({ root, name });
  const analysis = buildProjectAnalysis(created);
  const project = await repo.saveAnalysis(created.id, analysis);
  return { repo, projectId: project.id, project };
}

export async function seedTask(
  selectedNos: string[] = ["01"],
  root = "D:/work/demo",
  name = "演示服务",
): Promise<SeededTask> {
  const seeded = await seedProject(root, name);
  await seeded.repo.ensureDraftTask(seeded.projectId);
  const withDraft = await seeded.repo.getProject(seeded.projectId);
  const candidate = withDraft?.candidates[0] ?? null;
  if (!candidate) {
    throw new Error("test fixture: analysis did not produce candidates");
  }
  const task = await seeded.repo.createTask(seeded.projectId, {
    goal: candidate.title,
    candidateId: candidate.id,
  });
  const selected = task.acceptanceItems
    .filter((item) => selectedNos.includes(item.no))
    .map((item) => item.id);
  await seeded.repo.selectScope(seeded.projectId, task.id, selected);
  const project = await seeded.repo.getProject(seeded.projectId);
  return {
    ...seeded,
    project: project as Project,
    task: (project as Project).tasks.find((entry) => entry.id === task.id) as Task,
  };
}

export async function seedPlannedTask(
  selectedNos: string[] = ["01"],
  root = "D:/work/demo",
  name = "演示服务",
): Promise<SeededTask> {
  const seeded = await seedTask(selectedNos, root, name);
  const plan = await seeded.repo.generatePlan(seeded.projectId, seeded.task.id);
  const environment = buildEnvironmentChecks();
  await seeded.repo.confirmPlan(seeded.projectId, seeded.task.id, plan.id, {
    operator: "测试用户",
    environment,
    sideEffects: sideEffectsFor(seeded.project),
  });
  const project = await seeded.repo.getProject(seeded.projectId);
  return {
    ...seeded,
    project: project as Project,
    task: (project as Project).tasks.find((entry) => entry.id === seeded.task.id) as Task,
  };
}

export async function seedRunningTask(
  selectedNos: string[] = ["01"],
  root = "D:/work/demo",
  name = "演示服务",
): Promise<SeededTask & { runId: string }> {
  const seeded = await seedPlannedTask(selectedNos, root, name);
  const plan = seeded.task.plans[seeded.task.plans.length - 1];
  if (!plan) {
    throw new Error("test fixture: plan missing");
  }
  const run = await seeded.repo.startRun(seeded.projectId, seeded.task.id, plan.id);
  return { ...seeded, runId: run.id };
}

export async function finishRun(
  repo: LocalStorageRepository,
  projectId: string,
  taskId: string,
  runId: string,
): Promise<void> {
  const project = await repo.getProject(projectId);
  const task = project?.tasks.find((entry) => entry.id === taskId);
  const run = task?.runs.find((entry) => entry.id === runId);
  if (!project || !task || !run) {
    throw new RepositoryError("NOT_FOUND", "fixture run missing");
  }
  const taskRunCount = task.runs.length;
  for (const testCase of run.cases) {
    if (testCase.layer === "L3") {
      await repo.recordRunCase(projectId, runId, testCase.id, {
        result: "manual_pending",
        actual: "等待人工记录",
        message: "",
        errorType: "",
        stackKey: "",
        evidence: [],
      });
      continue;
    }
    const outcome = simulateOutcome(project, task, run, testCase, taskRunCount);
    await repo.recordRunCase(projectId, runId, testCase.id, outcome);
  }
  await repo.completeRun(projectId, runId);
}

export async function completedRun(
  selectedNos: string[] = ["01"],
  root = "D:/work/demo",
  name = "演示服务",
): Promise<SeededTask & { runId: string }> {
  const seeded = await seedRunningTask(selectedNos, root, name);
  await finishRun(seeded.repo, seeded.projectId, seeded.task.id, seeded.runId);
  const project = await seeded.repo.getProject(seeded.projectId);
  return {
    ...seeded,
    project: project as Project,
    task: (project as Project).tasks.find((entry) => entry.id === seeded.task.id) as Task,
    runId: seeded.runId,
  };
}
