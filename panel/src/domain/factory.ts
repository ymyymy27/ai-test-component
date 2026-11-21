import { buildFailureAnalysis, buildMockSummary } from "./analysis";
import {
  applicableItems,
  conclusionForRun,
  effectiveCaseResult,
  confirmationFingerprint,
  featureGroups,
  issueSignatureFor,
  layersForRun,
  sourceSignature,
  taskContract,
  unselectedByModule,
  unverifiedCaseIds,
} from "./derive";
import { hashText, isoNow, newId } from "./ids";
import type {
  Candidate,
  Confirmation,
  Delivery,
  EnvironmentCheck,
  Issue,
  Plan,
  PlanCase,
  Project,
  ProjectCheck,
  ProjectConfig,
  QuickCheck,
  Report,
  RuleVersion,
  Run,
  RunCase,
  Scope,
  Task,
  TemplateResult,
} from "./types";

export function defaultConfig(): ProjectConfig {
  return {
    l1: {},
    services: {},
    runners: {},
    evidence: { singleFileMb: 20, mediaMb: 500, video: false },
    link: { logPath: "", query: "" },
    data: { cleanupCommand: "", dataPrefix: "" },
    model: { name: "" },
  };
}

export function defaultRule(projectId: string): RuleVersion {
  return {
    id: newId("rule"),
    projectId,
    name: "默认检查",
    version: "1",
    stepsMd: "按项目 README、启动脚本与已有测试执行主流程检查。",
    aiRules: ["预期必须来自验收项原文；无法引用时标未核实。"],
    evidenceRequirements: ["运行日志", "关键页面截图或接口响应"],
    revision: 1,
    createdAt: isoNow(),
  };
}

export function createProjectRecord(input: {
  id?: string;
  name: string;
  root: string;
  goal?: string;
}): Project {
  const projectId = input.id ?? newId("proj");
  const rule = defaultRule(projectId);
  const now = isoNow();
  return {
    id: projectId,
    name: input.name,
    root: input.root,
    goal: input.goal?.trim() ?? "",
    createdAt: now,
    updatedAt: now,
    analysisStatus: "analyzing",
    analysisStartedAt: now,
    analysisError: "",
    candidates: [],
    modules: [],
    map: null,
    catalog: [],
    tasks: [],
    activeTaskId: null,
    rules: [rule],
    rulesVersionId: rule.id,
    config: defaultConfig(),
    github: null,
    quickChecks: [],
    issues: [],
    reports: [],
    archived: false,
  };
}

export function createDraftTask(project: Project): Task {
  const now = isoNow();
  return {
    id: newId("task"),
    projectId: project.id,
    title: "",
    goal: "",
    intent: "business",
    origin: "goal",
    sourceMapRevision: project.map?.revision ?? 0,
    scope: "",
    inputs: "",
    outputs: "",
    preconditions: [],
    executor: "",
    reviewer: "",
    acceptanceItems: [],
    recommendedItemIds: [],
    selectedItemIds: [],
    stage: "fresh",
    previousStage: null,
    revision: 1,
    createdAt: now,
    updatedAt: now,
    plans: [],
    currentPlanId: null,
    runs: [],
    currentRunId: null,
    delivery: null,
    templates: [],
  };
}

function inferIntent(goal: string): "smoke" | "business" {
  return /冒烟|能否启动|能否运行|跑通主流程/.test(goal) ? "smoke" : "business";
}

export function confirmDraftTask(
  project: Project,
  draft: Task,
  input: { goal: string; candidate: Candidate | null; origin?: Task["origin"] },
): Task {
  const ignored = new Set(project.map?.ignoredFeatures ?? []);
  const catalog = project.catalog
    .filter((item) => !ignored.has(item.featureName))
    .map((item) => ({ ...item }));
  const observable = catalog.filter((item) => item.observable && item.status === "active");
  const recommended = input.candidate
    ? observable
        .filter((item) => input.candidate?.featureNames.includes(item.featureName))
        .map((item) => item.id)
    : observable.map((item) => item.id);
  const title = input.candidate?.title ?? input.goal.trim();
  const now = isoNow();
  return {
    ...draft,
    title: title.length > 80 ? `${title.slice(0, 78)}…` : title,
    goal: input.goal.trim(),
    intent: input.candidate?.intent ?? inferIntent(input.goal),
    origin: input.origin ?? "goal",
    sourceMapRevision: project.map?.revision ?? 0,
    acceptanceItems: catalog,
    recommendedItemIds: recommended.length > 0 ? recommended : observable.map((item) => item.id),
    selectedItemIds: recommended.length > 0 ? recommended : observable.map((item) => item.id),
    stage: "scope",
    previousStage: null,
    revision: 1,
    updatedAt: now,
  };
}

function caseForItem(item: Task["acceptanceItems"][number]): PlanCase[] {
  const common = {
    itemId: item.id,
    extraItemIds: [],
    preconditions: [] as string[],
    steps: [] as string[],
    expectationSource: `验收项 ${item.no}：${item.text}`,
    expectationVerified: true,
    mockAllowed: true,
    importance: "medium" as const,
    scriptName: null,
    scriptContent: null,
  };
  if (item.no === "01") {
    return [
      {
        ...common,
        id: newId("case"),
        layer: "L2",
        title: "启动并完成一次完整操作",
        expected: item.text,
        verifyMethod: "command: 启动项目并执行一次主流程",
        dependencies: ["启动入口"],
        dependsOn: "build",
        manual: false,
      },
    ];
  }
  if (item.no === "03") {
    return [
      {
        ...common,
        id: newId("case"),
        layer: "L2",
        title: "异常输入被拒绝并返回可读提示",
        expected: item.text,
        verifyMethod: "command: 提交空字段与超长字段",
        dependencies: [],
        dependsOn: null,
        manual: false,
      },
      {
        ...common,
        id: newId("case"),
        layer: "L3",
        title: "人工确认错误提示可读",
        expected: "错误提示能让用户知道哪里不对，并给出下一步。",
        expectationSource: "验收项 03 的可读性补充",
        verifyMethod: "manual: 人工检查错误提示",
        dependencies: [],
        dependsOn: null,
        manual: true,
      },
    ];
  }
  return [
    {
      ...common,
      id: newId("case"),
      layer: "L2",
      title: item.no === "02" ? "写入后可以在列表中查询" : "重复执行不产生重复数据",
      expected: item.text,
      verifyMethod:
        item.no === "02"
          ? "command: 写入一条记录并刷新列表"
          : "command: 连续执行两次同一操作并比较记录数",
      dependencies: [],
      dependsOn: null,
      manual: false,
    },
  ];
}

export function buildPlan(
  project: Project,
  task: Task,
  selectedIds: string[],
  rulesVersionId: string | null,
): Plan {
  const selectedSet = new Set(selectedIds);
  const items = applicableItems(task).filter((item) => selectedSet.has(item.id));
  const cases = items.flatMap((item) => caseForItem(item));
  const applicable = applicableItems(task).length;
  const scope: Scope = {
    preset: selectedIds.length === applicable ? "all_applicable" : "selected",
    moduleIds: [...new Set(items.map((item) => item.moduleId ?? ""))].filter(Boolean),
    acceptanceItemIds: selectedIds,
    coverage: { selected: selectedIds.length, applicable },
  };
  const plan: Plan = {
    id: newId("plan"),
    projectId: project.id,
    taskId: task.id,
    no: Math.max(0, ...task.plans.map((entry) => entry.no)) + 1,
    revision: 1,
    status: "published",
    taskRevision: task.revision,
    taskContract: taskContract(task),
    createdAt: isoNow(),
    scope,
    applicableCount: applicable,
    rulesVersionId,
    cases,
    templates: [...task.templates],
    unselectedByModule: unselectedByModule(task, selectedIds),
    warnings:
      selectedIds.length < applicable
        ? [`未纳入 ${applicable - selectedIds.length} 项；结论只对本次范围成立。`]
        : [],
    sourceSignature: sourceSignature(project, task),
    confirmation: null,
    stale: false,
    staleReason: "",
  };
  return plan;
}

export function buildConfirmation(
  project: Project,
  plan: Plan,
  environment: EnvironmentCheck[],
  sideEffects: string[],
  operator: string,
): Confirmation {
  void project;
  const environmentValues: Record<string, string> = {};
  environment.forEach((check) => {
    environmentValues[check.key] = check.ok ? "ok" : "missing";
  });
  const selectedIds = plan.scope.acceptanceItemIds;
  return {
    id: newId("conf"),
    planId: plan.id,
    planRevision: plan.revision,
    acceptanceItemIds: [...selectedIds],
    environment: environmentValues,
    credentials: { source: "本机本地存储", provided: "none" },
    sideEffects,
    dependencies: [...new Set(plan.cases.flatMap((testCase) => testCase.dependencies))],
    fingerprint: confirmationFingerprint(
      plan,
      selectedIds,
      environmentValues,
      sideEffects,
      plan.cases.flatMap((testCase) => testCase.dependencies),
    ),
    confirmedBy: operator,
    confirmedAt: isoNow(),
  };
}

export function buildProjectChecks(project: Project, includeService: boolean): ProjectCheck[] {
  const now = isoNow();
  const checks: ProjectCheck[] = [
    {
      key: "build",
      name: "构建",
      method: project.config.l1?.build || "自动检测构建命令",
      result: "passed",
      detail: "本地模拟：构建退出码 0",
      time: now,
    },
    {
      key: "entry",
      name: "启动入口",
      method: project.config.l1?.entry || "自动检测启动入口",
      result: "passed",
      detail: "本地模拟：入口可加载",
      time: now,
    },
    {
      key: "smoke",
      name: "最小测试",
      method: project.config.l1?.smokeCommand || "运行已有最小测试",
      result: "passed",
      detail: "本地模拟：最小测试通过",
      time: now,
    },
  ];
  if (includeService && (project.config.services?.healthUrl || project.config.services?.baseUrl)) {
    checks.push({
      key: "service",
      name: "本地服务",
      method: project.config.services.healthUrl || project.config.services.baseUrl || "",
      result: "passed",
      detail: "本地模拟：健康检查返回 200",
      time: now,
    });
  }
  return checks;
}

function toRunCase(testCase: PlanCase): RunCase {
  return {
    id: newId("rc"),
    caseId: testCase.id,
    itemId: testCase.itemId,
    title: testCase.title,
    method: testCase.verifyMethod,
    layer: testCase.layer,
    expected: testCase.expected,
    expectationVerified: testCase.expectationVerified,
    dependsOn: testCase.dependsOn,
    actual: "",
    result: "not_run",
    evidence: [],
    message: "",
    errorType: "",
    stackKey: "",
    attempts: 0,
    manualSteps: [],
  };
}

export function buildRun(
  project: Project,
  task: Task,
  plan: Plan,
  confirmation: Confirmation,
): Run {
  const now = isoNow();
  const cases = plan.cases.map(toRunCase);
  if (cases.length > 0) {
    (cases[0] as RunCase).result = "not_run";
  }
  const items = plan.scope.acceptanceItemIds.map((itemId) => {
    const item = task.acceptanceItems.find((entry) => entry.id === itemId);
    return {
      itemId,
      no: item?.no ?? "",
      text: item?.text ?? "验收项",
      module: item?.moduleName ?? "",
      feature: item?.featureName ?? "",
      outcome: "not_run" as const,
      caseIds: cases
        .filter((testCase) => testCase.itemId === itemId)
        .map((testCase) => testCase.id),
    };
  });
  const run: Run = {
    id: newId("run"),
    projectId: project.id,
    taskId: task.id,
    taskTitle: task.title,
    planId: plan.id,
    kind: "task",
    status: "running",
    startedAt: now,
    finishedAt: "",
    elapsedMs: 0,
    confirmedBy: confirmation.confirmedBy,
    confirmationId: confirmation.id,
    confirmationFingerprint: confirmation.fingerprint,
    scope: plan.scope,
    cases,
    l1: buildProjectChecks(project, true),
    items,
    evidence: [],
    link: null,
    verdict: "not_applicable",
    conclusion: "",
    counts: {
      selected: plan.scope.acceptanceItemIds.length,
      applicable: plan.applicableCount,
      covered: 0,
      passed: 0,
      failed: 0,
      incomplete: plan.scope.acceptanceItemIds.length,
    },
    reportIds: [],
    currentCaseId: cases[0]?.id ?? "",
    simulated: true,
    heartbeatAt: now,
    cancelRequested: false,
  };
  return run;
}

export function buildQuickCheckRun(project: Project): QuickCheck {
  return {
    id: newId("quick"),
    projectId: project.id,
    startedAt: isoNow(),
    finishedAt: "",
    status: "running",
    confirmedBy: "本机用户",
    checks: buildProjectChecks(project, false).map((check) => ({
      ...check,
      result: "not_run",
      detail: "等待检查",
    })),
    simulated: true,
  };
}

export function completeQuickCheck(project: Project, quick: QuickCheck): QuickCheck {
  return {
    ...quick,
    status: "completed",
    finishedAt: isoNow(),
    checks: buildProjectChecks(project, false),
  };
}

export function createDelivery(task: Task): Delivery {
  return {
    id: newId("dlv"),
    taskId: task.id,
    done: [],
    notDone: [],
    changedModules: [],
    interfaceChanges: [],
    runCommands: [],
    testData: "",
    dependencies: [],
    mocks: [],
    selfTestEvidence: [],
    commit: "",
    complete: false,
    revision: 1,
    updatedAt: isoNow(),
  };
}

export function draftDelivery(project: Project, task: Task): Delivery {
  const delivery = createDelivery(task);
  return {
    ...delivery,
    done: task.acceptanceItems
      .filter((item) => item.observable)
      .slice(0, 2)
      .map((item) => `覆盖：${item.text}`),
    changedModules: project.modules.slice(0, 2).map((module) => module.name),
    runCommands: project.config.l1?.build ? [project.config.l1.build] : [],
    testData: "本机测试数据",
    dependencies: [],
    mocks: [],
    selfTestEvidence: [],
    commit: "",
    complete: false,
  };
}

export function buildReport(project: Project, task: Task, run: Run, version: number): Report {
  const plan = task.plans.find((entry) => entry.id === run.planId) ?? null;
  const issueIds = project.issues
    .filter(
      (issue) =>
        issue.runId === run.id ||
        issue.occurrences.some((occurrence) => occurrence.runId === run.id),
    )
    .map((issue) => issue.id);
  const evidence = new Map<string, Run["cases"][number]["evidence"][number]>();
  run.evidence.forEach((item) => evidence.set(item.id, item));
  run.cases.forEach((testCase) => {
    testCase.evidence.forEach((item) => evidence.set(item.id, item));
  });
  const reportedItemIds = new Set(run.items.map((item) => item.itemId));
  const uncovered = applicableItems(task)
    .filter((item) => !reportedItemIds.has(item.id))
    .map((item) => `${item.no} ${item.text}`);
  const unverified = unverifiedCaseIds(run).map((caseId) => {
    const testCase = run.cases.find((entry) => entry.id === caseId);
    return testCase?.title ?? caseId;
  });
  const rule = project.rules.find((entry) => entry.id === project.rulesVersionId) ?? null;
  const modules = Object.entries(
    run.items.reduce<Record<string, number>>((accumulator, item) => {
      const key = item.module || "未归类";
      accumulator[key] = (accumulator[key] ?? 0) + 1;
      return accumulator;
    }, {}),
  ).map(([name, count]) => ({ name, count }));
  const templateResults: TemplateResult[] = [...task.templates];
  const layers = layersForRun(run);
  if (run.l1.length > 0) {
    // 项目级检查不一定是 run.cases，分层计数必须合并 run.l1。
    layers.L1 = {
      total: run.l1.length,
      passed: run.l1.filter((check) => check.result === "passed").length,
      failed: run.l1.filter((check) => check.result === "failed" || check.result === "error")
        .length,
      incomplete: run.l1.filter(
        (check) => !["passed", "failed", "error"].includes(check.result),
      ).length,
    };
  }
  const summaryParts = [
    `本次运行包含 ${run.cases.length} 条用例，覆盖 ${run.items.length} 个验收项。`,
    `失败 ${run.counts.failed} 项，未完成 ${run.counts.incomplete} 项，待人工 ${
      run.cases.filter((testCase) => testCase.result === "manual_pending").length
    } 条。`,
    "本地模拟运行未连接真实依赖，未产生真实链路通过。",
  ];
  return {
    id: newId("rep"),
    projectId: project.id,
    taskId: task.id,
    runId: run.id,
    version,
    generatedAt: isoNow(),
    verdict: run.verdict,
    counts: run.counts,
    conclusion: conclusionForRun(run),
    coverage: {
      selected: run.counts.selected,
      applicable: run.counts.applicable,
      covered: run.counts.covered,
      unselected: Math.max(0, run.counts.applicable - run.counts.selected),
    },
    itemDetails: run.items.map((item) => ({
      itemId: item.itemId,
      no: item.no,
      text: item.text,
      outcome: item.outcome,
      caseIds: item.caseIds,
      selected: true,
    })),
    caseDetails: run.cases.map((testCase) => {
      const item = run.items.find((entry) => entry.itemId === testCase.itemId);
      return {
        caseId: testCase.id,
        itemId: testCase.itemId,
        itemNo: item?.no ?? "",
        title: testCase.title,
        method: testCase.method,
        expected: testCase.expected,
        actual: testCase.actual,
        result: effectiveCaseResult(testCase, run),
        evidence: testCase.evidence,
        analysis: null,
      };
    }),
    l1: run.l1,
    layers,
    issues: issueIds,
    mock: buildMockSummary(run),
    analysis: buildFailureAnalysis(run),
    link: run.link,
    templates: templateResults,
    modules,
    unmapped: [],
    uncoveredItems: uncovered,
    unverifiedItems: unverified,
    planWarnings: plan?.warnings ?? [],
    evidenceIndex: [...evidence.values()],
    source: {
      runTime: run.startedAt,
      planLabel: plan ? `计划 ${plan.no} · 第 ${plan.revision} 版` : "计划快照缺失",
      rulesLabel: rule ? `${rule.name} v${rule.version}` : "未绑定规则",
      commit: task.delivery?.commit || "未记录",
      model: "未接入",
      github: project.github
        ? `${project.github.repo} · ${project.github.commit} · 缓存快照，未确认最新`
        : "未连接",
    },
    summary: summaryParts.join(""),
    summarySource: "fallback",
    opinion: null,
    status: "final",
  };
}

export function createIssueFromCase(
  project: Project,
  task: Task,
  run: Run,
  runCase: RunCase,
): Issue {
  const now = isoNow();
  const item = task.acceptanceItems.find((entry) => entry.id === runCase.itemId);
  return {
    id: newId("issue"),
    projectId: project.id,
    taskId: task.id,
    planId: run.planId,
    runId: run.id,
    itemId: runCase.itemId,
    caseId: runCase.caseId,
    signature: issueSignatureFor(runCase),
    title: `${runCase.title} 未通过`,
    expected: runCase.expected,
    actual: runCase.actual || "实际结果未记录",
    steps: [
      `执行用例：${runCase.title}`,
      `检查方式：${runCase.method || "自动检查"}`,
      `验收项：${item ? `${item.no} ${item.text}` : runCase.itemId}`,
    ],
    severity: item?.no === "03" ? "P1" : "P2",
    status: "pending",
    disposition: null,
    reason: "",
    fixVersion: "",
    source: "auto",
    occurrences: [
      {
        runId: run.id,
        caseId: runCase.caseId,
        time: now,
        result: runCase.result,
        note: runCase.actual || "运行记录",
        evidenceRefs: runCase.evidence.map((entry) => entry.name),
      },
    ],
    evidenceRefs: runCase.evidence.map((entry) => entry.name),
    verificationRunId: "",
    unstable: false,
    createdAt: now,
    updatedAt: now,
  };
}

export function createManualIssue(
  project: Project,
  input: {
    taskId: string;
    itemId: string;
    title: string;
    expected: string;
    actual: string;
    steps: string[];
    severity: Issue["severity"];
  },
): Issue {
  const now = isoNow();
  const task = project.tasks.find((entry) => entry.id === input.taskId) ?? null;
  const plan = task?.currentPlanId
    ? (task.plans.find((entry) => entry.id === task.currentPlanId) ?? null)
    : null;
  const run = task?.currentRunId
    ? (task.runs.find((entry) => entry.id === task.currentRunId) ?? null)
    : null;
  return {
    id: newId("issue"),
    projectId: project.id,
    taskId: input.taskId,
    planId: plan?.id ?? "",
    runId: run?.id ?? "",
    itemId: input.itemId,
    caseId: "",
    signature: hashText(`${input.taskId}|manual|${input.itemId}|${input.title}`),
    title: input.title.trim(),
    expected: input.expected.trim(),
    actual: input.actual.trim(),
    steps: input.steps,
    severity: input.severity,
    status: "pending",
    disposition: null,
    reason: "",
    fixVersion: "",
    source: "manual",
    occurrences: [],
    evidenceRefs: [],
    verificationRunId: "",
    unstable: false,
    createdAt: now,
    updatedAt: now,
  };
}

export function createRuleVersion(
  projectId: string,
  input: {
    name: string;
    version: string;
    stepsMd: string;
    aiRules: string[];
    evidenceRequirements: string[];
  },
): RuleVersion {
  return {
    id: newId("rule"),
    projectId,
    name: input.name.trim(),
    version: input.version.trim(),
    stepsMd: input.stepsMd.trim(),
    aiRules: input.aiRules,
    evidenceRequirements: input.evidenceRequirements,
    revision: 1,
    createdAt: isoNow(),
  };
}

export function featureSummary(task: Task): Array<{ name: string; count: number }> {
  return featureGroups(task).map((group) => ({ name: group.name, count: group.items.length }));
}
