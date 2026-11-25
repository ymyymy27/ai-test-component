<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ErrorState from "@/components/ErrorState.vue";
import LoadingState from "@/components/LoadingState.vue";
import PageHeader from "@/components/PageHeader.vue";
import ReportAppendixSection from "@/components/report/ReportAppendixSection.vue";
import ReportCaseTable from "@/components/report/ReportCaseTable.vue";
import ReportConclusionSection from "@/components/report/ReportConclusionSection.vue";
import ReportDetailDialog from "@/components/report/ReportDetailDialog.vue";
import ReportOpinionAndExport from "@/components/report/ReportOpinionAndExport.vue";
import ReportScopeAndChecks from "@/components/report/ReportScopeAndChecks.vue";
import { commandText, type ReportDetail } from "@/components/report/reportFormat";
import { buildCaseFixPrompt } from "@/domain/aiPrompt";
import { normalizeReport, reportsForRun } from "@/domain/derive";
import type { EvidenceRef, ExportResult, Issue, Report, ReportCaseDetail, Run, Task } from "@/domain/types";
import type { LinkAssertInput } from "@/repositories/ProjectRepository";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const reportId = computed(() => String(route.params.reportId ?? ""));
const project = computed(() => workspace.currentProject);
const report = computed<Report | null>(() => {
  const found = project.value?.reports.find((entry) => entry.id === reportId.value) ?? null;
  return found ? normalizeReport(found) : null;
});
const task = computed<Task | null>(
  () => project.value?.tasks.find((entry) => entry.id === report.value?.taskId) ?? null,
);
const run = computed<Run | null>(
  () => task.value?.runs.find((entry) => entry.id === report.value?.runId) ?? null,
);
const environmentRows = computed(() => {
  const environment = run.value?.environment ?? {};
  return [
    environment["python_version"] ? `Python ${environment["python_version"]}` : "",
    environment["venv"] ? `venv ${environment["venv"]}` : "",
    environment["node"] ? `Node ${environment["node"]}` : "",
  ].filter(Boolean);
});
const versions = computed(() =>
  project.value && report.value ? reportsForRun(project.value, report.value.runId) : [],
);
const isLatest = computed(() => versions.value[0]?.id === report.value?.id);
const issues = computed<Issue[]>(() =>
  report.value
    ? report.value.issues
        .map((issueId) => project.value?.issues.find((issue) => issue.id === issueId))
        .filter((issue): issue is Issue => Boolean(issue))
    : [],
);

const exportResult = ref<ExportResult | null>(null);
const detailContent = ref<ReportDetail | null>(null);
const loadingReport = ref(false);
const savingOpinion = ref(false);
const exporting = ref(false);
const activeItemId = ref("");
const caseTableRef = ref<InstanceType<typeof ReportCaseTable> | null>(null);

const busyOpinion = computed(
  () =>
    savingOpinion.value ||
    Boolean(report.value && workspace.busyKey(`opinion:${report.value.id}`)),
);
const linkBusy = computed(
  () =>
    Boolean(run.value) &&
    (workspace.busyKey(`link:${run.value?.id}`) ||
      workspace.busyKey(`link-assert:${run.value?.id}`)),
);
const regenerating = computed(() =>
  report.value ? workspace.busyKey(`report:${report.value.runId}`) : false,
);

function scrollToSection(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function goOpinion(): void {
  scrollToSection("report-opinion");
  window.setTimeout(() => {
    document.getElementById("opinion-text")?.focus({ preventScroll: true });
  }, 420);
}

function showCommand(testCase: ReportCaseDetail): void {
  detailContent.value = {
    kind: "command",
    title: testCase.title,
    command: commandText(testCase.method),
    root: project.value?.root ?? "",
  };
}

function openEvidence(evidence: EvidenceRef): void {
  detailContent.value = { kind: "evidence", evidence };
}

async function openHostPath(path: string): Promise<void> {
  try {
    await workspace.openHostPath(path);
  } catch {
    ui.showToast("打不开证据文件；可复制路径后手动打开。", "error");
  }
}

async function copyCaseFix(testCase: ReportCaseDetail): Promise<void> {
    const matchedCase = run.value?.cases.find((entry) => entry.id === testCase.caseId);
    const itemDetail = report.value?.itemDetails.find(
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

function filterCasesByItem(itemId: string): void {
  activeItemId.value = activeItemId.value === itemId ? "" : itemId;
  if (!activeItemId.value) {
    return;
  }
  void nextTick(() => {
    caseTableRef.value?.scrollIntoView();
  });
}

async function copyTextWithToast(text: string, okText: string): Promise<void> {
  try {
    if (!navigator.clipboard?.writeText) {
      ui.showToast("当前环境不支持剪贴板写入。", "error");
      return;
    }
    await navigator.clipboard.writeText(text);
    ui.showToast(okText, "success");
  } catch {
    ui.showToast("复制失败，请检查剪贴板权限。", "error");
  }
}

async function copyRepro(command: string): Promise<void> {
  await copyTextWithToast(command, "最小复现命令已复制到剪贴板。");
}

async function markCaseConvention(testCase: ReportCaseDetail): Promise<void> {
  const defaultPattern = (testCase.actual || testCase.title || testCase.caseId).trim();
  const reason = await ui.askConfirm({
    title: "标为项目正常约定（写入错题本）",
    body: `后续如果再次出现匹配“${defaultPattern.slice(0, 64)}”的报错，探针和裁判将按正常约定豁免降噪。`,
    confirmLabel: "加入错题本",
    requireText: true,
    textLabel: "正常约定原因",
    placeholder: "例如：开发态预留未实现，非生产缺陷",
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

function openSource(file: string, line: number): void {
  void workspace.openSourceLocation(file, line);
}

onMounted(async () => {
  if (task.value && workspace.currentTask?.id !== task.value.id) {
    try {
      await workspace.setActiveTask(projectId.value, task.value.id);
    } catch {
      // store 已提示。
    }
  }
  if (!report.value) {
    loadingReport.value = true;
    try {
      await workspace.loadReport(projectId.value, reportId.value);
    } finally {
      loadingReport.value = false;
    }
  }
  if (report.value) {
    await workspace.ensureReportVersions(projectId.value, report.value.runId);
  }
});

watch(
  () => report.value?.id,
  () => {
    exportResult.value = null;
  },
  { immediate: true },
);

async function regenerate(): Promise<void> {
  if (!report.value) {
    return;
  }
  try {
    const next = await workspace.regenerateReport(projectId.value, report.value.runId);
    await router.push(`/projects/${projectId.value}/reports/${next.id}`);
  } catch {
    // store 已提示。
  }
}

async function exportReport(): Promise<void> {
  if (!report.value || exporting.value) {
    return;
  }
  exporting.value = true;
  try {
    exportResult.value = await workspace.exportReport(projectId.value, report.value.id);
  } catch {
    // store 已提示。
  } finally {
    exporting.value = false;
  }
}

async function saveOpinion(payload: { by: string; text: string }): Promise<void> {
  if (!report.value || busyOpinion.value) {
    return;
  }
  savingOpinion.value = true;
  try {
    await workspace.saveOpinion(projectId.value, report.value.id, payload.by, payload.text);
    ui.showToast("验收意见已记录；结论不变。", "success");
  } catch {
    // store 已提示。
  } finally {
    savingOpinion.value = false;
  }
}

async function assertLink(input: LinkAssertInput): Promise<void> {
  if (!report.value) {
    return;
  }
  try {
    await workspace.assertLink(projectId.value, report.value.runId, input);
    const next = await workspace.regenerateReport(projectId.value, report.value.runId);
    await router.push(`/projects/${projectId.value}/reports/${next.id}`);
    await nextTick();
    document.querySelector("#report-appendix")?.scrollIntoView({ block: "start" });
  } catch {
    // store 已提示。
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
    ui.showToast("Markdown 已复制。", "success");
  } catch {
    ui.showToast("复制失败，请手动选择文本。", "error");
  }
}
</script>

<template>
  <div v-if="loadingReport" class="page">
    <LoadingState text="正在读取报告…" />
  </div>
  <div v-else-if="!report || !task" class="page">
    <ErrorState
      title="报告不存在"
      text="这个报告可能已被移除；回到历史或结果页重新打开。"
      retry-label="回到历史"
      @retry="router.push(`/projects/${projectId}/history`)"
    />
  </div>
  <div v-else class="page report-page">
    <PageHeader
      title="测试报告"
      :subtitle="`${project?.name ?? ''} · ${task.title} · 第 ${report.version} 版 · ${new Date(report.generatedAt).toLocaleString('zh-CN')}`"
    >
      <template #actions>
        <span v-if="exportResult" class="report-export-receipt">
          已导出 {{ exportResult.filename }}
        </span>
        <button
          v-if="run && task"
          class="btn btn-sm btn-ghost"
          type="button"
          @click="router.push(`/projects/${projectId}/tasks/${task.id}/result?run=${report.runId}`)"
        >
          返回运行结果
        </button>
        <button
          class="btn btn-sm btn-ghost"
          type="button"
          :disabled="regenerating"
          :aria-busy="regenerating"
          @click="regenerate"
        >
          {{ regenerating ? "正在生成…" : "重新生成" }}
        </button>
        <button
          class="btn btn-sm btn-primary"
          type="button"
          :disabled="exporting"
          :aria-busy="exporting"
          @click="exportReport"
        >
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

    <details v-if="environmentRows.length" class="details-tech">
      <summary>查看运行环境审计记录</summary>
      <p class="run-environment muted mono">{{ environmentRows.join(" · ") }}</p>
    </details>

    <div v-if="versions.length > 1" class="toolbar" role="group" aria-label="报告版本">
      <span class="section-note">版本</span>
      <button
        v-for="version in versions"
        :key="version.id"
        class="filter-btn"
        :class="{ 'is-active': version.id === report.id }"
        type="button"
        :aria-pressed="version.id === report.id"
        @click="router.push(`/projects/${projectId}/reports/${version.id}`)"
      >
        第 {{ version.version }} 版
      </button>
    </div>

    <div v-if="!isLatest || report.status === 'superseded'" class="notice is-old">
      <span>正在查看旧版本：内容只读，结论与计数保留当时结果。</span>
    </div>

    <nav class="report-nav" aria-label="报告章节">
      <button type="button" @click="scrollToSection('report-conclusion')">结论</button>
      <button type="button" @click="scrollToSection('report-scope')">范围</button>
      <button type="button" @click="scrollToSection('report-checks')">检查</button>
      <button type="button" @click="scrollToSection('case-details')">用例</button>
      <button type="button" @click="scrollToSection('report-appendix')">追溯</button>
      <button type="button" @click="goOpinion">填写意见</button>
    </nav>

    <section class="report-group" aria-label="结论与验收">
      <ReportConclusionSection :report="report" />
      <ReportScopeAndChecks
        :report="report"
        :active-item-id="activeItemId"
        :can-open-source="workspace.mode === 'host'"
        @filter-item="filterCasesByItem"
        @open-evidence="openEvidence"
        @add-convention="addProbeConvention"
        @open-source="openSource"
      />
      <ReportCaseTable
        ref="caseTableRef"
        :report="report"
        :run="run"
        :active-item-id="activeItemId"
        :can-open-source="workspace.mode === 'host'"
        @clear-filter="activeItemId = ''"
        @show-command="showCommand"
        @open-evidence="openEvidence"
        @copy-fix="copyCaseFix"
        @copy-repro="copyRepro"
        @mark-convention="markCaseConvention"
        @open-source="openSource"
      />
      <ReportAppendixSection
        :report="report"
        :run="run"
        :issues="issues"
        :link-busy="linkBusy"
        @assert-link="assertLink"
        @open-evidence="openEvidence"
        @open-issue="(issueId) => router.push(`/projects/${projectId}/issues/${issueId}`)"
      />
      <ReportOpinionAndExport
        :report="report"
        :is-latest="isLatest"
        :busy-opinion="busyOpinion"
        :export-result="exportResult"
        @save-opinion="saveOpinion"
        @open-export="openExport"
        @copy-markdown="copyMarkdown"
      />
    </section>

    <ReportDetailDialog
      :detail="detailContent"
      :simulated="Boolean(run?.simulated)"
      :host-mode="workspace.mode === 'host'"
      :project-id="projectId"
      :run-id="report.runId"
      @close="detailContent = null"
      @open-host-path="openHostPath"
    />
  </div>
</template>
