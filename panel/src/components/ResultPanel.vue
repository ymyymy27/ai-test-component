<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import { openHostPath } from "@/api/host";
import {
  CASE_RESULT_LABEL,
  LAYER_LABEL,
  RUN_STATUS_BADGE,
  RUN_STATUS_LABEL,
  formatDateTime,
  methodLabel,
} from "@/api/labels";
import type {
  CaseResult,
  EvidenceItem,
  Opinion,
  PlanCase,
  RunCase,
  TemplateDisposition,
} from "@/api/types";
import AnalysisPanel from "@/components/AnalysisPanel.vue";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import CaseRow from "@/components/CaseRow.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Field from "@/components/Field.vue";
import Icon from "@/components/Icon.vue";
import IssueList from "@/components/IssueList.vue";
import ManualRecord from "@/components/ManualRecord.vue";
import ResultSummary from "@/components/ResultSummary.vue";
import {
  generateAnalysis,
  isBusy,
  newTarget,
  openEvidenceDrawer,
  rerun,
  rerunItem,
  session,
} from "@/state/session";

interface EvidenceTile {
  ref: string;
  name: string;
  bytes: number;
  exists: boolean;
  path: string;
  missing: string;
  known: boolean;
}

const evidenceItems = ref<EvidenceItem[]>([]);
const actionNote = ref("");
const rerunError = ref<FriendlyError | null>(null);

const showExport = ref(false);
const exportPath = ref("");
const exporting = ref(false);
const exportError = ref<FriendlyError | null>(null);
const exported = ref("");

const opinionBy = ref("");
const opinionText = ref("");
const opinionError = ref<FriendlyError | null>(null);
const savingOpinion = ref(false);
const savedOpinion = ref<Opinion | null>(null);

let reportId = "";

const DISPOSITION_LABEL: Record<TemplateDisposition, string> = {
  applicable: "适用",
  not_applicable: "不适用",
  unsupported: "不支持",
  unknown: "未知",
};

const run = computed(() => session.result?.run ?? null);
const runId = computed(() => run.value?.run_id ?? "");
const outcome = computed(() => session.result?.outcome ?? null);
const selected = computed(() => run.value?.scope.acceptance_item_ids.length ?? 0);
const applicable = computed(() => session.plan?.applicable_count ?? 0);
const uncoveredItems = computed(() => session.result?.uncovered_items ?? []);
const canCloseIssues = computed(() => Boolean(run.value && run.value.status !== "running"));

const planCases = computed(() => {
  const map = new Map<string, PlanCase>();
  for (const planCase of session.plan?.cases ?? []) {
    map.set(planCase.case_id, planCase);
  }
  return map;
});

const itemTexts = computed(() => {
  const map = new Map<string, string>();
  for (const item of session.task?.acceptance_items ?? []) {
    map.set(item.item_id, item.text);
  }
  return map;
});

interface JoinedCase {
  index: number;
  runCase: RunCase;
  planCase?: PlanCase;
  itemText?: string;
}

const joined = computed<JoinedCase[]>(() =>
  (run.value?.cases ?? []).map((runCase, index) => {
    const planCase = planCases.value.get(runCase.case_id);
    return {
      index,
      runCase,
      planCase,
      itemText: planCase?.acceptance_item_id
        ? itemTexts.value.get(planCase.acceptance_item_id)
        : undefined,
    };
  }),
);

const failed = computed(() => joined.value.filter((item) => item.runCase.result === "failed"));
const totalCases = computed(() => joined.value.length);
const stalled = computed(() =>
  joined.value.filter(
    (item) =>
      item.runCase.result === "blocked" ||
      item.runCase.result === "error" ||
      item.runCase.result === "manual_pending" ||
      item.runCase.result === "not_run",
  ),
);

const analysis = computed(() => run.value?.analysis?.[0]);
const canAnalyze = computed(() => failed.value.length > 0 || stalled.value.length > 0);

type TickTone = "passed" | "failed" | "incomplete" | "selected" | "unselected";

const taskItems = computed(() =>
  (session.task?.acceptance_items ?? []).filter(
    (item) => item.observable && item.status === "active",
  ),
);
const projectMode = computed(
  () => applicable.value === 0 && taskItems.value.length === 0,
);
const effectiveApplicable = computed(() =>
  applicable.value > 0 ? applicable.value : taskItems.value.length,
);

const caseCounts = computed(() => {
  const counts = { passed: 0, failed: 0, incomplete: 0, manual: 0 };
  for (const row of joined.value) {
    switch (row.runCase.result) {
      case "passed":
        counts.passed += 1;
        break;
      case "failed":
        counts.failed += 1;
        break;
      case "manual_pending":
        counts.manual += 1;
        break;
      case "blocked":
      case "error":
      case "not_run":
        counts.incomplete += 1;
        break;
    }
  }
  return counts;
});

const itemOutcomes = computed(() => {
  const rank: Record<CaseResult, number> = {
    failed: 4,
    blocked: 3,
    error: 3,
    not_run: 3,
    manual_pending: 3,
    passed: 2,
  };
  const outcomes = new Map<string, CaseResult>();
  for (const row of joined.value) {
    const itemId = row.planCase?.acceptance_item_id;
    if (!itemId) {
      continue;
    }
    const current = outcomes.get(itemId);
    if (!current || rank[row.runCase.result] > rank[current]) {
      outcomes.set(itemId, row.runCase.result);
    }
  }
  return outcomes;
});

function resultTone(result: CaseResult): TickTone {
  if (result === "failed") {
    return "failed";
  }
  if (result === "passed") {
    return "passed";
  }
  return "incomplete";
}

const selectedIds = computed(
  () => new Set(run.value?.scope.acceptance_item_ids ?? []),
);

const summaryTicks = computed(() => {
  if (!projectMode.value) {
    const items = taskItems.value;
    if (items.length === 0) {
      return [];
    }
    return items.map((item) => {
      if (!selectedIds.value.has(item.item_id)) {
        return { tone: "unselected" as TickTone, label: `${item.text}：未纳入本次范围` };
      }
      const result = itemOutcomes.value.get(item.item_id) ?? "not_run";
      return {
        tone: resultTone(result),
        label: `${item.text}：${CASE_RESULT_LABEL[result]}`,
      };
    });
  }
  return joined.value.map((row) => ({
    tone: resultTone(row.runCase.result),
    label: `${row.planCase?.title || "项目检查"}：${CASE_RESULT_LABEL[row.runCase.result]}`,
  }));
});

const pendingItems = computed(() => {
  if (projectMode.value) {
    return 0;
  }
  let count = 0;
  for (const item of taskItems.value) {
    if (!selectedIds.value.has(item.item_id)) {
      continue;
    }
    const result = itemOutcomes.value.get(item.item_id) ?? "not_run";
    if (result !== "passed" && result !== "failed") {
      count += 1;
    }
  }
  return count;
});

const summaryNote = computed(() => {
  if (run.value?.status !== "cancelled") {
    return "";
  }
  if (caseCounts.value.failed > 0) {
    return "运行在用例边界取消；已完成用例仍参与结论。";
  }
  return "运行已取消；已完成用例与证据保留。";
});

const confirmationLabel = computed(() => {
  const value = run.value;
  if (!value?.confirmed_by || !value.confirmed_at) {
    return "";
  }
  return `范围已由 ${value.confirmed_by} 于 ${formatDateTime(value.confirmed_at)} 确认`;
});

const summaryCaseIncomplete = computed(() => caseCounts.value.incomplete);

const sectionNo = computed(() => {
  const numbers: Record<string, string> = {};
  let index = 0;
  const next = (): string => String(++index).padStart(2, "0");
  if (failed.value.length > 0) {
    numbers.failed = next();
  }
  if (stalled.value.length > 0) {
    numbers.stalled = next();
  }
  numbers.analysis = next();
  if (session.issues.length > 0) {
    numbers.issues = next();
  }
  numbers.report = next();
  numbers.opinion = next();
  numbers.cases = next();
  return numbers;
});

const link = computed(() => session.result?.link ?? null);
const linkLocation = computed(() => {
  const location = link.value?.location;
  if (!location) {
    return { known: false, text: "没有链路数据，位置未知。" };
  }
  return location.known
    ? { known: true, text: `${location.path}:${location.line}` }
    : { known: false, text: location.reason };
});

const evidenceCount = computed(
  () => evidenceItems.value.length || run.value?.evidence_refs.length || 0,
);
const appliedTemplates = computed(() => session.plan?.templates ?? []);
const dispositionChips = computed(() => {
  const totals: Record<TemplateDisposition, number> = {
    applicable: 0,
    not_applicable: 0,
    unsupported: 0,
    unknown: 0,
  };
  for (const template of appliedTemplates.value) {
    for (const key of Object.keys(totals) as TemplateDisposition[]) {
      totals[key] += template.summary?.[key] ?? 0;
    }
  }
  return (Object.keys(totals) as TemplateDisposition[])
    .filter((key) => totals[key] > 0)
    .map((key) => ({ key, label: DISPOSITION_LABEL[key], count: totals[key] }));
});

const evidenceMap = computed(() => {
  const map = new Map<string, EvidenceItem>();
  for (const item of evidenceItems.value) {
    map.set(item.ref, item);
  }
  return map;
});

async function loadEvidence(): Promise<void> {
  if (!session.project || !runId.value) {
    evidenceItems.value = [];
    return;
  }
  try {
    const data = await api.listEvidence(session.project.project_id, runId.value);
    evidenceItems.value = data.evidence ?? [];
  } catch {
    evidenceItems.value = [];
  }
}

function filename(ref: string): string {
  const parts = ref.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || ref;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function guessKind(ref: string): string {
  const lower = ref.toLowerCase();
  if (/\.(png|jpe?g|gif|webp)$/.test(lower)) {
    return "截图";
  }
  if (/\.(json|xml|ya?ml)$/.test(lower)) {
    return "数据";
  }
  if (/\.(log|txt|md)$/.test(lower)) {
    return "记录";
  }
  return "文件";
}

function caseEvidence(runCase: RunCase): EvidenceTile[] {
  const refs = new Set<string>([
    ...(runCase.evidence_refs ?? []),
    ...(runCase.attempts ?? []).flatMap((attempt) => attempt.evidence_refs ?? []),
  ]);
  return [...refs].map((ref) => {
    const item = evidenceMap.value.get(ref);
    return {
      ref,
      name: item?.name || filename(ref),
      bytes: item?.bytes ?? 0,
      exists: Boolean(item?.exists),
      path: item?.path ?? "",
      missing: item?.missing_reason ?? "",
      known: Boolean(item),
    };
  });
}

function runMessages(runCase: RunCase): string {
  return (runCase.attempts ?? [])
    .map((attempt) => attempt.message?.trim())
    .filter((message): message is string => Boolean(message))
    .join("\n\n");
}

async function copyRef(ref: string): Promise<void> {
  try {
    if (!navigator.clipboard?.writeText) {
      throw new Error("no-clipboard");
    }
    await navigator.clipboard.writeText(ref);
    actionNote.value = `已复制引用：${ref}`;
  } catch {
    actionNote.value = "编辑器没有提供剪贴板写入权限。下一步：手动选中引用后复制。";
  }
}

async function openTile(tile: EvidenceTile): Promise<void> {
  actionNote.value = "";
  if (tile.known && !tile.exists) {
    actionNote.value = `${tile.name} 已缺失。下一步：核对运行目录是否被移动或清理。`;
    return;
  }
  if (!tile.path) {
    await copyRef(tile.ref);
    return;
  }
  try {
    await openHostPath(tile.path);
    actionNote.value = `已请求用系统程序打开：${tile.ref}`;
  } catch (error) {
    const friendly = describeError(error);
    actionNote.value = `${friendly.title}。${friendly.next}`;
  }
}

async function onRerun(): Promise<void> {
  rerunError.value = null;
  const error = await rerun();
  if (error) {
    rerunError.value = error;
  }
}

async function onRerunItem(acceptanceItemId: string): Promise<void> {
  rerunError.value = null;
  const error = await rerunItem(acceptanceItemId);
  if (error) {
    rerunError.value = error;
  }
}

function prefillExport(): void {
  if (!exportPath.value) {
    const root = session.project?.root?.replace(/[\\/]+$/, "") ?? "";
    const short = run.value?.run_id.slice(-6) ?? "run";
    exportPath.value = root ? `${root}\\aitest-report-${short}.md` : `aitest-report-${short}.md`;
  }
  showExport.value = true;
}

async function ensureReport(): Promise<string> {
  if (reportId) {
    return reportId;
  }
  if (!session.project || !run.value) {
    throw new Error("缺少运行信息");
  }
  const report = await api.generateReport(session.project.project_id, run.value.run_id);
  reportId = report.report_id;
  return reportId;
}

async function exportReport(): Promise<void> {
  if (!session.project || !exportPath.value.trim()) {
    return;
  }
  exporting.value = true;
  exportError.value = null;
  exported.value = "";
  try {
    const id = await ensureReport();
    const result = await api.exportReport(
      session.project.project_id,
      id,
      exportPath.value.trim(),
    );
    exported.value = result.path;
    showExport.value = false;
  } catch (error) {
    exportError.value = describeError(error);
  } finally {
    exporting.value = false;
  }
}

async function saveOpinion(): Promise<void> {
  if (!session.project || !opinionText.value.trim() || !opinionBy.value.trim()) {
    opinionError.value = {
      title: "署名和意见都要填写",
      detail: "",
      next: "填写你的名字和验收意见后再保存",
    };
    return;
  }
  savingOpinion.value = true;
  opinionError.value = null;
  try {
    const id = await ensureReport();
    const result = await api.saveOpinion({
      projectId: session.project.project_id,
      reportId: id,
      by: opinionBy.value.trim(),
      text: opinionText.value.trim(),
    });
    savedOpinion.value = result.opinion;
    opinionText.value = "";
  } catch (error) {
    opinionError.value = describeError(error);
  } finally {
    savingOpinion.value = false;
  }
}

function scrollToTop(): void {
  const run = (): void => {
    try {
      window.scrollTo({ top: 0, behavior: "auto" });
    } catch {
      // jsdom 不实现滚动；浏览器中才有效。
    }
  };
  run();
  window.setTimeout(run, 0);
}

onMounted(() => {
  void loadEvidence();
  scrollToTop();
});

watch(runId, () => {
  reportId = "";
  exported.value = "";
  showExport.value = false;
  exportError.value = null;
  opinionError.value = null;
  savedOpinion.value = null;
  opinionText.value = "";
  actionNote.value = "";
  rerunError.value = null;
  evidenceItems.value = [];
  void loadEvidence();
  scrollToTop();
});
</script>

<template>
  <section v-if="run" class="result" data-testid="result-panel">
    <ResultSummary
      :verdict="outcome"
      :status-label="RUN_STATUS_LABEL[run.status]"
      :status-tone="RUN_STATUS_BADGE[run.status]"
      :time-label="formatDateTime(run.started_at)"
      :selected="selected"
      :applicable="effectiveApplicable"
      :project-mode="projectMode"
      :project-passed="caseCounts.passed"
      :project-total="totalCases"
      :case-total="totalCases"
      :case-passed="caseCounts.passed"
      :case-failed="caseCounts.failed"
      :case-incomplete="summaryCaseIncomplete"
      :case-manual="caseCounts.manual"
      :pending-items="pendingItems"
      :assumptions="analysis?.unverified?.length ?? 0"
      :ticks="summaryTicks"
      :uncovered-items="uncoveredItems"
      :note="summaryNote"
      :confirmation="confirmationLabel"
    />

    <div class="result-actions">
      <div class="row-wrap">
        <AppButton
          variant="primary"
          :loading="isBusy('rerun')"
          data-testid="rerun-button"
          @click="onRerun"
        >
          <Icon name="refresh" :size="14" />
          再测一次
        </AppButton>
        <AppButton variant="secondary" data-testid="new-target" @click="newTarget">
          新目标
        </AppButton>
      </div>
      <span class="hint">再测一次复用最近计划与范围，旧运行完整保留。</span>
    </div>

    <ErrorNotice
      v-if="rerunError"
      :error="rerunError"
      :show-retry="false"
      @retry="() => {}"
    />


    <section v-if="failed.length" class="block">
      <div class="section-label">
        <span class="sec-no">{{ sectionNo.failed }}</span>
        <span>失败用例 · {{ failed.length }}</span>
        <span class="sec-note">先看证据，再看分析与问题</span>
      </div>

      <div v-if="failed.length" class="fail-list">
        <details
          v-for="(row, failedIndex) in failed"
          :key="row.runCase.case_id"
          class="fail-card"
          :open="failedIndex === 0"
          data-testid="failed-case"
        >
          <summary class="fail-head">
            <span class="fail-index numeric">{{ String(failedIndex + 1).padStart(2, "0") }}</span>
            <span class="fail-title">{{ row.planCase?.title || row.planCase?.expected || "失败用例" }}</span>
            <Badge tone="failed">失败</Badge>
            <span class="fail-chev" aria-hidden="true"><Icon name="chevron-right" :size="13" /></span>
          </summary>
          <div class="fail-body">
          <p v-if="row.itemText" class="fail-item">验收标准：{{ row.itemText }}</p>
          <p v-if="row.planCase?.expected" class="fail-expected">
            预期：{{ row.planCase.expected }}
          </p>
          <p v-if="row.planCase?.layer || methodLabel(row.planCase?.verify_method)" class="fail-meta">
            <template v-if="row.planCase?.layer">{{ LAYER_LABEL[row.planCase.layer] }}</template>
            <template v-if="row.planCase?.layer && methodLabel(row.planCase?.verify_method)"> · </template>
            <template v-if="methodLabel(row.planCase?.verify_method)">{{ methodLabel(row.planCase?.verify_method) }}</template>
          </p>
          <pre v-if="runMessages(row.runCase)" class="output">{{ runMessages(row.runCase) }}</pre>

          <div v-if="caseEvidence(row.runCase).length" class="tiles" aria-label="这条用例的证据">
            <button
              v-for="tile in caseEvidence(row.runCase)"
              :key="tile.ref"
              type="button"
              class="tile"
              :title="tile.ref"
              @click="openTile(tile)"
            >
              <span class="tile-icon" aria-hidden="true"><Icon name="file" :size="14" /></span>
              <span class="tile-copy">
                <span class="tile-name truncate">{{ tile.name }}</span>
                <span class="tile-meta">
                  {{ guessKind(tile.ref) }}
                  <template v-if="tile.known && tile.bytes > 0"> · {{ formatBytes(tile.bytes) }}</template>
                  <template v-else-if="!tile.known"> · 引用</template>
                </span>
              </span>
              <Badge v-if="tile.known && !tile.exists" tone="failed">缺失</Badge>
              <span v-else class="tile-open" aria-hidden="true">
                <Icon name="chevron-right" :size="13" />
              </span>
            </button>
          </div>
          <p v-else class="hint">这条用例没有登记证据。</p>

          <footer v-if="row.planCase?.acceptance_item_id" class="fail-foot">
            <button
              type="button"
              class="text-action"
              @click="onRerunItem(row.planCase?.acceptance_item_id ?? '')"
            >
              只重跑这条
            </button>
          </footer>
          </div>
        </details>
      </div>
    </section>

    <section v-if="stalled.length" class="block">
      <div class="section-label">
        <span class="sec-no">{{ sectionNo.stalled }}</span>
        <span>阻塞与未完成 · {{ stalled.length }}</span>
        <span class="sec-note">这些结果不计入通过</span>
      </div>
      <div class="case-card">
        <template v-for="row in stalled" :key="row.runCase.case_id">
          <ManualRecord
            v-if="row.runCase.result === 'manual_pending'"
            :run-case="row.runCase"
            :plan-case="row.planCase"
            :item-text="row.itemText"
          />
          <CaseRow
            v-else
            :run-case="row.runCase"
            :plan-case="row.planCase"
            :item-text="row.itemText"
            :index="row.index"
          />
        </template>
      </div>
    </section>

    <AnalysisPanel
      :analysis="analysis"
      :loading="isBusy('analysis')"
      :can-generate="canAnalyze"
      :not-needed="!canAnalyze"
      :step="sectionNo.analysis"
      @generate="generateAnalysis"
    />

    <section v-if="session.issues.length" class="block">
      <div class="section-label">
        <span class="sec-no">{{ sectionNo.issues }}</span>
        <span>遗留问题 · {{ session.issues.length }}</span>
        <span class="sec-note">跨运行累积，不改变本次结论</span>
      </div>
      <IssueList
        :issues="session.issues"
        :run-id="run.run_id"
        :can-close="canCloseIssues"
      />
    </section>

    <details v-if="appliedTemplates.length" class="fold" data-testid="template-block">
      <summary>模板检查项（{{ appliedTemplates.length }}）</summary>
      <div class="fold-body">
        <p class="hint">只读提示：不进入必测分母，也不改变验收项。</p>
        <div class="disposition-row">
          <span
            v-for="chip in dispositionChips"
            :key="chip.key"
            class="disposition"
            :class="`disposition-${chip.key}`"
          >
            {{ chip.label }}
            <span class="numeric">{{ chip.count }}</span>
          </span>
          <span class="quiet">{{ appliedTemplates.map((template) => template.name).join("、") }}</span>
        </div>
        <div v-for="template in appliedTemplates" :key="template.template_id" class="template">
          <div class="template-head">
            <h3>{{ template.name }}</h3>
            <span class="quiet">{{ template.version }} · {{ template.applied_at }}</span>
          </div>
          <ul class="template-items">
            <li v-for="item in template.items" :key="item.item_id">
              <span class="disposition" :class="`disposition-${item.disposition}`">
                {{ DISPOSITION_LABEL[item.disposition] }}
              </span>
              <span class="template-objective">{{ item.objective || item.item_id }}</span>
              <span class="template-reason">{{ item.reason || "未提供理由" }}</span>
            </li>
          </ul>
        </div>
      </div>
    </details>

    <details class="fold" data-testid="link-block">
      <summary>链路与位置</summary>
      <div class="fold-body link-card">
        <div class="link-row">
          <span class="link-label">首错节点</span>
          <span v-if="link?.first_anomaly" class="link-value mono">
            {{ link.first_anomaly.node_id }}（{{ link.first_anomaly.status || "unknown" }}）
          </span>
          <span v-else class="link-value">没有发现非 passed 节点</span>
        </div>
        <div class="link-row">
          <span class="link-label">上游</span>
          <span class="link-value">
            {{ link?.upstream.length ? link.upstream.map((node) => node.node_id).join(" → ") : "无" }}
          </span>
        </div>
        <div class="link-row">
          <span class="link-label">下游</span>
          <span class="link-value">
            {{
              link?.downstream.length
                ? link.downstream.map((node) => node.node_id).join("、")
                : "无"
            }}
          </span>
        </div>
        <div class="link-row">
          <span class="link-label">位置</span>
          <span class="link-value mono" :class="{ unknown: !linkLocation.known }">
            {{ linkLocation.known ? linkLocation.text : `未知范围：${linkLocation.text}` }}
          </span>
        </div>
      </div>
    </details>

    <section class="block">
      <div class="section-label">
        <span class="sec-no">{{ sectionNo.report }}</span>
        <span>证据与报告</span>
      </div>
      <div class="action-row">
        <AppButton variant="secondary" @click="openEvidenceDrawer">
          <Icon name="file" :size="14" />
          全部证据（{{ evidenceCount }}）
        </AppButton>
        <AppButton variant="secondary" @click="prefillExport">
          <Icon name="export" :size="14" />
          导出报告
        </AppButton>
        <span v-if="exported" class="hint exported">
          已导出到 <span class="mono">{{ exported }}</span>
        </span>
        <span v-if="actionNote" class="hint" role="status">{{ actionNote }}</span>
      </div>
    </section>

    <div v-if="showExport" class="export-form">
      <div class="section-title">
        <h2>导出 Markdown 报告</h2>
        <span class="section-note">凭据不会写入报告</span>
      </div>
      <Field label="保存位置" for-id="export-path" hint="绝对路径；目录需要已存在。">
        <input
          id="export-path"
          v-model="exportPath"
          class="input mono"
          type="text"
          spellcheck="false"
        />
      </Field>
      <ErrorNotice
        v-if="exportError"
        :error="exportError"
        :show-retry="false"
        @retry="() => {}"
      />
      <div class="row">
        <AppButton variant="primary" :loading="exporting" @click="exportReport">导出</AppButton>
        <AppButton variant="ghost" @click="showExport = false">取消</AppButton>
      </div>
    </div>

    <section class="opinion block">
      <div class="section-label">
        <span class="sec-no">{{ sectionNo.opinion }}</span>
        <span>验收意见</span>
        <span class="sec-note">自由文本，不改变结论</span>
      </div>
      <div v-if="savedOpinion" class="saved">
        <Badge tone="passed">已记录</Badge>
        <span class="hint">
          {{ savedOpinion.by }} · {{ formatDateTime(savedOpinion.at) }}：{{ savedOpinion.text }}
        </span>
      </div>
      <div class="opinion-grid">
        <Field label="署名" for-id="opinion-by" required>
          <input
            id="opinion-by"
            v-model="opinionBy"
            class="input"
            type="text"
            autocomplete="name"
            placeholder="你的名字"
          />
        </Field>
        <Field label="意见" for-id="opinion-text" required>
          <textarea
            id="opinion-text"
            v-model="opinionText"
            class="input textarea"
            rows="3"
            placeholder="例如：A3 已复测通过，可以上线"
          ></textarea>
        </Field>
      </div>
      <ErrorNotice
        v-if="opinionError"
        :error="opinionError"
        :show-retry="false"
        @retry="() => {}"
      />
      <div class="row">
        <AppButton variant="primary" :loading="savingOpinion" @click="saveOpinion">
          保存意见
        </AppButton>
        <span class="hint">不填写时，报告会显示“未填写验收意见”。</span>
      </div>
    </section>

    <details class="all-cases">
      <summary><span class="sec-no">{{ sectionNo.cases }}</span>全部用例（{{ joined.length }}）</summary>
      <div class="case-card">
        <CaseRow
          v-for="row in joined"
          :key="row.runCase.case_id"
          :run-case="row.runCase"
          :plan-case="row.planCase"
          :item-text="row.itemText"
          :index="row.index"
        />
      </div>
    </details>
  </section>
</template>

<style scoped>
.result {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  overflow-anchor: none;
}

.result-actions {
  position: sticky;
  top: var(--header-h);
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding: 8px 0;
  background: var(--bg);
  border-bottom: 1px solid var(--line-strong);
}

.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.block .section-title {
  margin-bottom: 0;
}

.fail-list {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--line);
}

.fail-card {
  border-bottom: 1px solid var(--line);
}

.fail-card:last-child {
  border-bottom: 0;
}

.fail-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) 0;
  cursor: pointer;
  list-style: none;
}

.fail-head::-webkit-details-marker {
  display: none;
}

.fail-title {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--fs-14);
  font-weight: 500;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.fail-chev {
  flex: none;
  color: var(--text-3);
  transition: transform 160ms ease;
}

.fail-card[open] .fail-chev {
  transform: rotate(90deg);
}

.fail-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: 0 0 var(--space-3) 30px;
}

.fail-index {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  flex: none;
  border: 1px solid var(--line-strong);
  border-radius: 50%;
  font-size: var(--fs-12);
  color: var(--failed);
}

.fail-item,
.fail-expected,
.fail-meta {
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.55;
}

.fail-meta {
  color: var(--text-3);
}

.fail-expected {
  color: var(--text-3);
}

.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 0 var(--space-4);
  border-top: 1px solid var(--line);
}

.tile {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  padding: 8px 0;
  background: none;
  border: 0;
  border-bottom: 1px solid var(--line);
  border-radius: 0;
  text-align: left;
  color: var(--text);
  transition:
    border-color 140ms ease,
    background-color 140ms ease;
}

.tile:hover {
  background: var(--surface-raised);
}

.tile-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: 4px;
  background: var(--surface-raised);
  color: var(--text-3);
}

.tile-copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1 1 auto;
}

.tile-open {
  flex: none;
  color: var(--text-3);
  transition: color 140ms ease;
}

.tile:hover .tile-open {
  color: var(--accent);
}

.tile-name {
  font-size: var(--fs-12);
  line-height: 1.4;
}

.tile-meta {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.3;
}

.fail-foot {
  display: flex;
  justify-content: flex-end;
  padding-top: 2px;
}

.text-action {
  padding: 0;
  background: none;
  border: 0;
  color: var(--accent);
  font-size: var(--fs-12);
  text-decoration: underline;
  text-underline-offset: 3px;
}

.case-card {
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: none;
  overflow: hidden;
}

.fold {
  border-top: 1px solid var(--line);
  padding-top: var(--space-2);
}

.fold summary {
  cursor: pointer;
  font-size: var(--fs-12);
  color: var(--text-2);
  padding: 4px 0;
  width: fit-content;
}

.fold-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2) 0 var(--space-1);
}

.disposition-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.disposition {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 6px;
  border-radius: var(--r-chip);
  border: 1px solid var(--line);
  background: var(--surface-sunken);
  font-size: var(--fs-12);
  color: var(--text-2);
}

.disposition-applicable {
  color: var(--passed);
  border-color: var(--passed-border);
}

.disposition-not_applicable {
  color: var(--neutral);
}

.disposition-unsupported,
.disposition-unknown {
  color: var(--incomplete);
  border-color: var(--incomplete-border);
}

.template {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: var(--space-2);
  border-top: 1px dashed var(--line);
}

.template-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.template-head h3 {
  font-size: var(--fs-12);
}

.template-items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.template-items li {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 6px var(--space-2);
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.5;
}

.template-reason {
  grid-column: 2;
  color: var(--text-3);
}

.link-card {
  display: flex;
  flex-direction: column;
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: none;
  overflow: hidden;
}

.link-row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: var(--space-3);
  padding: 7px var(--space-3);
  font-size: var(--fs-12);
  line-height: 1.55;
}

.link-row + .link-row {
  border-top: 1px solid var(--line);
}

.link-label {
  color: var(--text-3);
}

.link-value {
  color: var(--text-2);
  overflow-wrap: anywhere;
}

.link-value.unknown {
  color: var(--incomplete);
}

.action-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.exported {
  word-break: break-all;
}

.export-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}

.opinion {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--line);
}

.opinion .section-title {
  margin-bottom: 0;
}

.input {
  width: 100%;
  padding: 7px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-13);
}

.input::placeholder {
  color: var(--text-disabled);
}

.input:focus {
  border-color: var(--accent-line);
  outline: none;
  box-shadow: 0 0 0 2px var(--accent-glow);
}

.textarea {
  resize: vertical;
  line-height: 1.55;
}

.opinion-grid {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: var(--space-3);
  align-items: start;
}

.saved {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--passed-border);
  background: var(--passed-fill);
  border-radius: var(--r-control);
}

.all-cases summary {
  cursor: pointer;
  font-size: var(--fs-12);
  color: var(--text-2);
  padding: 4px 0;
}

.all-cases .case-card {
  margin-top: var(--space-2);
}

@media (max-width: 640px) {
  .opinion-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 520px) {
  .fail-body {
    padding-left: 0;
  }

  .tiles {
    grid-template-columns: 1fr;
  }

  .result-actions :deep(.btn) {
    flex: 1 1 auto;
  }

  .result-actions .hint {
    display: none;
  }
}
</style>
