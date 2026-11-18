<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { api } from "@/api/commands";
import { describeError } from "@/api/errors";
import {
  CASE_RESULT_LABEL,
  ISSUE_STATUS_LABEL,
  VERDICT_LABEL,
  formatDateTime,
} from "@/api/labels";
import type { CaseResult, Verdict } from "@/api/types";
import {
  closeSheet,
  generateReport,
  loadReport,
  loadRun,
  workspace,
} from "@/state/workspace";

const by = ref("");
const text = ref("");
const carryOpinion = ref(true);
const busy = ref(false);
const note = ref("");
const error = ref("");
const exportPath = ref("");
const exportNote = ref("");
const exportError = ref("");
const versionMenu = ref(false);
const linkBusy = ref(false);
const linkNote = ref("");
const linkError = ref("");

const report = computed(() => workspace.report);
const l1Cases = computed(
  () => (report.value?.l1?.cases as Record<string, unknown>[] | undefined) ?? [],
);
const l1Reasons = computed(
  () => (report.value?.l1?.blocking_reasons as string[] | undefined) ?? [],
);
const run = computed(() => workspace.run);
const coverage = computed(() => report.value?.coverage ?? {});
const selected = computed(() => Number(coverage.value.selected ?? 0));
const applicable = computed(() => Number(coverage.value.applicable ?? 0));

const MOCK_KIND_LABEL: Record<string, string> = {
  real: "真实",
  mock: "模拟数据",
  mixed: "混合",
  unknown: "无法判断",
};

async function importLink(): Promise<void> {
  if (!workspace.project || !run.value) {
    return;
  }
  linkBusy.value = true;
  linkNote.value = "";
  linkError.value = "";
  try {
    await api.importLink(workspace.project.project_id, run.value.run.run_id);
    linkNote.value = "调用链已导入；重新运行后才会影响结论。";
    await loadRun(run.value.run.run_id);
  } catch (error) {
    const friendly = describeError(error);
    linkError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    linkBusy.value = false;
  }
}

function resultLabel(value: unknown): string {
  if (typeof value !== "string") {
    return "未记录";
  }
  if (value === "not_selected") {
    return "未选";
  }
  return CASE_RESULT_LABEL[value as CaseResult] ?? value;
}

function verdictLabel(value: Verdict | undefined): string {
  return value ? VERDICT_LABEL[value] : "还没有结论";
}

function rowText(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  return value == null ? "" : String(value);
}

function rowList(row: Record<string, unknown>, key: string): string[] {
  const value = row[key];
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

async function regenerate(): Promise<void> {
  busy.value = true;
  note.value = "";
  error.value = "";
  const previous = report.value?.opinion ?? null;
  try {
    await generateReport();
    const latest = workspace.report;
    if (latest && previous && carryOpinion.value && workspace.project) {
      await api.saveOpinion({
        projectId: workspace.project.project_id,
        reportId: latest.report_id,
        by: previous.by,
        text: previous.text,
      });
      await loadReport(latest.report_id);
      note.value = "已生成新报告，并沿用旧报告的验收意见。";
    } else if (latest && previous) {
      note.value = "已生成新报告；旧报告的验收意见保留在旧版本里。";
    }
  } catch (failure) {
    const friendly = describeError(failure);
    error.value = `${friendly.title}。${friendly.next}`;
  } finally {
    busy.value = false;
  }
}

async function saveOpinion(): Promise<void> {
  if (!workspace.project || !report.value) {
    return;
  }
  busy.value = true;
  note.value = "";
  error.value = "";
  try {
    await api.saveOpinion({
      projectId: workspace.project.project_id,
      reportId: report.value.report_id,
      by: by.value.trim() || "验收人",
      text: text.value.trim(),
    });
    await loadReport(report.value.report_id);
    note.value = "验收意见已保存。";
  } catch (failure) {
    const friendly = describeError(failure);
    error.value = `${friendly.title}。${friendly.next}`;
  } finally {
    busy.value = false;
  }
}

async function exportReport(): Promise<void> {
  if (!workspace.project || !report.value) {
    return;
  }
  if (!exportPath.value.trim()) {
    exportError.value = "先填写报告要保存到哪里。";
    return;
  }
  exportError.value = "";
  exportNote.value = "";
  busy.value = true;
  try {
    const result = await api.exportReport(
      workspace.project.project_id,
      report.value.report_id,
      exportPath.value.trim(),
    );
    exportNote.value = `已导出：${result.path}（${result.bytes} 字节）`;
  } catch (failure) {
    const friendly = describeError(failure);
    exportError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    busy.value = false;
  }
}

function pickVersion(reportId: string): void {
  versionMenu.value = false;
  if (reportId !== report.value?.report_id) {
    void loadReport(reportId);
  }
}

function startOpinion(): void {
  const opinion = report.value?.opinion;
  by.value = opinion?.by ?? "";
  text.value = opinion?.text ?? "";
}

startOpinion();

watch(
  () => report.value?.report_id,
  () => {
    startOpinion();
    note.value = "";
    error.value = "";
  },
);
</script>

<template>
  <div class="ui-sheet" role="dialog" aria-modal="true" aria-labelledby="report-title">
    <header class="ui-sheet-head">
      <div class="head-left">
        <button type="button" class="ui-link" @click="closeSheet">返回测试</button>
        <strong id="report-title">报告</strong>
        <span class="ui-help">
          {{ report ? `生成于 ${formatDateTime(report.generated_at)}` : "还没有报告" }}
        </span>
      </div>
      <div class="head-actions">
        <div v-if="workspace.reports.length > 1" class="version">
          <button type="button" class="ui-btn small" @click="versionMenu = !versionMenu">
            {{ report ? formatDateTime(report.generated_at) : "选择版本" }} ▾
          </button>
          <div v-if="versionMenu" class="version-menu">
            <button
              v-for="item in workspace.reports"
              :key="item.report_id"
              type="button"
              class="version-row"
              :class="{ on: item.report_id === report?.report_id }"
              @click="pickVersion(item.report_id)"
            >
              <span>{{ formatDateTime(item.generated_at) }}</span>
              <span :class="`result ${item.outcome}`">{{ verdictLabel(item.outcome) }}</span>
            </button>
          </div>
        </div>
        <button type="button" class="ui-btn small" :disabled="busy" @click="regenerate">
          {{ report ? "重新生成" : "生成报告" }}
        </button>
      </div>
    </header>

    <div class="ui-sheet-body report">
      <p v-if="note" class="ui-help note">{{ note }}</p>
      <p v-if="error" class="ui-error">{{ error }}</p>
      <p v-if="workspace.run && workspace.run.task_snapshot === false" class="warning">
        这次运行没有任务快照，报告中的验收项文字可能来自当前任务；历史原文无法还原。
      </p>

      <template v-if="report">
        <section class="summary">
          <div>
            <span class="ui-label">结论</span>
            <strong :class="`verdict ${report.outcome}`">{{ verdictLabel(report.outcome) }}</strong>
          </div>
          <div class="summary-meta">
            <span>本次 {{ selected }} / 共 {{ applicable }} 项</span>
            <span v-if="report.uncovered_items.length">未覆盖 {{ report.uncovered_items.length }}</span>
          </div>
          <p v-if="report.conclusion" class="conclusion">{{ report.conclusion }}</p>
        </section>

        <section class="section">
          <div class="section-head">
            <h2>验收项</h2>
            <span class="ui-help">按最差结果</span>
          </div>
          <div class="table">
            <div class="table-row table-head">
              <span>验收项</span><span>结果</span><span>用例</span>
            </div>
            <div
              v-for="row in report.item_details"
              :key="rowText(row, 'item_id')"
              class="table-row"
            >
              <span>{{ rowText(row, "text") || "未命名验收项" }}</span>
              <span :class="`result ${rowText(row, 'result')}`">
                {{ rowText(row, "result") === "not_selected" ? "未选" : resultLabel(rowText(row, "result")) }}
              </span>
              <span class="ui-mono">{{ rowList(row, "case_ids").length }}</span>
            </div>
          </div>
        </section>

        <section v-if="l1Cases.length || l1Reasons.length" class="section">
          <div class="section-head">
            <h2>项目级检查</h2>
            <span class="ui-help">不计入范围</span>
          </div>
          <div
            v-for="row in l1Cases"
            :key="rowText(row, 'case_id')"
            class="line"
          >
            <span>{{ rowText(row, "title") || "项目级检查" }}</span>
            <span :class="`result ${rowText(row, 'result')}`">{{ resultLabel(rowText(row, "result")) }}</span>
          </div>
          <p v-for="reason in l1Reasons" :key="reason" class="ui-help">
            {{ reason }}
          </p>
        </section>

        <section class="section">
          <div class="section-head">
            <h2>用例明细</h2>
            <span class="ui-help">{{ report.case_details.length }} 条</span>
          </div>
          <div v-for="row in report.case_details" :key="rowText(row, 'case_id')" class="case-row">
            <div class="line">
              <span>{{ rowText(row, "title") || "检查项" }}</span>
              <span :class="`result ${rowText(row, 'result')}`">{{ resultLabel(rowText(row, "result")) }}</span>
            </div>
            <p class="ui-help">预期：{{ rowText(row, "expected") }}</p>
            <p v-if="rowList(row, 'evidence_refs').length" class="ui-help">
              证据：{{ rowList(row, "evidence_refs").join("、") }}
            </p>
            <p v-if="rowList(row, 'warnings').length" class="ui-help warning">
              {{ rowList(row, "warnings").join("；") }}
            </p>
          </div>
        </section>

        <section v-if="report.issues.length" class="section">
          <div class="section-head"><h2>问题</h2></div>
          <div v-for="row in report.issues" :key="rowText(row, 'issue_id')" class="line">
            <span>{{ rowText(row, "title") }}</span>
            <span class="ui-help">
              {{ ISSUE_STATUS_LABEL[rowText(row, "status") as keyof typeof ISSUE_STATUS_LABEL] ?? rowText(row, "status") }}
            </span>
          </div>
        </section>

        <section v-if="report.analysis.length" class="section">
          <div class="section-head"><h2>分析</h2></div>
          <div v-for="(row, index) in report.analysis" :key="index" class="analysis">
            <p v-if="rowText(row, 'status') === 'invalid'" class="warning">
              分析不合格，整条已隐藏。
            </p>
            <p v-else>{{ rowText(row, "observed") || "未记录观察" }}</p>
            <p v-if="rowList(row, 'unverified').length" class="ui-help">
              尚未验证：{{ rowList(row, "unverified").join("；") }}
            </p>
          </div>
        </section>

        <section class="section">
          <div class="section-head">
            <h2>调用链与模拟数据</h2>
            <span class="ui-help">{{ report.real_chain ? "全真实" : "未证明全真实" }}</span>
          </div>
          <p v-if="report.mock_judgement.length === 0" class="ui-help">没有依赖判定记录。</p>
          <div v-for="(row, index) in report.mock_judgement" :key="index" class="line">
            <span>{{ rowText(row, "dependency") || rowText(row, "name") || "依赖" }}</span>
            <span class="ui-help">{{ MOCK_KIND_LABEL[rowText(row, "kind")] ?? "无法判断" }}</span>
          </div>
          <div v-if="run?.link" class="link-facts">
            <p class="ui-help">
              首个异常：{{ run.link.first_anomaly?.type || "未识别" }}
              · {{ run.link.first_anomaly?.status || "无状态" }}
              · {{ run.link.first_anomaly?.message || "没有消息" }}
            </p>
            <p v-if="run.link.location.known" class="ui-help ui-mono">
              {{ run.link.location.path }}:{{ run.link.location.line }}
            </p>
            <p v-else class="ui-help">位置未知：{{ run.link.location.reason }}</p>
          </div>
          <template v-else-if="run">
            <button type="button" class="ui-btn" :disabled="linkBusy" @click="importLink">
              {{ linkBusy ? "导入中…" : "导入调用链日志" }}
            </button>
            <p class="ui-help">读取项目配置里的日志来源；导入后重新运行才会影响结论。</p>
          </template>
          <p v-if="linkError" class="ui-error">{{ linkError }}</p>
          <p v-if="linkNote" class="ui-help">{{ linkNote }}</p>
        </section>

        <section v-if="report.evidence_index.length" class="section">
          <div class="section-head"><h2>证据索引</h2></div>
          <div v-for="row in report.evidence_index" :key="rowText(row, 'ref')" class="line">
            <span class="ui-mono">{{ rowText(row, "ref") }}</span>
            <span class="ui-help">{{ rowText(row, "exists") === "true" ? "存在" : "缺失" }}</span>
          </div>
        </section>

        <section class="section">
          <div class="section-head">
            <h2>导出</h2>
            <span class="ui-help">Markdown</span>
          </div>
          <label class="field">
            <span class="ui-label">保存到</span>
            <input v-model="exportPath" class="ui-input" placeholder="例如 E:/reports/run-report.md" />
          </label>
          <button type="button" class="ui-btn" :disabled="busy" @click="exportReport">
            导出报告
          </button>
          <p v-if="exportError" class="ui-error">{{ exportError }}</p>
          <p v-if="exportNote" class="ui-help">{{ exportNote }}</p>
        </section>

        <section class="section">
          <div class="section-head">
            <h2>验收意见</h2>
            <span class="ui-help">由人填写</span>
          </div>
          <p v-if="report.opinion" class="opinion">
            {{ report.opinion.text || "（空意见）" }}
            <span class="ui-help">· {{ report.opinion.by }} · {{ formatDateTime(report.opinion.at) }}</span>
          </p>
          <label class="field">
            <span class="ui-label">填写人</span>
            <input v-model="by" class="ui-input" placeholder="你的名字" />
          </label>
          <label class="field">
            <span class="ui-label">意见</span>
            <textarea v-model="text" class="ui-textarea" placeholder="写验收结论、遗留风险或上线条件" />
          </label>
          <label class="check">
            <input v-model="carryOpinion" type="checkbox" />
            重新生成报告时沿用这条意见
          </label>
          <button type="button" class="ui-btn" :disabled="busy" @click="saveOpinion">
            保存验收意见
          </button>
        </section>
      </template>

      <div v-else class="empty">
        <p>这次运行还没有报告。</p>
        <button type="button" class="ui-btn primary" :disabled="busy || !run" @click="regenerate">
          生成报告
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.head-left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.version {
  position: relative;
}

.version-menu {
  position: absolute;
  right: 0;
  top: 35px;
  z-index: 5;
  width: 260px;
  padding: 4px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  box-shadow: var(--shadow-pop);
}

.version-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 6px 7px;
  border: 0;
  background: transparent;
  color: var(--text-2);
  font-size: var(--fs-11);
  text-align: left;
}

.version-row:hover,
.version-row.on {
  background: var(--surface-raised);
  color: var(--text);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.version {
  width: auto;
  max-width: 180px;
}

.report {
  padding-top: var(--space-3);
  padding-bottom: var(--space-6);
}

.summary {
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line-strong);
}

.verdict {
  display: block;
  margin-top: 2px;
  font-size: var(--fs-18);
}

.verdict.passed {
  color: var(--passed);
}

.verdict.failed {
  color: var(--failed);
}

.verdict.incomplete {
  color: var(--incomplete);
}

.summary-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 4px;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
}

.conclusion {
  margin-top: 5px;
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.5;
}

.section {
  padding: 11px 0;
  border-bottom: 1px solid var(--line);
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-3);
  margin-bottom: 5px;
}

.section-head h2 {
  margin: 0;
  font-size: var(--fs-12);
}

.table {
  border-top: 1px solid var(--line);
}

.table-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 62px 34px;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--line);
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.45;
}

.table-head {
  color: var(--text-3);
  font-size: var(--fs-10);
}

.table-row span:last-child {
  text-align: right;
}

.line {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 5px 0;
  border-bottom: 1px solid var(--line);
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.45;
}

.result {
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  white-space: nowrap;
}

.result.passed {
  color: var(--passed);
}

.result.failed {
  color: var(--failed);
}

.result.blocked,
.result.error {
  color: var(--incomplete);
}

.case-row {
  padding: 5px 0;
  border-bottom: 1px solid var(--line);
}

.warning {
  color: var(--incomplete);
}

.analysis {
  padding: 5px 0;
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.5;
}

.opinion {
  margin-bottom: 6px;
  color: var(--text-2);
  font-size: var(--fs-12);
  line-height: 1.5;
}

.field {
  display: block;
  margin-bottom: 7px;
}

.check {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 8px;
  color: var(--text-3);
  font-size: var(--fs-10);
}

.empty {
  padding: var(--space-5) 0;
  color: var(--text-2);
}

@media (max-width: 520px) {
  .head-left {
    gap: var(--space-2);
  }

  .version {
  position: relative;
}

.version-menu {
  position: absolute;
  right: 0;
  top: 35px;
  z-index: 5;
  width: 260px;
  padding: 4px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  box-shadow: var(--shadow-pop);
}

.version-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 6px 7px;
  border: 0;
  background: transparent;
  color: var(--text-2);
  font-size: var(--fs-11);
  text-align: left;
}

.version-row:hover,
.version-row.on {
  background: var(--surface-raised);
  color: var(--text);
}

.head-actions {
    gap: 4px;
  }

  .version {
    max-width: 108px;
  }
}
</style>
