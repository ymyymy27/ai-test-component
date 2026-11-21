import { hashText } from "./ids";
import type {
  AcceptanceItem,
  CaseResult,
  Counts,
  EnvironmentCheck,
  Issue,
  Plan,
  PlanCase,
  Project,
  Report,
  Run,
  RunCase,
  Scope,
  Task,
  TaskStage,
  Verdict,
} from "./types";

const RESULT_RANK: Record<CaseResult, number> = {
  failed: 7,
  error: 6,
  blocked: 5,
  not_run: 3,
  manual_pending: 3,
  passed: 2,
};

export const RESULT_ORDER: CaseResult[] = [
  "failed",
  "error",
  "blocked",
  "not_run",
  "manual_pending",
  "passed",
];

export function worstResult(results: CaseResult[]): CaseResult {
  if (results.length === 0) {
    return "not_run";
  }
  return results.reduce((worst, current) => {
    const worstRank = RESULT_RANK[worst] ?? 0;
    const currentRank = RESULT_RANK[current] ?? 0;
    return currentRank > worstRank ? current : worst;
  }, results[0] as CaseResult);
}

export function projectCheckBlocks(run: Run, dependency: "build" | "service"): boolean {
  const keys = dependency === "build" ? ["build", "l1-build"] : ["service", "entry", "smoke"];
  return run.l1.some(
    (check) => keys.includes(check.key) && ["failed", "blocked", "error"].includes(check.result),
  );
}

export function effectiveCaseResult(runCase: RunCase, run: Run): CaseResult {
  if (runCase.result === "passed" && !runCase.expectationVerified) {
    return "blocked";
  }
  if (
    runCase.result === "passed" &&
    runCase.dependsOn &&
    projectCheckBlocks(run, runCase.dependsOn)
  ) {
    return "blocked";
  }
  return runCase.result;
}

export function itemOutcome(run: Run, itemId: string): CaseResult {
  const cases = run.cases.filter(
    (testCase) => (testCase.caseItemIds ?? [testCase.itemId]).includes(itemId),
  );
  if (cases.length === 0) {
    return "not_run";
  }
  return worstResult(cases.map((testCase) => effectiveCaseResult(testCase, run)));
}

/**
 * 展示用逐项结论：宿主运行以 CLI 的 item_outcomes 为准（映射进 run.items），
 * 本地模拟才回退到前端重算。
 */
export function displayItemOutcome(run: Run, itemId: string): CaseResult {
  if (!run.simulated) {
    const item = run.items.find((entry) => entry.itemId === itemId);
    if (item) {
      return item.outcome;
    }
  }
  return itemOutcome(run, itemId);
}

export function compareItemNo(first: string, second: string): number {
  const firstNo = Number.parseInt(first.replace(/\D+/g, ""), 10);
  const secondNo = Number.parseInt(second.replace(/\D+/g, ""), 10);
  if (Number.isFinite(firstNo) && Number.isFinite(secondNo) && firstNo !== secondNo) {
    return firstNo - secondNo;
  }
  return first.localeCompare(second);
}

export function recountRun(run: Run): Run {
  const selected = run.scope.acceptanceItemIds.length;
  const items = run.items.map((item) => ({
    ...item,
    outcome: itemOutcome(run, item.itemId),
    caseIds: run.cases.filter((testCase) => testCase.itemId === item.itemId).map((c) => c.id),
  }));
  const outcomes = items.map((item) => item.outcome);
  const covered = items.filter((item) =>
    run.cases.some(
      (testCase) =>
        testCase.itemId === item.itemId &&
        !["not_run", "manual_pending"].includes(effectiveCaseResult(testCase, run)),
    ),
  ).length;
  const counts: Counts = {
    selected,
    applicable: run.scope.coverage.applicable,
    covered,
    passed: outcomes.filter((outcome) => outcome === "passed").length,
    failed: outcomes.filter((outcome) => outcome === "failed").length,
    incomplete: outcomes.filter((outcome) => outcome !== "passed" && outcome !== "failed").length,
  };
  return { ...run, items, counts, verdict: verdictForRun({ ...run, items, counts }) };
}

export function verdictForRun(run: Run): Verdict {
  const selected = run.scope.acceptanceItemIds.length;
  if (selected === 0) {
    return "not_applicable";
  }
  const l1Failed = run.l1.some((check) => ["failed", "blocked", "error"].includes(check.result));
  if (l1Failed) {
    return "failed";
  }
  const outcomes = run.items.map((item) => itemOutcome(run, item.itemId));
  if (outcomes.includes("failed")) {
    return "failed";
  }
  if (outcomes.some((outcome) => outcome !== "passed")) {
    return "incomplete";
  }
  return "passed";
}

export function conclusionForRun(run: Run): string {
  const counts = run.counts;
  if (counts.selected === 0) {
    return "本次没有纳入验收项，项目级检查单独展示，不计入验收项覆盖。";
  }
  const unselected = Math.max(0, counts.applicable - counts.selected);
  const base = `本次 ${counts.selected} 项中，通过 ${counts.passed} 项、失败 ${counts.failed} 项、未完成 ${counts.incomplete} 项。`;
  if (run.l1.some((check) => ["failed", "blocked", "error"].includes(check.result))) {
    return `${base}项目级检查未通过，本次结论为失败；项目级检查不计入验收项覆盖。`;
  }
  if (unselected > 0) {
    const suffix = "未纳入的验收项不计入本次结论。";
    if (run.verdict === "passed") {
      return `${base}未纳入 ${unselected} 项，${suffix}可以表述为“本次范围通过”，但不能扩大为整个项目通过。`;
    }
    return `${base}未纳入 ${unselected} 项，${suffix}`;
  }
  if (run.verdict === "passed") {
    return `本次 ${counts.selected} 项全部通过。`;
  }
  return base;
}

export function sortedRunItems(run: Run): Run["items"] {
  return [...run.items].sort((first, second) => {
    const firstRank = RESULT_RANK[displayItemOutcome(run, first.itemId)] ?? 0;
    const secondRank = RESULT_RANK[displayItemOutcome(run, second.itemId)] ?? 0;
    if (firstRank !== secondRank) {
      return secondRank - firstRank;
    }
    return first.no.localeCompare(second.no);
  });
}

export function problemRunItems(run: Run): Run["items"] {
  return sortedRunItems(run).filter((item) =>
    ["failed", "blocked", "error"].includes(displayItemOutcome(run, item.itemId)),
  );
}

export function unverifiedCaseIds(run: Run): string[] {
  return run.cases
    .filter((testCase) => !testCase.expectationVerified)
    .map((testCase) => testCase.id);
}

export function layersForRun(run: Run): Record<
  "L1" | "L2" | "L3",
  {
    total: number;
    passed: number;
    failed: number;
    incomplete: number;
  }
> {
  const result = {
    L1: { total: 0, passed: 0, failed: 0, incomplete: 0 },
    L2: { total: 0, passed: 0, failed: 0, incomplete: 0 },
    L3: { total: 0, passed: 0, failed: 0, incomplete: 0 },
  };
  run.cases.forEach((testCase) => {
    const bucket = result[testCase.layer];
    bucket.total += 1;
    const outcome = effectiveCaseResult(testCase, run);
    if (outcome === "passed") {
      bucket.passed += 1;
    } else if (outcome === "failed") {
      bucket.failed += 1;
    } else {
      bucket.incomplete += 1;
    }
  });
  return result;
}

export function allRuns(project: Project): Run[] {
  return project.tasks
    .flatMap((task) => task.runs)
    .sort((first, second) => second.startedAt.localeCompare(first.startedAt));
}

export function allReports(project: Project): Report[] {
  return [...project.reports].sort((first, second) => {
    if (first.runId === second.runId) {
      return second.version - first.version;
    }
    return second.generatedAt.localeCompare(first.generatedAt);
  });
}

export function reportsForRun(project: Project, runId: string): Report[] {
  return project.reports
    .filter((report) => report.runId === runId)
    .sort((first, second) => second.version - first.version);
}

export function latestReportForRun(project: Project, runId: string): Report | null {
  return reportsForRun(project, runId)[0] ?? null;
}

export function findTask(project: Project, taskId: string): Task | null {
  return project.tasks.find((task) => task.id === taskId) ?? null;
}

export function activeTask(project: Project): Task | null {
  return project.activeTaskId ? findTask(project, project.activeTaskId) : null;
}

export function runningTask(project: Project): Task | null {
  return project.tasks.find((task) => task.stage === "running") ?? null;
}

export function projectHasRunningRun(project: Project): boolean {
  return project.tasks.some((task) => task.runs.some((run) => run.status === "running"));
}

export function anyRunningProject(projects: Project[]): Project | null {
  return projects.find((project) => projectHasRunningRun(project)) ?? null;
}

export function currentPlan(task: Task): Plan | null {
  if (!task.currentPlanId) {
    return null;
  }
  return task.plans.find((plan) => plan.id === task.currentPlanId) ?? null;
}

export function currentRun(task: Task): Run | null {
  if (!task.currentRunId) {
    return null;
  }
  return task.runs.find((run) => run.id === task.currentRunId) ?? null;
}

export function planForStage(task: Task): Plan | null {
  return currentPlan(task);
}

export function taskRunCount(task: Task): number {
  return task.runs.length;
}

export function runDurationMs(run: Run): number {
  if (run.status === "running") {
    return Math.max(0, Date.now() - new Date(run.startedAt).getTime());
  }
  return Math.max(0, run.elapsedMs);
}

export function featureGroups(task: Task): Array<{
  name: string;
  items: AcceptanceItem[];
  modules: string[];
}> {
  const groups = new Map<string, AcceptanceItem[]>();
  task.acceptanceItems
    .filter((item) => item.observable && item.status === "active")
    .forEach((item) => {
      const list = groups.get(item.featureName) ?? [];
      list.push(item);
      groups.set(item.featureName, list);
    });
  return [...groups.entries()].map(([name, items]) => ({
    name,
    items,
    modules: [...new Set(items.map((item) => item.moduleName).filter(Boolean))],
  }));
}

export function applicableItems(task: Task): AcceptanceItem[] {
  return task.acceptanceItems.filter((item) => item.observable && item.status === "active");
}

export function pendingItems(task: Task): AcceptanceItem[] {
  return task.acceptanceItems.filter((item) => !item.observable || item.status === "pending");
}

export function selectedItems(task: Task, selectedIds: string[]): AcceptanceItem[] {
  const selected = new Set(selectedIds);
  return applicableItems(task).filter((item) => selected.has(item.id));
}

export function unselectedItems(task: Task, selectedIds: string[]): AcceptanceItem[] {
  const selected = new Set(selectedIds);
  return applicableItems(task).filter((item) => !selected.has(item.id));
}

export function moduleCounts(items: AcceptanceItem[]): Array<{ name: string; count: number }> {
  const counts = new Map<string, number>();
  items.forEach((item) => {
    counts.set(item.moduleName, (counts.get(item.moduleName) ?? 0) + 1);
  });
  return [...counts.entries()].map(([name, count]) => ({ name, count }));
}

export function unselectedByModule(
  task: Task,
  selectedIds: string[],
): Array<{ moduleId: string; moduleName: string; count: number }> {
  const counts = new Map<string, { moduleId: string; moduleName: string; count: number }>();
  unselectedItems(task, selectedIds).forEach((item) => {
    const key = item.moduleId ?? item.moduleName;
    const entry = counts.get(key) ?? {
      moduleId: item.moduleId ?? "",
      moduleName: item.moduleName,
      count: 0,
    };
    entry.count += 1;
    counts.set(key, entry);
  });
  return [...counts.values()];
}

export function openIssues(project: Project): Issue[] {
  return project.issues.filter((issue) => issue.status !== "closed");
}

export function issueSignatureFor(testCase: RunCase): string {
  return hashText(`${testCase.caseId}|${testCase.errorType}|${testCase.stackKey}`);
}

export function lastIssueOccurrence(issue: Issue): string {
  if (issue.occurrences.length === 0) {
    return issue.createdAt;
  }
  return issue.occurrences
    .map((occurrence) => occurrence.time)
    .sort((first, second) => second.localeCompare(first))[0] as string;
}

export interface IssueCloseCandidate {
  run: Run;
  runCase: RunCase;
}

export function findIssueCloseCandidate(
  project: Project,
  issue: Issue,
): IssueCloseCandidate | null {
  const task = findTask(project, issue.taskId);
  if (!task) {
    return null;
  }
  const lastAt = lastIssueOccurrence(issue);
  const candidates = task.runs
    .filter((run) => run.status === "completed")
    .filter((run) => run.id !== issue.runId)
    .filter((run) => run.startedAt > lastAt)
    .sort((first, second) => first.startedAt.localeCompare(second.startedAt));
  for (const run of candidates) {
    const runCase =
      run.cases.find((testCase) => testCase.caseId === issue.caseId) ??
      run.cases.find((testCase) => testCase.itemId === issue.itemId);
    if (!runCase) {
      continue;
    }
    if (effectiveCaseResult(runCase, run) !== "passed") {
      continue;
    }
    const hasEvidence = runCase.evidence.length > 0;
    if (!hasEvidence) {
      continue;
    }
    return { run, runCase };
  }
  return null;
}

export type IssueGate =
  { ok: true; run: Run; runCase: RunCase; reason: string } | { ok: false; reason: string };

export function issueCloseGate(project: Project, issue: Issue): IssueGate {
  if (issue.status === "closed") {
    return { ok: false, reason: "问题已经关闭；如需重新验证，先重新打开。" };
  }
  if (issue.status !== "retest") {
    return { ok: false, reason: "问题需要先提交复测，才能绑定新运行关闭。" };
  }
  const candidate = findIssueCloseCandidate(project, issue);
  if (!candidate) {
    return {
      ok: false,
      reason:
        "还没有满足条件的新运行：运行要晚于最后一次发生、包含该用例、有效结果为通过，并且附有证据。",
    };
  }
  return {
    ok: true,
    run: candidate.run,
    runCase: candidate.runCase,
    reason: "已找到包含该用例且通过的新运行，可以关闭。",
  };
}

export function taskContract(task: Task): string {
  const items = task.acceptanceItems
    .filter((item) => item.status === "active")
    .map((item) => ({
      id: item.id,
      text: item.text,
      status: item.status,
      observable: item.observable,
      moduleId: item.moduleId,
    }));
  return hashText(JSON.stringify({ intent: task.intent, items }));
}

export function sourceSignature(project: Project, task: Task): string {
  const config = JSON.stringify(project.config);
  const delivery = task.delivery?.commit ?? "";
  return hashText(
    JSON.stringify({
      project: project.id,
      root: project.root,
      taskContract: taskContract(task),
      rules: project.rulesVersionId,
      config,
      delivery,
    }),
  );
}

export function confirmationFingerprint(
  plan: Plan,
  selectedIds: string[],
  environment: Record<string, string>,
  sideEffects: string[],
  dependencies: string[],
): string {
  return hashText(
    JSON.stringify({
      planId: plan.id,
      revision: plan.revision,
      selected: [...selectedIds].sort(),
      environment,
      sideEffects: [...sideEffects].sort(),
      dependencies: [...dependencies].sort(),
    }),
  );
}

export function environmentSummary(checks: EnvironmentCheck[]): Record<string, string> {
  const summary: Record<string, string> = {};
  checks.forEach((check) => {
    summary[check.key] = check.ok ? "ok" : "missing";
  });
  return summary;
}

export function sideEffectsFor(project: Project): string[] {
  const effects: string[] = [];
  if (project.config.l1?.build) {
    effects.push(`执行构建命令：${project.config.l1.build}`);
  }
  if (project.config.services?.healthUrl || project.config.services?.baseUrl) {
    effects.push("启动本地服务，运行结束后停止");
  }
  if (project.config.runners?.test) {
    effects.push(`运行测试命令：${project.config.runners.test}`);
  }
  if (project.config.data?.cleanupCommand) {
    effects.push("可能按运行清理测试数据");
  }
  effects.push("只读取项目文件；证据、报告与记录写入本机工作空间");
  return effects;
}

export function scopeForSelection(
  task: Task,
  selectedIds: string[],
  preset: "all_applicable" | "selected",
): Scope {
  const applicable = applicableItems(task);
  return {
    preset,
    moduleIds: [
      ...new Set(selectedItems(task, selectedIds).map((item) => item.moduleId ?? "")),
    ].filter(Boolean),
    acceptanceItemIds: selectedIds,
    coverage: { selected: selectedIds.length, applicable: applicable.length },
  };
}

export function planCasesForItems(cases: PlanCase[], selectedIds: string[]): PlanCase[] {
  const selected = new Set(selectedIds);
  return cases.filter((testCase) => selected.has(testCase.itemId));
}

export function projectStatus(project: Project): { tone: string; label: string } {
  if (project.analysisStatus === "analyzing") {
    return { tone: "running", label: "分析中" };
  }
  if (projectHasRunningRun(project)) {
    return { tone: "running", label: "运行中" };
  }
  if (project.tasks.filter((task) => task.stage !== "fresh").length === 0) {
    return { tone: "idle", label: "还没有任务" };
  }
  if (project.tasks.some((task) => task.runs.some((run) => run.verdict === "failed"))) {
    return { tone: "fail", label: "有失败" };
  }
  if (openIssues(project).length > 0) {
    return { tone: "warn", label: "待处理" };
  }
  return { tone: "ok", label: "已就绪" };
}

export function stageForDisplay(task: Task): TaskStage {
  return task.stage;
}
