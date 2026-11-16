<script setup lang="ts">
import { computed, ref } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import {
  RUN_STATUS_BADGE,
  RUN_STATUS_LABEL,
  formatDateTime,
} from "@/api/labels";
import type { Opinion, PlanCase, RunCase } from "@/api/types";
import AnalysisPanel from "@/components/AnalysisPanel.vue";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import CaseRow from "@/components/CaseRow.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Field from "@/components/Field.vue";
import Icon from "@/components/Icon.vue";
import IssueRow from "@/components/IssueRow.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import VerdictBlock from "@/components/VerdictBlock.vue";
import {
  generateAnalysis,
  go,
  isBusy,
  openHistory,
  openResult,
  session,
} from "@/state/session";

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

const rerunning = ref("");
const rerunError = ref<FriendlyError | null>(null);

let reportId = "";

const run = computed(() => session.result?.run ?? null);
const outcome = computed(() => session.result?.outcome ?? null);
const selected = computed(() => run.value?.scope.acceptance_item_ids.length ?? 0);
const applicable = computed(() => session.plan?.applicable_count ?? 0);
const uncoveredItems = computed(() => session.result?.uncovered_items ?? []);
const loading = computed(() => isBusy("result"));

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

const failedItems = computed(() => {
  const seen = new Set<string>();
  const rows: { itemId: string; text: string }[] = [];
  for (const row of failed.value) {
    const itemId = row.planCase?.acceptance_item_id;
    if (!itemId || seen.has(itemId)) {
      continue;
    }
    seen.add(itemId);
    rows.push({ itemId, text: itemTexts.value.get(itemId) ?? "这个验收项" });
  }
  return rows;
});

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

async function rerunItem(itemId: string): Promise<void> {
  if (!session.project || !run.value) {
    return;
  }
  rerunning.value = itemId;
  rerunError.value = null;
  try {
    const started = await api.startRun(session.project.project_id, run.value.plan_id, [itemId]);
    session.activeRunId = started.run_id;
    session.activePlanId = run.value.plan_id;
    session.result = null;
    go("run");
  } catch (error) {
    rerunError.value = describeError(error);
  } finally {
    rerunning.value = "";
  }
}

function refreshResult(): void {
  if (session.activeRunId) {
    void openResult(session.activeRunId, { keepView: true });
  }
}
</script>

<template>
  <div class="stack" data-testid="result-view">
    <div class="head">
      <div class="head-left">
        <button type="button" class="back" @click="openHistory">
          <Icon name="arrow-left" :size="15" />
          历史记录
        </button>
        <button type="button" class="back" @click="go('project')">项目</button>
      </div>
      <AppButton size="sm" variant="ghost" :loading="loading" @click="refreshResult">
        <Icon name="refresh" :size="14" />
        刷新
      </AppButton>
    </div>

    <SkeletonBlock v-if="loading && !run" :lines="5" label="正在读取运行结果" />

    <template v-else-if="run">
      <VerdictBlock
        :verdict="outcome"
        :selected="selected"
        :applicable="applicable"
        :uncovered-items="uncoveredItems"
        :status-label="RUN_STATUS_LABEL[run.status]"
        :status-tone="RUN_STATUS_BADGE[run.status]"
        :time-label="formatDateTime(run.started_at)"
      />

      <section v-if="failed.length" class="block">
        <div class="section-title">
          <h2>失败用例（{{ failed.length }}）</h2>
          <span class="section-note">先看证据，再看分析</span>
        </div>
        <div class="case-card">
          <CaseRow
            v-for="row in failed"
            :key="row.runCase.case_id"
            :run-case="row.runCase"
            :plan-case="row.planCase"
            :item-text="row.itemText"
            :index="row.index"
          />
        </div>
      </section>

      <section v-if="stalled.length" class="block">
        <div class="section-title">
          <h2>阻塞与未完成（{{ stalled.length }}）</h2>
          <span class="section-note">这些结果不计入通过</span>
        </div>
        <div class="case-card">
          <CaseRow
            v-for="row in stalled"
            :key="row.runCase.case_id"
            :run-case="row.runCase"
            :plan-case="row.planCase"
            :item-text="row.itemText"
            :index="row.index"
          />
        </div>
      </section>

      <section class="block" data-testid="link-block">
        <div class="section-title">
          <h2>链路</h2>
          <span class="section-note">首错节点、上下游与代码位置</span>
        </div>
        <div class="link-card">
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
      </section>

      <AnalysisPanel
        :analysis="analysis"
        :loading="isBusy('analysis')"
        :can-generate="canAnalyze"
        @generate="generateAnalysis"
      />

      <section class="block">
        <div class="section-title">
          <h2>本项目问题</h2>
          <span class="section-note">按失败签名去重，跨运行累积</span>
        </div>
        <ul v-if="session.issues.length" class="issue-card">
          <IssueRow v-for="issue in session.issues" :key="issue.issue_id" :issue="issue" />
        </ul>
        <p v-else class="empty-line">还没有登记问题。运行失败时会按失败签名自动生成。</p>
      </section>

      <section class="actions">
        <div v-if="failedItems.length" class="rerun">
          <span class="actions-label">按验收项重跑</span>
          <div class="row-wrap">
            <AppButton
              v-for="item in failedItems"
              :key="item.itemId"
              size="sm"
              variant="secondary"
              :loading="rerunning === item.itemId"
              @click="rerunItem(item.itemId)"
            >
              <Icon name="refresh" :size="13" />
              {{ item.text }}
            </AppButton>
          </div>
          <p class="hint">重跑会新建一次运行，范围只含该验收项，旧运行完整保留。</p>
          <ErrorNotice
            v-if="rerunError"
            :error="rerunError"
            :show-retry="false"
            @retry="() => {}"
          />
        </div>

        <div class="action-row">
          <AppButton variant="secondary" @click="go('evidence')">
            <Icon name="file" :size="14" />
            证据（{{ run.evidence_refs.length }}）
          </AppButton>
          <AppButton variant="secondary" @click="go('issues')">
            <Icon name="alert" :size="14" />
            问题处置（{{ session.issues.length }}）
          </AppButton>
          <AppButton variant="secondary" @click="prefillExport">
            <Icon name="export" :size="14" />
            导出报告
          </AppButton>
          <span v-if="exported" class="hint exported">
            已导出到 <span class="mono">{{ exported }}</span>
          </span>
        </div>

        <div v-if="showExport" class="card form">
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
            <AppButton variant="primary" :loading="exporting" @click="exportReport">
              导出
            </AppButton>
            <AppButton variant="ghost" @click="showExport = false">取消</AppButton>
          </div>
        </div>
      </section>

      <section class="card form">
        <div class="section-title">
          <h2>验收意见</h2>
          <span class="section-note">自由文本，不改变结论</span>
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
          <AppButton
            variant="primary"
            :loading="savingOpinion"
            @click="saveOpinion"
          >
            保存意见
          </AppButton>
          <span class="hint">不填写时，报告会显示“未填写验收意见”。</span>
        </div>
      </section>

      <details class="all-cases">
        <summary>全部用例（{{ joined.length }}）</summary>
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
    </template>

    <ErrorNotice
      v-else
      :error="{
        title: '还没有可显示的结果',
        detail: '',
        next: '回到历史记录打开一次运行，或从项目页新建测试',
      }"
      :show-retry="false"
      @retry="() => {}"
    />
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.head-left {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  background: none;
  border: 0;
  color: var(--text-2);
  font-size: var(--fs-12);
  border-radius: var(--r-control);
}

.back:hover {
  color: var(--text);
  background: var(--surface-raised);
}

.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.block .section-title {
  margin-bottom: 0;
}

.case-card {
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.link-card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.link-row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: var(--space-3);
  padding: 9px var(--space-4);
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

.issue-card {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.empty-line {
  padding: var(--space-4);
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text-3);
  font-size: var(--fs-12);
}

.actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.rerun {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.actions-label {
  font-size: var(--fs-12);
  color: var(--text-3);
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

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
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
  box-shadow: 0 0 0 2px rgba(130, 175, 205, 0.16);
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
  border: 1px solid rgba(134, 179, 148, 0.25);
  background: var(--passed-fill);
  border-radius: var(--r-control);
}

.all-cases summary {
  cursor: pointer;
  font-size: var(--fs-12);
  color: var(--text-2);
  padding: 6px 0;
}

.all-cases .case-card {
  margin-top: var(--space-2);
}

@media (max-width: 640px) {
  .opinion-grid {
    grid-template-columns: 1fr;
  }
}
</style>
