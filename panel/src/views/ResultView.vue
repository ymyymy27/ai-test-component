<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import CaseLedger from "@/components/CaseLedger.vue";
import ErrorState from "@/components/ErrorState.vue";
import EvidenceDialog from "@/components/EvidenceDialog.vue";
import Icon from "@/components/Icon.vue";
import LinkPanel from "@/components/LinkPanel.vue";
import ManualResultDialog from "@/components/ManualResultDialog.vue";
import PageHeader from "@/components/PageHeader.vue";
import PreflightAndProbesCard from "@/components/PreflightAndProbesCard.vue";
import ResultSummary from "@/components/ResultSummary.vue";
import StatusMark from "@/components/StatusMark.vue";
import StatusText from "@/components/StatusText.vue";
import ReportCaseTable from "@/components/report/ReportCaseTable.vue";
import ReportConclusionSection from "@/components/report/ReportConclusionSection.vue";
import ReportDetailDialog from "@/components/report/ReportDetailDialog.vue";
import ReportOpinionAndExport from "@/components/report/ReportOpinionAndExport.vue";
import ReportScopeAndChecks from "@/components/report/ReportScopeAndChecks.vue";
import { commandText, type ReportDetail } from "@/components/report/reportFormat";
import { buildCaseFixPrompt, buildMinimalReproCommand } from "@/domain/aiPrompt";
import {
  currentPlan,
  currentRun,
  displayItemOutcome,
  effectiveCaseResult,
  itemDiffTag,
  normalizeReport,
  reportsForRun,
  sortedRunItems,
  unselectedItems,
} from "@/domain/derive";
import { ANALYSIS_REASON_LABEL, ANALYSIS_REASON_TEXT } from "@/domain/labels";
import type {
  CaseResult,
  EvidenceRef,
  ExportResult,
  Issue,
  Report,
  ReportCaseDetail,
  Run,
  RunCase,
  RunItem,
  Task,
} from "@/domain/types";
import type { PlanRepairCaseResultDto } from "@/host/dto";
import type { EvidenceInput, LinkAssertInput } from "@/repositories/ProjectRepository";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

type ResultTab = "summary" | "report" | "links";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const taskId = computed(() => String(route.params.taskId ?? ""));
const project = computed(() => workspace.currentProject);
const task = computed<Task | null>(
  () => project.value?.tasks.find((entry) => entry.id === taskId.value) ?? null,
);
const run = computed<Run | null>(() => {
  if (!task.value) {
    return null;
  }
  const requested = String(route.query.run ?? "");
  if (requested) {
    const found = task.value.runs.find((entry) => entry.id === requested);
    if (found) {
      return found;
    }
  }
  return currentRun(task.value);
});
const plan = computed(() => (task.value ? currentPlan(task.value) : null));

const activeTab = ref<ResultTab>(
  route.query.tab === "report" || route.query.tab === "links" ? route.query.tab : "summary",
);
watch(activeTab, () => {
  const query = { ...route.query };
  if (activeTab.value === "summary") {
    delete query.tab;
  } else {
    query.tab = activeTab.value;
  }
  void router.replace({ query });
});

const environmentRows = computed(() => {
  const environment = run.value?.environment ?? {};
  return [
    environment["python_version"] ? `Python ${environment["python_version"]}` : "",
    environment["venv"] ? `venv ${environment["venv"]}` : "",
    environment["node"] ? `Node ${environment["node"]}` : "",
  ].filter(Boolean);
});
const repairSuggestions = computed(() =>
  (run.value?.dependencySuggestions ?? []).filter((entry) => !entry.installed),
);
const repairHistory = computed(() => run.value?.dependencyRepairs ?? []);
const repairSelection = ref<string[]>([]);
const repairConfirmed = ref(false);
watch(
  repairSuggestions,
  (entries) => {
    repairSelection.value = entries.map((entry) => entry.package);
    repairConfirmed.value = false;
  },
  { immediate: true },
);
const repairing = computed(
  () => Boolean(run.value) && workspace.busyKey(`repair:${run.value?.id}`),
);

function toggleRepair(packageName: string): void {
  repairSelection.value = repairSelection.value.includes(packageName)
    ? repairSelection.value.filter((entry) => entry !== packageName)
    : [...repairSelection.value, packageName];
  repairConfirmed.value = false;
}

async function installDependenciesAndRerun(): Promise<void> {
  if (
    !run.value ||
    repairSelection.value.length === 0 ||
    !repairConfirmed.value ||
    repairing.value
  ) {
    return;
  }
  try {
    await workspace.repairRunDependencies(
      projectId.value,
      run.value.id,
      [...repairSelection.value],
    );
  } catch {
    return;
  }
  const prepared = await rerun();
  if (!prepared) {
    ui.showToast("依赖已安装，但准备重跑计划失败；可稍后点“按未完成项重新生成计划”。", "error");
  }
}

const filter = ref<"all" | "problems" | "unfinished" | "regressed" | "fixed">(
  route.query.filter === "problems" ||
    route.query.filter === "unfinished" ||
    route.query.filter === "regressed" ||
    route.query.filter === "fixed"
    ? route.query.filter
    : "all",
);
watch(filter, () => {
  const query = { ...route.query };
  if (filter.value === "all") {
    delete query.filter;
  } else {
    query.filter = filter.value;
  }
  void router.replace({ query });
});

const caseLedgerRef = ref<InstanceType<typeof CaseLedger> | null>(null);
const caseTableRef = ref<InstanceType<typeof ReportCaseTable> | null>(null);
const manualOpen = ref(false);
const manualCase = ref<RunCase | null>(null);
const evidenceOpen = ref(false);
const evidenceCaseId = ref("");

const items = computed(() => (run.value ? sortedRunItems(run.value) : []));
const unmappedCases = computed(() =>
  (run.value?.cases ?? []).filter(
    (testCase) => !testCase.itemId && (testCase.caseItemIds?.length ?? 0) === 0,
  ),
);
const outcomes = computed<Record<string, CaseResult>>(() => {
  const map: Record<string, CaseResult> = {};
  if (run.value) {
    run.value.items.forEach((item) => {
      map[item.itemId] = displayItemOutcome(run.value as Run, item.itemId);
    });
  }
  return map;
});
const problemCount = computed(
  () =>
    items.value.filter((item) =>
      ["failed", "blocked", "error"].includes(outcomes.value[item.itemId] ?? "not_run"),
    ).length,
);
const unfinishedCount = computed(
  () =>
    items.value.filter((item) =>
      ["not_run", "manual_pending", "blocked"].includes(
        outcomes.value[item.itemId] ?? "not_run",
      ),
    ).length,
);
const regressedCount = computed(() =>
  previousRun.value && run.value
    ? items.value.filter(
        (item) => itemDiffTag(previousRun.value, run.value as Run, item.itemId) === "regressed",
      ).length
    : 0,
);
const fixedCount = computed(() =>
  previousRun.value && run.value
    ? items.value.filter(
        (item) => itemDiffTag(previousRun.value, run.value as Run, item.itemId) === "fixed",
      ).length
    : 0,
);

const openIssuesByCaseId = computed<Record<string, Issue>>(() => {
  const map: Record<string, Issue> = {};
  const issues = (project.value?.issues ?? []).filter((issue) => issue.status !== "closed");
  for (const issue of issues) {
    if (issue.caseId && !map[issue.caseId]) {
      map[issue.caseId] = issue;
    }
    for (const occ of issue.occurrences ?? []) {
      if (occ.caseId && !map[occ.caseId]) {
        map[occ.caseId] = issue;
      }
    }
  }
  // 兜底匹配：计划重新生成后 caseId 可能变化，按同一验收项原文 itemText 关联未关闭历史问题
  const currentRun = run.value;
  if (currentRun) {
    const itemTextById = new Map(items.value.map((it) => [it.itemId, it.text]));
    for (const rc of currentRun.cases) {
      if (map[rc.id]) {
        continue;
      }
      const relatedIds = rc.caseItemIds?.length ? rc.caseItemIds : rc.itemId ? [rc.itemId] : [];
      const texts = relatedIds
        .map((id) => itemTextById.get(id) ?? "")
        .filter(Boolean);
      if (texts.length === 0) {
        continue;
      }
      const byText = issues.find((issue) => issue.itemText && texts.includes(issue.itemText));
      if (byText) {
        map[rc.id] = byText;
      }
    }
  }
  return map;
});

const repairPreview = ref<PlanRepairCaseResultDto | null>(null);
const repairPreviewCase = ref<RunCase | null>(null);
const repairPreviewBusyCaseId = ref("");
const applyingCaseRepair = ref(false);
const closeIssueBusyId = ref("");

async function handlePreviewRepair(testCase: RunCase): Promise<void> {
  if (!plan.value || !run.value || repairPreviewBusyCaseId.value) {
    return;
  }
  repairPreviewBusyCaseId.value = testCase.id;
  try {
    const preview = await workspace.previewCaseRepair({
      projectId: projectId.value,
      planId: plan.value.id,
      caseId: testCase.id,
      runId: run.value.id,
      errorHint: testCase.actual || testCase.message || "",
    });
    repairPreview.value = preview;
    repairPreviewCase.value = testCase;
  } catch (cause) {
    ui.showToast(
      cause instanceof Error && cause.message ? cause.message : "生成修复预览失败。",
      "error",
    );
  } finally {
    repairPreviewBusyCaseId.value = "";
  }
}

async function handleApplyRepairAndRerun(): Promise<void> {
  if (
    !plan.value ||
    !run.value ||
    !repairPreview.value ||
    !repairPreviewCase.value ||
    repairPreview.value.blocked ||
    applyingCaseRepair.value
  ) {
    return;
  }
  const ok = await ui.askConfirm({
    title: "应用脚本修复并定向重跑",
    body: `将基于计划 ${plan.value.no}（第 ${plan.value.revision} 版）生成新修订写入修复脚本，并立即定向重跑「${repairPreviewCase.value.title}」所在验收项（不会修改本次历史运行记录）。`,
    confirmLabel: "确认应用并重跑",
  });
  if (ok === null) {
    return;
  }
  applyingCaseRepair.value = true;
  try {
    const nextRun = await workspace.applyCaseRepairAndRerun({
      projectId: projectId.value,
      taskId: taskId.value,
      planId: plan.value.id,
      caseId: repairPreviewCase.value.id,
      runId: run.value.id,
      repairedScript: repairPreview.value.repaired_script,
      expectedRevision: plan.value.revision,
    });
    repairPreview.value = null;
    repairPreviewCase.value = null;
    ui.showToast("已应用修复生成新计划修订，并定向启动新运行。", "success");
    await router.push(`/projects/${projectId.value}/tasks/${taskId.value}/run?run=${nextRun.id}`);
  } catch (cause) {
    ui.showToast(
      cause instanceof Error && cause.message ? cause.message : "应用脚本修复失败。",
      "error",
    );
  } finally {
    applyingCaseRepair.value = false;
  }
}

async function handleCloseLinkedIssue(payload: { issue: Issue; reason: string }): Promise<void> {
  if (!run.value || closeIssueBusyId.value) {
    return;
  }
  closeIssueBusyId.value = payload.issue.id;
  try {
    await workspace.closeLinkedIssue(
      projectId.value,
      payload.issue,
      run.value.id,
      payload.reason,
    );
    ui.showToast(`历史问题「${payload.issue.title}」已合规流转并附证据关闭。`, "success");
  } catch (cause) {
    ui.showToast(
      cause instanceof Error && cause.message ? cause.message : "关闭关联历史问题失败。",
      "error",
    );
  } finally {
    closeIssueBusyId.value = "";
  }
}

const reports = computed(() =>
  project.value && run.value
    ? reportsForRun(project.value, run.value.id).map((entry) => normalizeReport(entry))
    : [],
);
const selectedReportId = ref("");
const activeReport = computed<Report | null>(() => {
  if (reports.value.length === 0) {
    return null;
  }
  return reports.value.find((r) => r.id === selectedReportId.value) ?? reports.value[0] ?? null;
});
const isLatestReport = computed(
  () => Boolean(activeReport.value && reports.value[0]?.id === activeReport.value.id),
);
const activeAnalysis = computed(() => activeReport.value?.analysis ?? null);
const analyzingRun = computed(() =>
  run.value ? workspace.busyKey(`analysis-run:${run.value.id}`) : false,
);

const activeLink = computed(() => activeReport.value?.link ?? run.value?.link ?? null);
const linkBusy = computed(
  () =>
    Boolean(run.value) &&
    (workspace.busyKey(`link:${run.value?.id}`) ||
      workspace.busyKey(`link-assert:${run.value?.id}`)),
);

const exporting = ref(false);
const savingOpinion = ref(false);
const exportResult = ref<ExportResult | null>(null);
const detailContent = ref<ReportDetail | null>(null);
const activeReportItemId = ref("");

const reportBusy = computed(
  () => exporting.value || Boolean(run.value && workspace.busyKey(`report:${run.value.id}`)),
);
const busyOpinion = computed(
  () =>
    savingOpinion.value ||
    Boolean(activeReport.value && workspace.busyKey(`opinion:${activeReport.value.id}`)),
);

const unselected = computed(() => {
  if (!task.value || !run.value) {
    return [];
  }
  const runPlan = task.value.plans.find((entry) => entry.id === run.value?.planId);
  const snapshot = run.value.excludedItems ?? runPlan?.excludedItems;
  if (snapshot !== undefined) {
    return snapshot.map((entry, index) => ({
      id: entry.id,
      no:
        entry.no ||
        task.value?.acceptanceItems.find((item) => item.id === entry.id)?.no ||
        String(index + 1).padStart(2, "0"),
      text: entry.text,
    }));
  }
  return unselectedItems(task.value, run.value.scope.acceptanceItemIds);
});
const supportsEvidence = computed(() => workspace.capabilities.evidenceFiles);
const evidenceLimits = computed(() => ({
  singleFileMb: project.value?.config.evidence?.singleFileMb,
  mediaMb: project.value?.config.evidence?.mediaMb,
}));
const manualBusy = computed(() =>
  manualCase.value ? workspace.busyKey(`manual:${manualCase.value.id}`) : false,
);
const previousRun = computed<Run | null>(() => {
  if (!task.value || !run.value) {
    return null;
  }
  const ordered = [...task.value.runs].sort(
    (a, b) => new Date(a.startedAt).getTime() - new Date(b.startedAt).getTime(),
  );
  const idx = ordered.findIndex((entry) => entry.id === run.value?.id);
  return idx > 0 ? (ordered[idx - 1] ?? null) : null;
});
const failedAndBlockedItemIds = computed(() =>
  items.value
    .filter((item) =>
      ["failed", "error", "blocked"].includes(outcomes.value[item.itemId] ?? "not_run"),
    )
    .map((item) => item.itemId),
);
const manualPendingCases = computed(() => {
  const current = run.value;
  if (!current) {
    return [] as RunCase[];
  }
  return current.cases.filter(
    (entry) => entry.layer === "L3" && effectiveCaseResult(entry, current) === "manual_pending",
  );
});
const targetedRerunBusy = ref(false);
const batchManualBusy = ref(false);

async function triggerRunAnalysis(): Promise<void> {
  if (!run.value || analyzingRun.value) {
    return;
  }
  try {
    await workspace.runAnalysis(projectId.value, run.value.id);
    await workspace.ensureReportVersions(projectId.value, run.value.id);
  } catch {
    // store 已提示。
  }
}

function filterReportCasesByItem(itemId: string): void {
  activeReportItemId.value = activeReportItemId.value === itemId ? "" : itemId;
  if (!activeReportItemId.value) {
    return;
  }
  void nextTick(() => {
    caseTableRef.value?.scrollIntoView();
  });
}

async function targetedRerun(): Promise<void> {
  if (
    !run.value ||
    !task.value ||
    !plan.value ||
    failedAndBlockedItemIds.value.length === 0 ||
    targetedRerunBusy.value
  ) {
    return;
  }
  const ok = await ui.askConfirm({
    title: "直接复测未通过项",
    body: `将复用计划 ${plan.value.no}（第 ${plan.value.revision} 版）中的已有用例，仅执行 ${failedAndBlockedItemIds.value.length} 个未通过/阻塞验收项，不重建计划。`,
    confirmLabel: "确认并开始执行",
  });
  if (ok === null) {
    return;
  }
  targetedRerunBusy.value = true;
  try {
    const nextRun = await workspace.startTargetedRerun(
      projectId.value,
      task.value.id,
      plan.value.id,
      [...failedAndBlockedItemIds.value],
    );
    await router.push(`/projects/${projectId.value}/tasks/${task.value.id}/run?run=${nextRun.id}`);
  } catch (error) {
    if (
      error instanceof Error &&
      (error.message.includes("PLAN_STALE") || error.message.includes("过时"))
    ) {
      ui.showToast("计划与最新代码状态已不一致，请点击右侧重新生成计划后再试。", "error");
    }
  } finally {
    targetedRerunBusy.value = false;
  }
}

async function batchMarkManualPassed(): Promise<void> {
  const current = run.value;
  if (!current || manualPendingCases.value.length === 0 || batchManualBusy.value) {
    return;
  }
  const ok = await ui.askConfirm({
    title: "全部人工项标记通过",
    body: `确认将当前剩余 ${manualPendingCases.value.length} 条待核验人工用例全部标记为“通过”吗？该操作会同步刷新报告结论。`,
    confirmLabel: "全部标记通过",
  });
  if (ok === null) {
    return;
  }
  batchManualBusy.value = true;
  let saved = 0;
  let failed = 0;
  try {
    for (const entry of manualPendingCases.value) {
      try {
        await workspace.recordManual(projectId.value, current.id, entry.id, {
          result: "passed",
          note: "人工批量确认通过",
          evidence: [],
        });
        saved += 1;
      } catch {
        failed += 1;
      }
    }
    if (failed === 0) {
      ui.showToast(`已将 ${saved} 条人工用例全部标记通过并更新报告。`, "success");
    } else {
      ui.showToast(`已完成 ${saved} 条，另有 ${failed} 条更新失败。`, "error");
    }
  } finally {
    batchManualBusy.value = false;
  }
}

async function copyCaseFix(testCase: RunCase, item: RunItem): Promise<void> {
  const prompt = buildCaseFixPrompt({
    projectName: project.value?.name,
    projectRoot: project.value?.root,
    taskTitle: task.value?.title,
    item: { no: item.no, text: item.text },
    testCase,
  });
  try {
    if (!navigator.clipboard?.writeText) {
      ui.showToast("当前环境不支持剪贴板写入。", "error");
      return;
    }
    await navigator.clipboard.writeText(prompt);
    ui.showToast("AI 修复任务包已复制到剪贴板。", "success");
  } catch {
    ui.showToast("复制失败，请检查剪贴板权限。", "error");
  }
}

async function copyReportCaseFix(testCase: ReportCaseDetail): Promise<void> {
  const matchedCase = run.value?.cases.find((entry) => entry.id === testCase.caseId);
  const itemDetail = activeReport.value?.itemDetails.find(
    (entry) => entry.itemId === testCase.itemId,
  );
  const prompt = buildCaseFixPrompt({
    projectName: project.value?.name,
    projectRoot: project.value?.root,
    taskTitle: task.value?.title,
    item: {
      no: testCase.itemNo || itemDetail?.no || "",
      text: itemDetail?.text || testCase.title,
    },
    testCase: matchedCase ?? {
      id: testCase.caseId,
      title: testCase.title,
      method: testCase.method,
      expected: testCase.expected,
      actual: testCase.actual,
      message: testCase.analysis?.observed ?? "",
      errorType: testCase.errorType ?? "",
      stackKey: testCase.stackKey ?? "",
      evidence: testCase.evidence,
      testNodes: testCase.testNodes ?? [],
      confidence: testCase.confidence,
      confidenceReason: testCase.confidenceReason,
      phaseFailed: testCase.phaseFailed,
      serviceLogIssues: testCase.serviceLogIssues,
      judgement: testCase.judgement,
      file: testCase.file,
      line: testCase.line,
    },
  });
  try {
    if (!navigator.clipboard?.writeText) {
      ui.showToast("当前环境不支持剪贴板写入。", "error");
      return;
    }
    await navigator.clipboard.writeText(prompt);
    ui.showToast("修复上下文已复制到剪贴板。", "success");
  } catch {
    ui.showToast("复制失败，请检查剪贴板权限。", "error");
  }
}

async function addProbeConvention(input: {
  pattern: string;
  reason: string;
  scope: string;
}): Promise<void> {
  try {
    await workspace.addConvention(projectId.value, input);
  } catch {
    // store 已提示。
  }
}

async function markCaseConvention(testCase: ReportCaseDetail): Promise<void> {
  const defaultPattern = (testCase.actual || testCase.title || testCase.caseId).trim();
  const reason = await ui.askConfirm({
    title: "标为服务或项目正常约定（后续不再误报）",
    body: `后续再次出现匹配“${defaultPattern.slice(0, 64)}”的结果时，自动按已知惯例豁免。`,
    confirmLabel: "记录惯例",
    requireText: true,
    textLabel: "原因说明",
    placeholder: "例如：开发态预留端口探测日志，非故障",
  });
  if (reason === null) {
    return;
  }
  try {
    await workspace.addConvention(projectId.value, {
      pattern: defaultPattern,
      reason,
      scope: "case",
    });
  } catch {
    // store 已提示。
  }
}

let disposed = false;

async function loadReportsUntilReady(): Promise<void> {
  for (let attempt = 0; attempt < 30 && !disposed; attempt += 1) {
    if (!run.value || reports.value.length > 0) {
      return;
    }
    if (
      ["interrupted", "cancelled", "execution_error"].includes(run.value.status) ||
      run.value.postprocess === "error"
    ) {
      return;
    }
    try {
      await workspace.ensureReportVersions(projectId.value, run.value.id);
    } catch {
      // 忽略瞬时错误，待下次轮询
    }
    if (reports.value.length > 0) {
      return;
    }
    await new Promise((resolve) => {
      setTimeout(resolve, 2000);
    });
  }
}

onMounted(async () => {
  if (task.value && workspace.currentTask?.id !== taskId.value) {
    try {
      await workspace.setActiveTask(projectId.value, taskId.value);
    } catch {
      // store 已提示。
    }
  }
  void loadReportsUntilReady();
});

onBeforeUnmount(() => {
  disposed = true;
});

function openManual(testCaseId: string): void {
  const testCase = run.value?.cases.find((entry) => entry.id === testCaseId) ?? null;
  manualCase.value = testCase;
  manualOpen.value = true;
}

async function saveManual(input: {
  result: "passed" | "failed";
  note: string;
  evidence: EvidenceInput[];
}): Promise<void> {
  if (!run.value || !manualCase.value) {
    return;
  }
  try {
    await workspace.recordManual(projectId.value, run.value.id, manualCase.value.id, input);
    manualOpen.value = false;
    ui.showToast("人工结果已记录；报告已更新。", "success");
  } catch {
    // store 已提示。
  }
}

function openEvidence(): void {
  evidenceCaseId.value = "";
  evidenceOpen.value = true;
}

async function focusFromSummary(itemId: string): Promise<void> {
  activeTab.value = "summary";
  if (filter.value !== "all") {
    filter.value = "all";
  }
  await nextTick();
  await caseLedgerRef.value?.focusItem(itemId);
}

async function saveEvidence(evidence: EvidenceInput[]): Promise<void> {
  if (!run.value) {
    return;
  }
  const failed: string[] = [];
  for (const input of evidence) {
    try {
      await workspace.addEvidence(projectId.value, run.value.id, input);
    } catch {
      failed.push(input.name);
    }
  }
  const saved = evidence.length - failed.length;
  if (saved > 0) {
    try {
      await workspace.regenerateReport(projectId.value, run.value.id);
    } catch {
      // store 已提示。
    }
  }
  evidenceOpen.value = false;
  if (failed.length === 0) {
    ui.showToast(`已登记 ${saved} 个证据文件；报告已更新。`, "success");
  } else {
    ui.showToast(
      `已登记 ${saved} 个；${failed.length} 个失败：${failed.join("、")}。`,
      "error",
    );
  }
}

async function adjustScope(): Promise<void> {
  try {
    await workspace.reviseScope(projectId.value, taskId.value);
    await router.push(`/projects/${projectId.value}/tasks/${taskId.value}/scope`);
  } catch {
    // store 已提示。
  }
}

async function rerun(): Promise<boolean> {
  if (!run.value) {
    return false;
  }
  const targetIds =
    run.value.verdict === "failed"
      ? items.value
          .filter((item) =>
            ["failed", "blocked", "error"].includes(outcomes.value[item.itemId] ?? "not_run"),
          )
          .map((item) => item.itemId)
      : run.value.verdict === "incomplete"
        ? items.value
            .filter((item) => (outcomes.value[item.itemId] ?? "not_run") !== "passed")
            .map((item) => item.itemId)
        : run.value.scope.acceptanceItemIds;
  try {
    await workspace.prepareRerun(
      projectId.value,
      taskId.value,
      targetIds.length > 0 ? targetIds : undefined,
    );
    await router.push(`/projects/${projectId.value}/tasks/${taskId.value}/plan`);
    return true;
  } catch {
    return false;
  }
}

async function regenerate(): Promise<void> {
  if (!run.value) {
    return;
  }
  try {
    const next = await workspace.regenerateReport(projectId.value, run.value.id);
    selectedReportId.value = next.id;
    ui.showToast("正式报告已更新为最新版本。", "success");
  } catch {
    // store 已提示。
  }
}

async function exportReport(reportId: string): Promise<void> {
  if (exporting.value) {
    return;
  }
  exporting.value = true;
  try {
    exportResult.value = await workspace.exportReport(projectId.value, reportId);
  } catch {
    // store 已提示。
  } finally {
    exporting.value = false;
  }
}

async function saveOpinion(payload: { by: string; text: string }): Promise<void> {
  if (!activeReport.value || busyOpinion.value) {
    return;
  }
  savingOpinion.value = true;
  try {
    await workspace.saveOpinion(
      projectId.value,
      activeReport.value.id,
      payload.by,
      payload.text,
    );
    ui.showToast("验收意见已记录并署名。", "success");
  } catch {
    // store 已提示。
  } finally {
    savingOpinion.value = false;
  }
}

async function importHostLinks(): Promise<void> {
  if (!run.value || linkBusy.value) {
    return;
  }
  try {
    await workspace.importHostLink(projectId.value, run.value.id);
    ui.showToast("运行期调用链与观测证据已同步刷新。", "success");
  } catch {
    // store 已提示。
  }
}

async function assertLink(input: LinkAssertInput): Promise<void> {
  if (!run.value) {
    return;
  }
  try {
    await workspace.assertLink(projectId.value, run.value.id, input);
    const next = await workspace.regenerateReport(projectId.value, run.value.id);
    selectedReportId.value = next.id;
    ui.showToast("调用链节点断言已记录，并同步更新报告。", "success");
  } catch {
    // store 已提示。
  }
}

async function showEvidence(evidence: EvidenceRef): Promise<void> {
  if (workspace.mode === "host" && evidence.sourcePath) {
    try {
      await workspace.openHostPath(evidence.sourcePath);
      return;
    } catch {
      // 退回弹窗或剪贴板
    }
  }
  const reference = evidence.sourcePath || evidence.name;
  try {
    await navigator.clipboard.writeText(reference);
    ui.showToast(`证据引用已复制：${reference}`, "success");
  } catch {
    ui.showToast(`证据引用：${reference}`, "info");
  }
}

function showReportCommand(testCase: ReportCaseDetail): void {
  detailContent.value = {
    kind: "command",
    title: testCase.title,
    command: commandText(testCase.method),
    root: project.value?.root ?? "",
  };
}

function openReportEvidence(evidence: EvidenceRef): void {
  detailContent.value = { kind: "evidence", evidence };
}

async function copyRepro(command: string): Promise<void> {
  const text = command || buildMinimalReproCommand({ method: command });
  try {
    await navigator.clipboard?.writeText(text);
    ui.showToast("最小复现方式已复制。", "success");
  } catch {
    ui.showToast("复制失败，请检查剪贴板权限。", "error");
  }
}

async function openExport(): Promise<void> {
  if (!exportResult.value?.path) {
    return;
  }
  try {
    await workspace.openHostPath(exportResult.value.path);
  } catch {
    // store 已提示。
  }
}

async function copyMarkdown(): Promise<void> {
  if (!exportResult.value?.markdown) {
    return;
  }
  try {
    await navigator.clipboard.writeText(exportResult.value.markdown);
    ui.showToast("报告 Markdown 已复制到剪贴板。", "success");
  } catch {
    ui.showToast("复制失败，请手动选中。", "error");
  }
}

function rerunLabel(): string {
  if (run.value?.verdict === "passed") {
    return "按原范围再测一次";
  }
  if (run.value?.verdict === "failed") {
    return "仅围绕未通过项重建计划";
  }
  if (run.value?.verdict === "incomplete") {
    return "仅围绕未完成项重建计划";
  }
  return "按原范围重建计划";
}
</script>

<template>
  <div v-if="!task || !run" class="page">
    <ErrorState
      title="没有可查看的结果"
      text="回到计划页确认一次运行。"
      retry-label="回到计划"
      @retry="router.push(`/projects/${projectId}/tasks/${taskId}/plan`)"
    />
  </div>
  <div v-else class="page page-wide">
    <PageHeader
      title="测试结果"
      :subtitle="`${task.title} · ${plan ? `计划 ${plan.no}（第 ${plan.revision} 版）` : '计划快照'} · ${new Date(run.startedAt).toLocaleString('zh-CN')} · ${run.confirmedBy} 确认`"
    >
      <template #actions>
        <button
          v-if="activeReport"
          class="btn btn-ghost btn-sm"
          type="button"
          @click="router.push(`/projects/${projectId}/reports/${activeReport.id}`)"
        >
          独立打开报告
        </button>
        <button
          v-if="activeReport"
          class="btn btn-sm btn-primary"
          type="button"
          :disabled="exporting"
          :aria-busy="exporting"
          @click="exportReport(activeReport.id)"
        >
          <Icon name="download" :size="14" />
          {{
            exporting
              ? "正在导出…"
              : workspace.mode === "host"
                ? "导出到工作空间"
                : "导出 Markdown"
          }}
        </button>
      </template>
    </PageHeader>

    <div class="result-sheet">
      <ResultSummary
        :run="run"
        :task="task"
        :conclusion="activeReport?.conclusion"
        @select="focusFromSummary"
      />

      <!-- 三合一结果与报告切换条 -->
      <div class="row-between" style="margin: var(--space-4) 0; flex-wrap: wrap; gap: 10px;">
        <div class="tab-pills" role="tablist" aria-label="结果工作台分区">
          <button
            class="tab-pill"
            :class="{ 'is-active': activeTab === 'summary' }"
            type="button"
            role="tab"
            :aria-selected="activeTab === 'summary'"
            @click="activeTab = 'summary'"
          >
            检查结果
          </button>
          <button
            class="tab-pill"
            :class="{ 'is-active': activeTab === 'report' }"
            type="button"
            role="tab"
            :aria-selected="activeTab === 'report'"
            @click="activeTab = 'report'"
          >
            验收报告
            <span v-if="reports.length" class="muted"> (v{{ activeReport?.version ?? 1 }})</span>
          </button>
          <button
            class="tab-pill"
            :class="{ 'is-active': activeTab === 'links' }"
            type="button"
            role="tab"
            :aria-selected="activeTab === 'links'"
            @click="activeTab = 'links'"
          >
            调用链
          </button>
        </div>

        <button
          v-if="manualPendingCases.length >= 2 && activeTab === 'summary'"
          class="btn btn-sm"
          type="button"
          :disabled="batchManualBusy"
          :aria-busy="batchManualBusy"
          @click="batchMarkManualPassed"
        >
          {{ batchManualBusy ? "正在批量记录…" : `全部人工项标记通过 (${manualPendingCases.length})` }}
        </button>
      </div>

      <!-- 标签页 1：检查结果 -->
      <div v-if="activeTab === 'summary'" class="stack" role="tabpanel" aria-label="检查结果">
        <section
          v-if="workspace.mode === 'host' && repairSuggestions.length"
          class="elevated-panel stack-sm"
          aria-label="缺失依赖"
        >
          <div class="row-between">
            <h2 class="section-title">缺失依赖</h2>
            <span class="badge is-warn">安装后可续跑未完成项</span>
          </div>
          <div class="repair-list">
            <label
              v-for="entry in repairSuggestions"
              :key="`${entry.module}-${entry.package}`"
              class="repair-row"
            >
              <input
                type="checkbox"
                :checked="repairSelection.includes(entry.package)"
                @change="toggleRepair(entry.package)"
              />
              <span class="repair-package">{{ entry.package }}</span>
              <span class="repair-module">缺失：{{ entry.module }}</span>
              <span class="repair-source">
                {{
                  entry.source === "declared"
                    ? "项目声明依赖"
                    : entry.source === "ai"
                      ? "推荐"
                      : "常用映射"
                }}
              </span>
              <span class="muted">{{ entry.reason }}</span>
            </label>
          </div>
          <div class="empty-actions">
            <label class="repair-confirm">
              <input v-model="repairConfirmed" type="checkbox" />
              我已确认安装以上包，并允许记录环境变动凭证
            </label>
            <button
              class="btn btn-primary btn-sm"
              type="button"
              :disabled="repairSelection.length === 0 || !repairConfirmed || repairing"
              :aria-busy="repairing"
              @click="installDependenciesAndRerun"
            >
              {{ repairing ? "正在安装…" : "安装并重跑" }}
            </button>
          </div>
        </section>

        <!-- 失败分析面板 -->
        <section
          v-if="problemCount > 0 || activeAnalysis?.status === 'generated'"
          class="elevated-panel stack-sm"
          aria-label="失败分析"
        >
          <div class="row-between">
            <div class="row">
              <h2 class="section-title">失败分析</h2>
              <span class="badge is-neutral">仅供参考 · 不改结论</span>
            </div>
            <button
              v-if="workspace.mode === 'host'"
              class="btn btn-sm"
              type="button"
              :disabled="analyzingRun"
              :aria-busy="analyzingRun"
              @click="triggerRunAnalysis"
            >
              {{
                analyzingRun
                  ? "分析中…"
                  : activeAnalysis?.status === "generated"
                    ? "重新分析"
                    : "分析失败原因"
              }}
            </button>
          </div>
          <template v-if="activeAnalysis && activeAnalysis.status === 'generated'">
            <p class="doc-p">{{ activeAnalysis.observed.replace(/。{2,}/g, "。") }}</p>
            <ul class="list-plain">
              <li v-for="cause in activeAnalysis.candidateCauses" :key="cause.cause">
                <span class="badge is-warn">
                  可能性：{{
                    cause.confidence === "high"
                      ? "高"
                      : cause.confidence === "medium"
                        ? "中"
                        : cause.confidence === "low"
                          ? "低"
                          : cause.confidence
                  }}
                </span>
                {{ cause.cause.replace(/。{2,}/g, "。") }}
              </li>
              <li v-for="step in activeAnalysis.nextSteps" :key="step" class="muted">
                排查建议：{{ step.replace(/。{2,}/g, "。") }}
              </li>
            </ul>
            <details v-if="activeAnalysis.unverified.length" class="details-tech">
              <summary>待证实项 ({{ activeAnalysis.unverified.length }})</summary>
              <ul class="list-plain">
                <li v-for="item in activeAnalysis.unverified" :key="item">{{ item }}</li>
              </ul>
            </details>
          </template>
          <p v-else-if="activeAnalysis" class="field-hint">
            {{
              ANALYSIS_REASON_TEXT[activeAnalysis.reason] ||
              `${ANALYSIS_REASON_LABEL[activeAnalysis.status === "invalid" ? "invalid" : "not_generated"]}；可点击右上按钮重新分析。`
            }}
          </p>
          <p v-else class="field-hint">
            有 {{ problemCount }} 项未通过或阻塞，可点击右上按钮分析原因。
          </p>
        </section>

        <PreflightAndProbesCard
          :preflight="run.preflight ?? null"
          :probe-results="run.probeResults ?? []"
          :can-open-source="workspace.mode === 'host'"
          @add-convention="addProbeConvention"
          @open-source="(file, line) => void workspace.openSourceLocation(file, line)"
        />

        <section v-if="run.l1.length" class="section" aria-labelledby="result-l1-title">
          <div class="section-head">
            <h2 id="result-l1-title" class="section-title">基础检查 (L1)</h2>
            <span class="section-note">单独记录，不计入 S/M 验收覆盖分母</span>
          </div>
          <div class="check-list">
            <div v-for="check in run.l1" :key="check.key" class="check-row">
              <StatusMark :result="check.result" />
              <span class="check-name">{{ check.name }}</span>
              <span class="check-method">{{ check.method }}</span>
              <StatusText :result="check.result" />
              <span v-if="check.reason" class="muted check-reason">{{ check.reason }}</span>
            </div>
          </div>
        </section>

        <div class="row-between" style="flex-wrap: wrap; gap: 10px;">
          <div class="filter-bar" role="group" aria-label="筛选结果">
            <button
              class="filter-btn"
              :class="{ 'is-active': filter === 'all' }"
              type="button"
              :aria-pressed="filter === 'all'"
              @click="filter = 'all'"
            >
              全部<span class="filter-count">{{ items.length }}</span>
            </button>
            <button
              class="filter-btn"
              :class="{ 'is-active': filter === 'problems' }"
              type="button"
              :aria-pressed="filter === 'problems'"
              @click="filter = 'problems'"
            >
              只看未通过<span class="filter-count">{{ problemCount }}</span>
            </button>
            <button
              class="filter-btn"
              :class="{ 'is-active': filter === 'unfinished' }"
              type="button"
              :aria-pressed="filter === 'unfinished'"
              @click="filter = 'unfinished'"
            >
              未完成<span class="filter-count">{{ unfinishedCount }}</span>
            </button>
            <button
              v-if="previousRun"
              class="filter-btn"
              :class="{ 'is-active': filter === 'regressed' }"
              type="button"
              :aria-pressed="filter === 'regressed'"
              @click="filter = 'regressed'"
            >
              新增失败<span class="filter-count">{{ regressedCount }}</span>
            </button>
            <button
              v-if="previousRun"
              class="filter-btn"
              :class="{ 'is-active': filter === 'fixed' }"
              type="button"
              :aria-pressed="filter === 'fixed'"
              @click="filter = 'fixed'"
            >
              本次已修复<span class="filter-count">{{ fixedCount }}</span>
            </button>
          </div>
          <button v-if="supportsEvidence" class="btn btn-sm" type="button" @click="openEvidence">
            + 补充人工证据
          </button>
        </div>

        <!-- 脚本修复只读预览与确认应用面板（绝不修改历史 run，更新 Plan 新 revision 并定向重跑） -->
        <section
          v-if="repairPreview && repairPreviewCase"
          class="elevated-panel stack-sm"
          aria-label="脚本修复 diff 预览"
        >
          <div class="row-between" style="flex-wrap: wrap; gap: 8px;">
            <div>
              <strong>脚本修复预览 · {{ repairPreviewCase.title }}</strong>
              <span class="badge" :class="repairPreview.blocked ? 'is-fail' : 'is-pass'" style="margin-left: 8px;">
                {{ repairPreview.blocked ? "安全守卫拦截" : "通过安全守卫" }}
              </span>
            </div>
            <div class="row" style="gap: 8px;">
              <button
                class="btn btn-primary btn-sm"
                type="button"
                :disabled="repairPreview.blocked || applyingCaseRepair"
                :aria-busy="applyingCaseRepair"
                @click="handleApplyRepairAndRerun"
              >
                {{ applyingCaseRepair ? "正在更新并重跑…" : "应用更新计划并定向重跑" }}
              </button>
              <button
                class="btn btn-ghost btn-sm"
                type="button"
                @click="repairPreview = null; repairPreviewCase = null"
              >
                关闭
              </button>
            </div>
          </div>
          <p v-if="repairPreview.guard_blocked.length" class="field-hint">
            被以下安全守卫拦截（不可应用）：{{ repairPreview.guard_blocked.join("、") }}
          </p>
          <pre class="mono" style="white-space: pre-wrap; font-size: 12px; max-height: 260px; overflow: auto;">{{ repairPreview.unified_diff || "脚本内容无差异" }}</pre>
        </section>

        <CaseLedger
          ref="caseLedgerRef"
          :run="run"
          :previous-run="previousRun"
          :items="items"
          :filter="filter"
          allow-manual
          allow-copy-fix
          :allow-script-repair="workspace.mode === 'host' && Boolean(plan)"
          :repair-busy-case-id="repairPreviewBusyCaseId"
          :open-issues-by-case-id="openIssuesByCaseId"
          :close-issue-busy-id="closeIssueBusyId"
          :manual-busy-case-id="manualCase?.id ?? ''"
          @manual="openManual"
          @show-evidence="showEvidence"
          @copy-fix="copyCaseFix"
          @preview-repair="handlePreviewRepair"
          @close-linked-issue="handleCloseLinkedIssue"
          @reset-filter="filter = 'all'"
        />
      </div>

      <!-- 标签页 2：验收报告 -->
      <div v-else-if="activeTab === 'report'" class="stack" role="tabpanel" aria-label="验收报告">
        <div v-if="!activeReport" class="elevated-panel stack-sm">
          <p class="field-hint">
            本次测试的正式验收报告尚在整理或未生成。如果运行已结束，可立即生成正式版本。
          </p>
          <div>
            <button
              class="btn btn-primary btn-sm"
              type="button"
              :disabled="reportBusy"
              :aria-busy="reportBusy"
              @click="regenerate"
            >
              {{ reportBusy ? "正在整理报告…" : "立即生成正式报告" }}
            </button>
          </div>
        </div>
        <template v-else>
          <div class="row-between" style="flex-wrap: wrap; gap: 8px;">
            <div v-if="reports.length > 1" class="filter-bar" role="group" aria-label="切换报告版本">
              <button
                v-for="ver in reports"
                :key="ver.id"
                class="filter-btn"
                :class="{ 'is-active': ver.id === activeReport.id }"
                type="button"
                @click="selectedReportId = ver.id"
              >
                第 {{ ver.version }} 版
              </button>
            </div>
            <div class="row">
              <button
                class="btn btn-sm"
                type="button"
                :disabled="reportBusy"
                :aria-busy="reportBusy"
                @click="regenerate"
              >
                {{ reportBusy ? "正在生成…" : "刷新生成新版报告" }}
              </button>
            </div>
          </div>

          <section class="report-group">
            <ReportConclusionSection :report="activeReport" />
            <ReportScopeAndChecks
              :report="activeReport"
              :active-item-id="activeReportItemId"
              :can-open-source="workspace.mode === 'host'"
              @filter-item="filterReportCasesByItem"
              @open-evidence="openReportEvidence"
              @add-convention="addProbeConvention"
              @open-source="(file, line) => void workspace.openSourceLocation(file, line)"
            />
            <ReportCaseTable
              ref="caseTableRef"
              :report="activeReport"
              :run="run"
              :active-item-id="activeReportItemId"
              :can-open-source="workspace.mode === 'host'"
              @clear-filter="activeReportItemId = ''"
              @show-command="showReportCommand"
              @open-evidence="openReportEvidence"
              @copy-fix="copyReportCaseFix"
              @copy-repro="copyRepro"
              @mark-convention="markCaseConvention"
              @open-source="(file, line) => void workspace.openSourceLocation(file, line)"
            />
            <ReportOpinionAndExport
              :report="activeReport"
              :is-latest="isLatestReport"
              :busy-opinion="busyOpinion"
              :export-result="exportResult"
              @save-opinion="saveOpinion"
              @open-export="openExport"
              @copy-markdown="copyMarkdown"
            />
          </section>
        </template>
      </div>

      <!-- 标签页 3：调用链 -->
      <div v-else class="stack" role="tabpanel" aria-label="调用链">
        <div class="elevated-panel stack-sm">
          <div class="row-between" style="flex-wrap: wrap; gap: 10px;">
            <div>
              <h2 class="section-title">运行调用链</h2>
              <p class="field-hint">
                查看前端到后端与数据层的实际调用，可补充预期断言。
              </p>
            </div>
            <button
              v-if="workspace.mode === 'host'"
              class="btn btn-sm"
              type="button"
              :disabled="linkBusy"
              :aria-busy="linkBusy"
              @click="importHostLinks"
            >
              <Icon name="refresh" :size="14" />
              {{ linkBusy ? "正在采集…" : "采集 / 刷新链路证据" }}
            </button>
          </div>
          <LinkPanel :link="activeLink" :busy="linkBusy" @assert="assertLink" />
        </div>
      </div>

      <!-- 运行详情折叠区 -->
      <details
        v-if="unselected.length || environmentRows.length || unmappedCases.length || repairHistory.length"
        class="details-tech"
      >
        <summary>运行详情（环境、未纳入项、依赖记录）</summary>
        <div class="stack-sm" style="margin-top: 8px;">
          <p v-if="environmentRows.length" class="muted mono">
            运行环境：{{ environmentRows.join(" · ") }}
          </p>
          <p v-if="unmappedCases.length" class="muted">
            无关联计划快照的追加记录 ({{ unmappedCases.length }} 条)：
            {{ unmappedCases.map((c) => c.id).join("、") }}
          </p>
          <div v-if="unselected.length">
            <strong>未纳入本次范围的验收项 ({{ unselected.length }} 项，不计入分母)</strong>
            <ul class="list-plain">
              <li v-for="item in unselected" :key="item.id">{{ item.no }} {{ item.text }}</li>
            </ul>
          </div>
          <div v-if="repairHistory.length">
            <strong>依赖安装历史 ({{ repairHistory.length }} 次)</strong>
            <ul class="list-plain">
              <li v-for="entry in repairHistory" :key="`${entry.at}-${entry.confirmedBy}`">
                {{ new Date(entry.at).toLocaleString("zh-CN") }} · {{ entry.confirmedBy }} ·
                {{ entry.ok ? "成功" : "失败" }}
              </li>
            </ul>
          </div>
        </div>
      </details>
    </div>

    <div class="actionbar">
      <div class="actionbar-inner">
        <div class="actionbar-facts">
          <span class="fact-strong">计划 {{ plan?.no ?? "—" }} · 第 {{ plan?.revision ?? "—" }} 版</span>
          <span>本次 {{ run.counts.selected }} 项 / 共 {{ run.counts.applicable }} 项</span>
        </div>
        <div class="actionbar-actions">
          <button
            v-if="plan && failedAndBlockedItemIds.length > 0"
            class="btn btn-primary"
            type="button"
            :disabled="targetedRerunBusy"
            :aria-busy="targetedRerunBusy"
            @click="targetedRerun"
          >
            {{
              targetedRerunBusy
                ? "正在启动复测…"
                : `直接复测未通过项 (${failedAndBlockedItemIds.length})`
            }}
          </button>
          <button class="btn" type="button" @click="adjustScope">调整范围</button>
          <button class="btn" type="button" @click="rerun">{{ rerunLabel() }}</button>
        </div>
      </div>
    </div>

    <ManualResultDialog
      v-model="manualOpen"
      :test-case="manualCase"
      :busy="manualBusy"
      :allow-files="supportsEvidence"
      :limits="evidenceLimits"
      @save="saveManual"
    />
    <EvidenceDialog
      v-if="supportsEvidence"
      v-model="evidenceOpen"
      :cases="run.cases"
      :default-case-id="evidenceCaseId"
      :busy="workspace.busyKey(`evidence:${run.id}`)"
      :limits="evidenceLimits"
      @save="saveEvidence"
    />
    <ReportDetailDialog
      :detail="detailContent"
      :simulated="Boolean(run?.simulated)"
      :host-mode="workspace.mode === 'host'"
      :project-id="projectId"
      :run-id="run.id"
      @close="detailContent = null"
      @open-host-path="(p) => void workspace.openHostPath(p)"
    />
  </div>
</template>
