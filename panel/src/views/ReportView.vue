<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ErrorState from "@/components/ErrorState.vue";
import LoadingState from "@/components/LoadingState.vue";
import LinkPanel from "@/components/LinkPanel.vue";
import PageHeader from "@/components/PageHeader.vue";
import StatusMark from "@/components/StatusMark.vue";
import StatusText from "@/components/StatusText.vue";
import {
  ANALYSIS_REASON_LABEL,
  ANALYSIS_REASON_TEXT,
  ISSUE_STATUS_LABEL,
  LAYER_LABEL,
  TEMPLATE_DISPOSITION_LABEL,
  VERDICT_LABEL,
} from "@/domain/labels";
import { normalizeReport, reportsForRun } from "@/domain/derive";
import type { EvidenceRef, ExportResult, Issue, LinkData, Report, Run, Task } from "@/domain/types";
import type { LinkAssertInput } from "@/repositories/ProjectRepository";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function coverageLabel(kind: string | null | undefined): string {
  return (
    {
      direct: "直接",
      file: "文件级",
      shared: "共用节点",
      manual: "人工",
      unverified: "未验证",
    }[kind ?? ""] ?? "未记录"
  );
}

function coverageClass(kind: string | null | undefined): string {
  if (kind === "direct") {
    return "is-pass";
  }
  if (kind === "manual") {
    return "is-neutral";
  }
  return "is-warn";
}

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
const mockEvaluated = computed(() => {
  const mock = report.value?.mock;
  return Boolean(mock && mock.real + mock.mock + mock.mixed + mock.unknown > 0);
});
const realChainText = computed(() =>
  report.value?.mock.realChain ? "通过" : mockEvaluated.value ? "未通过" : "未验证",
);
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
const opinionBy = ref("本机用户");
const opinionText = ref("");
const opinionError = ref("");
const exportResult = ref<ExportResult | null>(null);
const detailsOpen = ref(false);

async function revealDetails(): Promise<void> {
  detailsOpen.value = true;
  await nextTick();
  document.querySelector(".report-details")?.scrollIntoView({ block: "start" });
}
const loadingReport = ref(false);
const savingOpinion = ref(false);
const exporting = ref(false);
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
const layers = computed(() => {
  const current = report.value;
  if (!current) {
    return [];
  }
  return (["L1", "L2", "L3"] as const).map((layer) => ({
    layer,
    label: LAYER_LABEL[layer],
    ...current.layers[layer],
  }));
});
const selectedCount = computed(() => report.value?.coverage.selected ?? 0);
const applicableCount = computed(() => report.value?.coverage.applicable ?? 0);
const unselectedCount = computed(() =>
  Math.max(0, applicableCount.value - selectedCount.value),
);
const verdictHeadline = computed(() => {
  const current = report.value;
  if (!current) {
    return "";
  }
  if (current.verdict === "passed") {
    return selectedCount.value < applicableCount.value ? "本范围通过" : "全部通过";
  }
  if (current.verdict === "failed") {
    return selectedCount.value < applicableCount.value ? "本范围存在失败" : "存在失败";
  }
  if (current.verdict === "incomplete") {
    return selectedCount.value < applicableCount.value ? "本范围未完成" : "运行未完成";
  }
  return VERDICT_LABEL[current.verdict];
});
const coverageSegments = computed(() => {
  const current = report.value;
  if (!current || applicableCount.value === 0) {
    return [];
  }
  const total = applicableCount.value;
  const rows = [
    { key: "passed", label: "通过", count: current.counts.passed },
    { key: "failed", label: "失败", count: current.counts.failed },
    { key: "incomplete", label: "未完成", count: current.counts.incomplete },
    { key: "unselected", label: "未纳入", count: unselectedCount.value },
  ];
  return rows
    .filter((row) => row.count > 0)
    .map((row) => ({ ...row, percent: (row.count / total) * 100 }));
});

function parseUncovered(value: string): { id: string; text: string } {
  const separator = value.search(/[：:]/);
  if (separator < 0) {
    return { id: "", text: value };
  }
  const id = value.slice(0, separator).trim();
  const text = value.slice(separator + 1).trim();
  return id.startsWith("ai_") ? { id, text } : { id: "", text: value };
}

function shortMethod(method: string): string {
  const value = (method || "").trim();
  if (!value || value === "manual" || value.startsWith("manual:")) {
    return "人工验证";
  }
  if (value.startsWith("script:")) {
    return `脚本 ${value.slice("script:".length).trim()}`;
  }
  if (value.startsWith("http:")) {
    return "HTTP 请求";
  }
  const command = value.replace(/^(?:command|pytest):\s*/, "");
  const pythonNode = command.match(
    /([^\\/]+\.py)::([A-Za-z0-9_:]+)/,
  );
  if (pythonNode) {
    return `${pythonNode[1]}::${pythonNode[2]}`;
  }
  const playwrightNode = command.match(
    /([^\\/]+\.spec\.ts)\s+--grep\s+"([^"]+)"/,
  );
  if (playwrightNode) {
    return `${playwrightNode[1]} · ${playwrightNode[2]}`;
  }
  const bunTest = command.match(/([^\\/]+\.test\.ts)\b/);
  if (bunTest) {
    return bunTest[1];
  }
  const pythonFile = command.match(/([^\\/]+\.py)\b/);
  if (pythonFile) {
    return pythonFile[1];
  }
  const compact = command.split("&&").pop()?.trim() || command;
  return compact.length > 62 ? `${compact.slice(0, 61)}…` : compact;
}

function evidenceLabel(evidence: EvidenceRef): string {
  const name = evidence.name.split(/[\\/]/).pop() || evidence.name;
  if (name.length > 30 || name.startsWith("run_")) {
    return "查看证据";
  }
  return name;
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
    opinionBy.value = report.value?.opinion?.by ?? "本机用户";
    opinionText.value = report.value?.opinion?.text ?? "";
    opinionError.value = "";
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

async function saveOpinion(): Promise<void> {
  if (!report.value || busyOpinion.value) {
    return;
  }
  if (!opinionText.value.trim()) {
    opinionError.value = "先填写验收意见。";
    return;
  }
  savingOpinion.value = true;
  try {
    await workspace.saveOpinion(
      projectId.value,
      report.value.id,
      opinionBy.value,
      opinionText.value,
    );
    opinionError.value = "";
    ui.showToast("验收意见已记录；结论不变。", "success");
  } catch {
    // store 已提示。
  } finally {
    savingOpinion.value = false;
  }
}

async function importLink(link: LinkData): Promise<void> {
  if (!report.value) {
    return;
  }
  try {
    await workspace.importLink(projectId.value, report.value.runId, link);
    const next = await workspace.regenerateReport(projectId.value, report.value.runId);
    await router.push(`/projects/${projectId.value}/reports/${next.id}`);
    await revealDetails();
  } catch {
    // store 已提示。
  }
}

async function importHostLink(): Promise<void> {
  if (!report.value) {
    return;
  }
  try {
    await workspace.importHostLink(projectId.value, report.value.runId);
    const next = await workspace.regenerateReport(projectId.value, report.value.runId);
    await router.push(`/projects/${projectId.value}/reports/${next.id}`);
    await revealDetails();
  } catch {
    // store 已提示。
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
    await revealDetails();
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

async function openEvidence(evidence: EvidenceRef): Promise<void> {
  if (workspace.mode === "host" && evidence.sourcePath) {
    try {
      await workspace.openHostPath(evidence.sourcePath);
      return;
    } catch {
      // 宿主打不开时退回复制引用。
    }
  }
  const reference = evidence.sourcePath || evidence.name;
  try {
    await navigator.clipboard.writeText(reference);
    ui.showToast(`证据引用已复制：${reference}`, "success");
  } catch {
    ui.showToast(`证据引用：${reference}（复制失败，请手动选择）`, "error");
  }
}

function bytesText(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} 字节`;
  }
  return `${(bytes / 1024).toFixed(1)} KB`;
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
  <div v-else class="page">
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

    <p v-if="environmentRows.length" class="run-environment muted">
      运行环境：{{ environmentRows.join(" · ") }}
    </p>

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

    <section class="report-group" aria-label="结论与验收">
    <section class="section">
      <div class="result-head report-hero">
        <div>
          <p class="page-kicker">本次结论</p>
          <p class="result-verdict report-hero-title" :class="`is-${report.verdict}`">
            {{ verdictHeadline }}
          </p>
          <p class="report-coverage">
            已选 <strong class="num">{{ selectedCount }}</strong> /
            <strong class="num">{{ applicableCount }}</strong> 项
            <span v-if="unselectedCount">· 未纳入 {{ unselectedCount }} 项</span>
          </p>
          <p class="report-conclusion-text">{{ report.conclusion }}</p>
          <div
            v-if="coverageSegments.length"
            class="scope-progress"
            role="img"
            :aria-label="`本次覆盖 ${selectedCount} / ${applicableCount} 项，未纳入 ${unselectedCount} 项`"
          >
            <span
              v-for="segment in coverageSegments"
              :key="segment.key"
              class="scope-progress-segment"
              :class="`is-${segment.key}`"
              :style="{ width: `${segment.percent}%` }"
              :title="`${segment.label} ${segment.count}`"
            />
          </div>
          <div v-if="coverageSegments.length" class="scope-legend">
            <span v-for="segment in coverageSegments" :key="segment.key">
              <i class="scope-dot" :class="`is-${segment.key}`" />
              {{ segment.label }} {{ segment.count }}
            </span>
          </div>
          <div class="report-trust">
            <span class="badge is-neutral">证据 {{ report.evidenceIndex.length }}</span>
            <span class="badge is-neutral">调用链 {{ report.link?.nodes.length ?? 0 }} 节点</span>
            <span class="badge is-neutral">模拟 {{ report.mock.mock }} 条</span>
            <span
              class="badge"
              :class="
                report.mock.realChain ? 'is-pass' : mockEvaluated ? 'is-warn' : 'is-neutral'
              "
            >
              真实链路{{ realChainText }}
            </span>
          </div>
        </div>
        <div class="result-counts" role="group" aria-label="报告计数">
          <div class="result-count is-passed">
            <strong>{{ report.counts.passed }}</strong>
            <span>通过</span>
          </div>
          <div class="result-count is-failed">
            <strong>{{ report.counts.failed }}</strong>
            <span>失败</span>
          </div>
          <div class="result-count is-incomplete">
            <strong>{{ report.counts.incomplete }}</strong>
            <span>未完成</span>
          </div>
          <div class="result-count is-unselected">
            <strong>{{ unselectedCount }}</strong>
            <span>未纳入</span>
          </div>
        </div>
      </div>
    </section>

    <section class="section">
      <h2 class="section-title-lg">范围与覆盖</h2>
      <div class="table-wrap" tabindex="0" role="region" aria-label="范围与覆盖表">
        <table class="table">
          <thead>
            <tr>
              <th scope="col">编号</th>
              <th scope="col">验收项</th>
              <th scope="col">最差结果</th>
              <th scope="col">用例数</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in report.itemDetails.filter((entry) => entry.selected)"
              :key="item.itemId"
            >
              <td class="cell-num" data-label="编号">{{ item.no }}</td>
              <td class="cell-main" data-label="验收项">{{ item.text }}</td>
              <td data-label="最差结果">
                <StatusMark :result="item.outcome" />
                <StatusText :result="item.outcome" />
              </td>
              <td data-label="用例数">{{ item.caseIds.length }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <details v-if="report.uncoveredItems.length" class="unselected-block">
        <summary>
          未纳入本次 {{ report.uncoveredItems.length }} 项 <span class="muted">不计入结论</span>
        </summary>
        <div class="coverage-chip-list">
          <span
            v-for="item in report.uncoveredItems"
            :key="item"
            class="coverage-chip"
            :title="parseUncovered(item).id || item"
          >
            {{ parseUncovered(item).text }}
          </span>
        </div>
      </details>
      <details v-if="report.unverifiedItems.length" class="unselected-block">
        <summary>
          未核实预期 {{ report.unverifiedItems.length }} 条
          <span class="muted">执行通过也按阻塞计</span>
        </summary>
        <ul class="list-plain">
          <li v-for="item in report.unverifiedItems" :key="item">{{ item }}</li>
        </ul>
      </details>
      <details v-if="report.planWarnings?.length" class="unselected-block">
        <summary>计划警告 {{ report.planWarnings.length }} 条</summary>
        <ul class="list-plain">
          <li v-for="item in report.planWarnings ?? []" :key="item">{{ item }}</li>
        </ul>
      </details>
    </section>

    <section class="section">
      <h2 class="section-title-lg">
        项目级检查 <span class="section-count">{{ report.l1.length }}</span>
      </h2>
      <div v-if="report.l1.length" class="check-list">
        <div
          v-for="check in report.l1"
          :key="check.key"
          class="check-row check-row-decorated"
          :class="`is-${check.result}`"
        >
          <StatusMark :result="check.result" />
          <span class="check-name">{{ check.name }}</span>
          <code class="check-method-chip">{{ check.method }}</code>
          <StatusText :result="check.result" />
          <span v-if="check.reason" class="muted">{{ check.reason }}</span>
        </div>
      </div>
      <p v-else class="report-empty-note">
        本次没有项目级检查记录；快速检查或计划中含 L1 用例时会显示在这里。
      </p>
    </section>

    <section class="section">
      <h2 class="section-title-lg">分层计数</h2>
      <div class="layer-pills">
        <span
          v-for="layer in layers"
          :key="layer.layer"
          class="layer-pill"
          :class="{ 'is-empty': layer.total === 0 }"
        >
          <strong>{{ layer.label }}</strong>
          {{ layer.passed }}/{{ layer.total }} 通过
          <em v-if="layer.failed">· {{ layer.failed }} 失败</em>
          <em v-if="layer.incomplete">· {{ layer.incomplete }} 未完成</em>
        </span>
      </div>
    </section>

    <section class="section">
      <h2 class="section-title-lg">
        用例明细 <span class="section-count">{{ report.caseDetails.length }}</span>
      </h2>
      <div v-if="report.caseDetails.length" class="table-wrap" tabindex="0" role="region" aria-label="用例明细表">
        <table class="table">
          <thead>
            <tr>
              <th scope="col">验收项</th>
              <th scope="col">用例</th>
              <th scope="col">检查方式</th>
              <th scope="col">覆盖</th>
              <th scope="col">结果</th>
              <th scope="col">证据</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="testCase in report.caseDetails"
              :key="testCase.caseId"
              :class="`is-${testCase.result}`"
            >
              <td class="cell-num" data-label="验收项">{{ testCase.itemNo }}</td>
              <td class="cell-main" data-label="用例">
                <details class="case-report-detail">
                  <summary>{{ testCase.title }}</summary>
                  <p><strong>预期</strong>：{{ testCase.expected || "未记录" }}</p>
                  <p><strong>实际</strong>：{{ testCase.actual || "未记录" }}</p>
                  <p v-if="testCase.analysis">
                    <strong>分析</strong>：{{ testCase.analysis.observed }}
                  </p>
                </details>
              </td>
              <td data-label="检查方式">
                <code class="method-short" :title="testCase.method">{{
                  shortMethod(testCase.method)
                }}</code>
              </td>
              <td data-label="覆盖">
                <span class="badge" :class="coverageClass(testCase.coverageKind)">
                  {{ coverageLabel(testCase.coverageKind) }}
                </span>
              </td>
              <td data-label="结果">
                <StatusMark :result="testCase.result" />
                <StatusText :result="testCase.result" />
              </td>
              <td data-label="证据">
                <template v-if="testCase.evidence.length">
                  <button
                    class="link-btn"
                    type="button"
                    :title="testCase.evidence[0]?.sourcePath || testCase.evidence[0]?.name"
                    @click="openEvidence(testCase.evidence[0]!)"
                  >
                    {{ evidenceLabel(testCase.evidence[0]!) }}
                  </button>
                  <span v-if="testCase.evidence.length > 1" class="muted">
                    +{{ testCase.evidence.length - 1 }}
                  </span>
                </template>
                <span v-else class="muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="doc-note">这一版没有保留用例明细。</p>
    </section>

    <section v-if="issues.length" class="section">
      <h2 class="section-title-lg">问题</h2>
      <div class="list-plain">
        <p v-for="issue in issues" :key="issue.id">
          <button
            class="link-btn"
            type="button"
            @click="router.push(`/projects/${projectId}/issues/${issue.id}`)"
          >
            {{ issue.severity }} · {{ issue.title }}
          </button>
          <span class="muted"> · {{ ISSUE_STATUS_LABEL[issue.status] }}</span>
        </p>
      </div>
    </section>

    <section class="section">
      <h2 class="section-title-lg">验收意见</h2>
      <div v-if="report.opinion" class="notice">
        <span
          ><strong>{{ report.opinion.by }}</strong> · {{ formatTime(report.opinion.at) }} ·
          {{ report.opinion.text }}</span
        >
      </div>
      <form v-if="isLatest" class="opinion-form" novalidate @submit.prevent="saveOpinion">
        <div class="form-grid">
          <div class="field">
            <label for="opinion-by">署名</label>
            <input id="opinion-by" v-model="opinionBy" type="text" maxlength="40" />
          </div>
        </div>
        <div class="field" :class="{ 'has-error': opinionError }">
          <label for="opinion-text">意见</label>
          <textarea
            id="opinion-text"
            v-model="opinionText"
            rows="3"
            placeholder="例如：删除标记修复后需重跑第 3、4 项，再决定是否上线。"
            :aria-invalid="opinionError ? 'true' : undefined"
            :aria-describedby="opinionError ? 'opinion-error' : undefined"
            @input="opinionError = ''"
          />
          <p v-if="opinionError" id="opinion-error" class="field-error" role="alert">
            {{ opinionError }}
          </p>
        </div>
        <div class="form-actions">
          <button
            class="btn btn-primary"
            type="submit"
            :disabled="busyOpinion"
            :aria-busy="busyOpinion"
          >
            {{ busyOpinion ? "正在保存…" : "保存意见" }}
          </button>
        </div>
      </form>
      <p v-else class="doc-note">旧版本只读，不能在旧版本上填写验收意见。</p>
    </section>

    </section>

    <details
      class="report-details"
      :open="detailsOpen"
      @toggle="detailsOpen = ($event.target as HTMLDetailsElement).open"
    >
      <summary>
        <span>运行明细</span>
      </summary>
    <section class="section">
      <h2 class="section-title-lg">模拟数据判断</h2>
      <p class="doc-p">
        真实 {{ report.mock.real }} · 模拟 {{ report.mock.mock }} · 混合 {{ report.mock.mixed }} ·
        无法判断 {{ report.mock.unknown }}。真实链路：{{ realChainText }}。{{
          report.mock.note
        }}
      </p>
      <p class="doc-note">
        含模拟或无法判断依赖的用例，不能获得“真实链路通过”。该判断不可手工修改。
      </p>
    </section>

    <section v-if="report.analysis.status === 'generated'" class="section">
      <div class="section-head">
        <h2 class="section-title-lg">失败分析</h2>
        <span class="badge is-neutral">本地分析 · 不改变结论</span>
      </div>
      <p class="doc-p">{{ report.analysis.observed }}</p>
      <ul class="list-plain">
        <li v-for="cause in report.analysis.candidateCauses" :key="cause.cause">
          候选原因（把握：{{ cause.confidence }}）：{{ cause.cause }}
        </li>
        <li v-for="item in report.analysis.unverified" :key="item">未验证：{{ item }}</li>
        <li v-for="item in report.analysis.nextSteps" :key="item">下一步：{{ item }}</li>
      </ul>
    </section>
    <section v-else class="section">
      <div class="section-head">
        <h2 class="section-title-lg">失败分析</h2>
        <span class="badge is-neutral">
          {{ ANALYSIS_REASON_LABEL[report.analysis.status] }}
        </span>
      </div>
      <p class="doc-p">
        {{ ANALYSIS_REASON_TEXT[report.analysis.reason] || "分析未生成；结论与计数不受影响。" }}
      </p>
      <p class="doc-note">
        需要模型分析时，在设置里检查模型配置后重新生成报告；不会改变本次结论。
      </p>
    </section>

    <section class="section">
      <h2 class="section-title-lg">调用链</h2>
      <LinkPanel
        v-if="run"
        :link="report.link"
        :busy="linkBusy"
        :host-mode="workspace.mode === 'host'"
        @load="importLink"
        @host-import="importHostLink"
        @assert="assertLink"
      />
      <p v-else class="doc-note">运行记录不存在，无法导入链路。</p>
    </section>

    <section v-if="report.templates.length" class="section">
      <h2 class="section-title-lg">检查模板</h2>
      <div v-if="report.templates.length" class="stack">
        <div v-for="template in report.templates" :key="template.templateId">
          <p class="doc-p">
            <strong>{{ template.name }} v{{ template.version }}</strong> · 适用
            {{ template.summary.applicable }} · 不适用 {{ template.summary.not_applicable }} ·
            不支持 {{ template.summary.unsupported }} · 无法判断 {{ template.summary.unknown }}
          </p>
          <ul class="list-plain">
            <li v-for="item in template.items" :key="item.id">
              {{ item.objective }} · {{ TEMPLATE_DISPOSITION_LABEL[item.disposition] }} ·
              {{ item.reason }}
            </li>
          </ul>
        </div>
      </div>
    </section>

    <section v-if="report.modules.length || report.unmapped.length" class="section">
      <h2 class="section-title-lg">涉及模块与文件</h2>
      <div v-if="report.modules.length" class="list-plain">
        <p v-for="module in report.modules" :key="module.name">
          {{ module.name }} · {{ module.count }} 条检查
        </p>
      </div>
      <p v-if="report.unmapped.length" class="doc-note mono">
        未映射文件：{{ report.unmapped.join("、") }}
      </p>
    </section>

    <section v-if="report.evidenceIndex.length" class="section">
      <h2 class="section-title-lg">证据索引</h2>
      <div v-if="report.evidenceIndex.length" class="evidence-list">
        <div v-for="evidence in report.evidenceIndex" :key="evidence.id" class="evidence-item">
          <span aria-hidden="true">•</span>
          <span>
            <span class="evidence-name">{{ evidence.name }}</span>
            <span class="evidence-meta">
              {{ evidence.mediaType }} · {{ evidence.bytes }} 字节 · {{ evidence.sha256 }}
              <template v-if="evidence.note"> · {{ evidence.note }}</template>
            </span>
          </span>
          <span
            class="badge"
            :class="evidence.missing ? 'is-fail' : evidence.truncated ? 'is-warn' : 'is-neutral'"
          >
            {{ evidence.missing ? "已缺失" : evidence.truncated ? "已截断" : "已登记" }}
          </span>
          <button
            class="link-btn"
            type="button"
            :aria-label="`打开或复制证据 ${evidence.name}`"
            @click="openEvidence(evidence)"
          >
            {{ workspace.mode === "host" && evidence.sourcePath ? "打开" : "复制引用" }}
          </button>
        </div>
      </div>
    </section>

    <section class="section">
      <h2 class="section-title-lg">来源与环境</h2>
      <dl class="info-grid">
        <div class="info-cell">
          <dt>计划</dt>
          <dd>{{ report.source.planLabel }}</dd>
        </div>
        <div class="info-cell">
          <dt>规则版本</dt>
          <dd>{{ report.source.rulesLabel }}</dd>
        </div>
        <div class="info-cell">
          <dt>源码提交</dt>
          <dd>{{ report.source.commit }}</dd>
        </div>
        <div class="info-cell">
          <dt>模型</dt>
          <dd>{{ report.source.model }}</dd>
        </div>
        <div class="info-cell">
          <dt>GitHub</dt>
          <dd>{{ report.source.github }}</dd>
        </div>
        <div class="info-cell">
          <dt>报告版本</dt>
          <dd>第 {{ report.version }} 版</dd>
        </div>
      </dl>
    </section>

    </details>

    <section class="section">
      <details class="unselected-block">
        <summary>
          {{
            report.summarySource === "ai"
              ? "AI 摘要 · 不改变结论"
              : report.summarySource === "none"
                ? "摘要未生成 · 不改变结论"
                : "本地摘要（模型未参与） · 不改变结论"
          }}
        </summary>
        <p class="doc-p">{{ report.summary || "本次没有生成摘要。" }}</p>
      </details>
    </section>

    <section v-if="exportResult" class="section">
      <h2 class="section-title-lg">导出结果</h2>
      <dl class="info-grid">
        <div class="info-cell">
          <dt>文件</dt>
          <dd class="mono">{{ exportResult.filename }}</dd>
        </div>
        <div class="info-cell">
          <dt>大小</dt>
          <dd>{{ bytesText(exportResult.bytes) }}</dd>
        </div>
        <div class="info-cell">
          <dt>行数</dt>
          <dd>{{ exportResult.lines }}</dd>
        </div>
        <div class="info-cell">
          <dt>sha256</dt>
          <dd class="mono">{{ exportResult.sha256 }}</dd>
        </div>
      </dl>
      <div v-if="exportResult.path" class="notice">
        <span class="mono">{{ exportResult.path }}</span>
        <button class="link-btn" type="button" @click="openExport">用系统程序打开</button>
        <p class="field-hint">
          宿主模式由 CLI 写入工作空间文件；面板不提供 Markdown 预览或复制，请在打开的文件中查看。
        </p>
      </div>
      <details v-else class="unselected-block">
        <summary>Markdown 预览</summary>
        <textarea class="markdown-preview" readonly :value="exportResult.markdown" />
        <div class="form-actions">
          <button class="btn btn-sm" type="button" @click="copyMarkdown">复制 Markdown</button>
        </div>
      </details>
    </section>
  </div>
</template>
