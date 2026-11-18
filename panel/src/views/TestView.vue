<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import { api } from "@/api/commands";
import { describeError } from "@/api/errors";
import {
  CASE_RESULT_LABEL,
  RUN_STATUS_LABEL,
  VERDICT_LABEL,
  formatDateTime,
  formatDuration,
  sourceLabel,
} from "@/api/labels";
import type { Plan, PlanCase, Task } from "@/api/types";
import AcceptanceLedger from "@/components/AcceptanceLedger.vue";
import ConfirmationPanel from "@/components/ConfirmationPanel.vue";
import CoverageMap, { type CoverageSegment } from "@/components/CoverageMap.vue";
import EvidenceViewer from "@/components/EvidenceViewer.vue";
import ManualForm from "@/components/ManualForm.vue";
import PlanDocument from "@/views/PlanDocument.vue";
import ReportDocument from "@/views/ReportDocument.vue";
import {
  backToCurrent,
  beginNewGoal,
  cancelRun,
  chooseCandidate,
  createTask,
  generatePlan,
  loadCandidates,
  loadRun,
  openAddProject,
  openIssue,
  openPlanSheet,
  openReportSheet,
  refreshRun,
  rerunFailed,
  selectTask,
  setScope,
  startPolling,
  startRun,
  stopPolling,
  workspace,
} from "@/state/workspace";

const evidenceRef = ref("");
const evidenceOpen = ref(false);
const manualCaseId = ref("");
const manualOpen = ref(false);
const actionError = ref("");
const ledger = ref<{ focus: (id: string) => void } | null>(null);
const taskMenuOpen = ref(false);
const scopeEditing = ref(false);

const task = computed<Task | null>(() => workspace.run?.task ?? workspace.task);
const plan = computed<Plan | null>(() => workspace.run?.plan ?? workspace.plan);
const run = computed(() => workspace.run);
const historical = computed(() => workspace.historical);

const mode = computed<"no-project" | "no-task" | "setup" | "stale" | "plan" | "running" | "result">(() => {
  if (!workspace.project) {
    return "no-project";
  }
  if (!workspace.task) {
    return "no-task";
  }
  if (!workspace.plan) {
    return "setup";
  }
  if (workspace.planDetail?.stale) {
    return "stale";
  }
  if (workspace.run?.run.status === "running") {
    return "running";
  }
  if (workspace.run) {
    return "result";
  }
  return "plan";
});

const activeItems = computed(() =>
  (task.value?.acceptance_items ?? []).filter((item) => item.status === "active"),
);

const selectedIds = computed(() =>
  run.value?.plan?.scope.acceptance_item_ids ?? workspace.scope,
);

const applicable = computed(() => plan.value?.applicable_count ?? activeItems.value.length);

const segments = computed<CoverageSegment[]>(() =>
  activeItems.value.map((item) => {
    const outcome = run.value?.item_outcomes?.find((row) => row.item_id === item.item_id);
    const tone = outcome
      ? outcome.result === "passed"
        ? "pass"
        : outcome.result === "failed"
          ? "fail"
          : outcome.result === "blocked" || outcome.result === "error"
            ? "block"
            : selectedIds.value.includes(item.item_id)
              ? "wait"
              : "off"
      : selectedIds.value.includes(item.item_id)
        ? "sel"
        : "off";
    const label = outcome
      ? outcome.result === "passed"
        ? "通过"
        : outcome.result === "failed"
          ? "失败"
          : outcome.result === "blocked"
            ? "阻塞"
            : outcome.result === "error"
              ? "出错"
              : "未运行"
      : selectedIds.value.includes(item.item_id)
        ? "已计划"
        : "未选";
    return { id: item.item_id, text: item.text, tone, label };
  }),
);

const planLabel = computed(() => {
  if (!plan.value) {
    return "还没有计划";
  }
  const number = plan.value.plan_no ? `计划 ${plan.value.plan_no}` : "旧计划";
  return plan.value.revision > 1 ? `${number} · 第 ${plan.value.revision} 版` : number;
});

const l1Cases = computed<PlanCase[]>(() =>
  (plan.value?.cases ?? []).filter((item) => {
    const ids = [
      ...(item.acceptance_item_id ? [item.acceptance_item_id] : []),
      ...(item.acceptance_item_ids ?? []),
    ];
    return item.layer === "L1" && ids.length === 0;
  }),
);

const l1Text = computed(() => {
  if (mode.value === "stale") {
    return "重新生成计划时一起检查";
  }
  if (!run.value) {
    if (l1Cases.value.length) {
      return "将随运行执行，不计入范围";
    }
    if (!workspace.plan) {
      return "生成计划后一起检查";
    }
    return workspace.task?.intent === "smoke"
      ? "生成计划后执行项目级检查"
      : "本次没有项目级检查";
  }
  if (!l1Cases.value.length && !run.value.run.l1) {
    return "本次没有项目级检查";
  }
  if (!l1Cases.value.length) {
    return "已执行";
  }
  const labels = l1Cases.value.map((item) => {
    const row = run.value?.run.cases.find((entry) => entry.case_id === item.case_id);
    return `${item.title || "项目级检查"} ${row ? CASE_RESULT_LABEL[row.result] : "未运行"}`;
  });
  return labels.join(" · ") || "项目级检查已执行";
});

const conclusion = computed(() =>
  run.value?.outcome ? VERDICT_LABEL[run.value.outcome] : "",
);

const unverified = computed(
  () => plan.value?.cases.filter((item) => (item.warnings ?? []).length > 0).length ?? 0,
);

const linkSummary = computed(() => {
  const link = run.value?.link;
  if (!link) {
    return "";
  }
  const anomaly = link.first_anomaly;
  const node = anomaly
    ? [anomaly.type, anomaly.status, anomaly.message].filter(Boolean).join(" · ")
    : "未找到异常节点";
  if (link.location.known) {
    return `${node} · ${link.location.path}:${link.location.line}`;
  }
  return `${node} · 位置未知：${link.location.reason}`;
});

const manualPending = computed(() => run.value?.manual_cases ?? 0);
const counts = computed(() => run.value?.counts ?? {});
const hasUnpassed = computed(() =>
  Boolean(run.value?.item_outcomes?.some((item) => item.result !== "passed")),
);

const completedCases = computed(() => {
  if (!run.value) {
    return 0;
  }
  return run.value.run.cases.filter(
    (item) => item.result !== "not_run" && item.result !== "manual_pending",
  ).length;
});

const totalCases = computed(() => plan.value?.cases.length ?? 0);

const elapsed = computed(() => {
  if (!run.value?.run.started_at) {
    return "";
  }
  const end = run.value.run.heartbeat_at || new Date().toISOString();
  const seconds = Math.max(
    0,
    Math.round((new Date(end).getTime() - new Date(run.value.run.started_at).getTime()) / 1000),
  );
  return formatDuration(seconds);
});

const stale = computed(() => workspace.planDetail?.stale === true);

const stateLabel = computed(() => {
  if (mode.value === "running") {
    return `运行中 · ${elapsed.value}`;
  }
  if (mode.value === "result") {
    return `${conclusion.value || RUN_STATUS_LABEL[run.value?.run.status ?? "completed"]}${
      run.value?.outcome && run.value.run.status !== "completed"
        ? ` · ${RUN_STATUS_LABEL[run.value.run.status]}`
        : ""
    }`;
  }
  if (mode.value === "plan") {
    return "等待确认";
  }
  if (mode.value === "stale") {
    return "计划已过期";
  }
  return "准备计划";
});

const editingScope = computed(
  () =>
    mode.value === "setup" ||
    mode.value === "stale" ||
    ((mode.value === "plan" || mode.value === "result") && scopeEditing.value),
);

function onCoverage(id: string): void {
  ledger.value?.focus(id);
}

function toggle(itemId: string): void {
  if (!editingScope.value) {
    return;
  }
  const next = new Set(workspace.scope);
  if (next.has(itemId)) {
    next.delete(itemId);
  } else {
    next.add(itemId);
  }
  setScope([...next]);
}

function selectAll(): void {
  setScope(activeItems.value.map((item) => item.item_id));
}

function clearAll(): void {
  setScope([]);
}

async function generate(): Promise<void> {
  actionError.value = "";
  scopeEditing.value = false;
  await generatePlan();
}

async function confirmStart(): Promise<void> {
  actionError.value = "";
  const ok = await startRun();
  if (ok) {
    startPolling();
  }
}

async function stop(): Promise<void> {
  await cancelRun();
}

async function rerun(): Promise<void> {
  actionError.value = "";
  const ok = await rerunFailed();
  if (ok) {
    startPolling();
  }
}

async function analyze(): Promise<void> {
  if (!workspace.project || !run.value) {
    return;
  }
  actionError.value = "";
  try {
    await api.runAnalysis(workspace.project.project_id, run.value.run.run_id);
    await loadRun(run.value.run.run_id);
  } catch (error) {
    const friendly = describeError(error);
    actionError.value = `${friendly.title}。${friendly.next}`;
  }
}

async function openIssueDetail(issueId: string): Promise<void> {
  await openIssue(issueId);
  workspace.view = "issues";
}

function openEvidence(ref: string): void {
  evidenceRef.value = ref;
  evidenceOpen.value = true;
}

function recordManual(caseId: string): void {
  manualCaseId.value = caseId;
  manualOpen.value = true;
}

function closeManual(): void {
  manualOpen.value = false;
  manualCaseId.value = "";
}

async function back(): Promise<void> {
  await backToCurrent();
}

async function switchTask(taskId: string): Promise<void> {
  taskMenuOpen.value = false;
  await selectTask(taskId);
}

async function newGoal(): Promise<void> {
  taskMenuOpen.value = false;
  scopeEditing.value = false;
  await beginNewGoal();
}

function beginScopeEdit(): void {
  scopeEditing.value = true;
}

function cancelScopeEdit(): void {
  scopeEditing.value = false;
}

watch(
  () => workspace.stage,
  (value) => {
    if (value === "running") {
      startPolling();
    } else {
      stopPolling();
    }
  },
  { immediate: true },
);

onMounted(() => {
  if (workspace.stage === "running") {
    startPolling();
  }
});

onUnmounted(() => {
  stopPolling();
});

watch(
  () => workspace.run?.run.status,
  (value) => {
    if (value && value !== "running") {
      void refreshRun();
    }
  },
);
</script>

<template>
  <div class="test">
    <div v-if="mode === 'no-project'" class="gate">
      <h1>先登记一个项目</h1>
      <p class="ui-help">
        面板只读取你选择的本机项目。数据工作空间另存，不会写进被测源码。
      </p>
      <button type="button" class="ui-btn primary" @click="openAddProject">添加项目</button>
    </div>

    <div v-else-if="mode === 'no-task'" class="gate">
      <h1>这次要验证什么</h1>
      <p class="ui-help">
        模型会先读项目，给出目标候选；默认选中第一条，但只有你确认后才建任务。
      </p>
      <p v-if="workspace.candidateLoading" class="ui-help">正在读项目…</p>
      <p v-else-if="workspace.candidateError" class="ui-error">
        {{ workspace.candidateError.title }}。{{ workspace.candidateError.next }}
      </p>
      <div v-else-if="workspace.candidates.length" class="candidates">
        <button
          v-for="(item, index) in workspace.candidates"
          :key="`${item.title}-${index}`"
          type="button"
          class="candidate"
          :class="{ on: index === workspace.candidateIndex }"
          @click="chooseCandidate(index)"
        >
          <strong>{{ item.title }}</strong>
          <span v-if="item.reason">{{ item.reason }}</span>
          <span v-if="item.source" class="source">{{ sourceLabel(item.source) }}</span>
        </button>
      </div>
      <label class="field goal-field">
        <span class="ui-label">或者自己写</span>
        <textarea
          v-model="workspace.goal"
          class="ui-textarea"
          placeholder="例如：验证下单改版后订单只出现一次，库存只扣一次"
        />
      </label>
      <div class="gate-actions">
        <button type="button" class="ui-link" @click="loadCandidates">换一批建议</button>
        <button
          type="button"
          class="ui-btn primary"
          :disabled="Boolean(workspace.loading.taskAdd)"
          @click="createTask"
        >
          {{ workspace.loading.taskAdd ? "正在建任务…" : "开始验证这个目标" }}
        </button>
      </div>
    </div>

    <template v-else-if="workspace.task">
      <header class="head">
        <div class="titleline">
          <div class="title-main">
            <h1>{{ task?.title || workspace.task.title }}</h1>
          </div>
          <div class="task-switch">
            <button type="button" class="ui-btn small" @click="taskMenuOpen = !taskMenuOpen">
              切换目标
            </button>
            <div v-if="taskMenuOpen" class="task-menu">
              <button
                v-for="item in workspace.tasks"
                :key="item.task_id"
                type="button"
                class="menu-item"
                :class="{ on: item.task_id === workspace.task.task_id }"
                @click="switchTask(item.task_id)"
              >
                {{ item.title }}
              </button>
              <button type="button" class="menu-item new" @click="newGoal">新建目标</button>
            </div>
          </div>
        </div>
        <div class="runline">
          <div class="meta">
            <span>{{ planLabel }}</span>
            <span v-if="historical">历史运行 · {{ formatDateTime(run?.run.started_at) }}</span>
            <span v-else-if="run">运行于 {{ formatDateTime(run.run.started_at) }}</span>
            <span v-else-if="mode !== 'stale'">未开始</span>
            <span v-if="plan && mode !== 'running'">{{ totalCases }} 条用例</span>
            <button v-if="mode === 'plan'" type="button" class="ui-link" @click="beginScopeEdit">
              调整范围
            </button>
          </div>
          <div class="state">
            <span
              class="stateword"
              :class="mode === 'running' ? 'running' : run?.outcome ?? ''"
            >
              {{ stateLabel }}
            </span>
          </div>
        </div>
        <CoverageMap
          v-if="segments.length"
          :segments="segments"
          :selected="selectedIds.length"
          :applicable="applicable"
          :phase="mode === 'running' ? 'running' : mode === 'plan' || mode === 'stale' ? 'plan' : 'result'"
          :compact="mode === 'stale'"
          @select="onCoverage"
        />
        <p v-else class="ui-help smoke-note">
          项目快速检查：只做项目级检查，不按验收项逐条验。
        </p>
      </header>

      <div class="l1">
        <span>项目级检查</span>
        <span>{{ l1Text }}</span>
      </div>
      <div v-if="run && linkSummary" class="l1">
        <span>链路</span>
        <span>{{ linkSummary }}</span>
      </div>

      <p v-if="actionError" class="ui-error view-error">{{ actionError }}</p>
      <p v-if="run && run.task_snapshot === false" class="view-warning">
        这次运行没有任务快照，验收项文字可能来自当前任务；历史原文无法还原。
      </p>

      <!-- 选择范围：任务还没有计划，或计划过期，或正在调整范围 -->
      <template v-if="editingScope">
        <section class="scope-panel">
          <div class="scope-copy">
            <h2>
              {{
                mode === "stale"
                  ? "旧计划已过期"
                  : mode === "plan"
                    ? "调整本次范围"
                    : mode === "result"
                      ? "调整本次范围"
                      : "准备这次测试"
              }}
            </h2>
            <p>
              {{
                mode === "stale"
                  ? "任务的验收项或验证方式已变化。选好范围后重新生成一份计划。"
                  : mode === "plan"
                    ? "范围变化会生成一份新计划；旧计划和旧运行保留。"
                    : "先选本次要验证的验收项，计划只覆盖选中的项。"
              }}
            </p>
          </div>
          <div class="scope-tools">
            <button type="button" class="ui-link" @click="selectAll">全选</button>
            <button type="button" class="ui-link" @click="clearAll">清空</button>
          </div>
          <div class="scope-list">
            <label v-for="item in activeItems" :key="item.item_id" class="scope-row">
              <input
                type="checkbox"
                :checked="workspace.scope.includes(item.item_id)"
                @change="toggle(item.item_id)"
              />
              <span>{{ item.text }}</span>
            </label>
          </div>
          <p v-if="!activeItems.length && workspace.task.intent === 'smoke'" class="ui-help">
            项目快速检查不需要选验收项，只做构建 / 启动 / 项目已有测试命令。
          </p>
          <div class="scope-action">
            <span class="ui-help">
              {{ workspace.scope.length }} 项 · {{ mode === "plan" ? "保存为新计划" : "生成后仍需确认" }}
            </span>
            <span class="actions">
              <button
                v-if="mode === 'plan' || mode === 'result'"
                type="button"
                class="ui-link"
                @click="cancelScopeEdit"
              >
                取消调整
              </button>
              <button
                type="button"
                class="ui-btn primary"
                :disabled="
                  Boolean(workspace.loading.planGenerate) ||
                  (workspace.scope.length === 0 && workspace.task.intent !== 'smoke')
                "
                @click="generate"
              >
                {{ workspace.loading.planGenerate ? "正在生成计划…" : mode === "stale" ? "重新生成计划" : mode === "plan" || mode === "result" ? "生成新计划" : "生成测试计划" }}
              </button>
            </span>
          </div>
        </section>
      </template>

      <!-- 计划预览：只读台账 + 一个主操作 -->
      <template v-else-if="mode === 'plan'">
        <AcceptanceLedger
          ref="ledger"
          :task="task"
          :plan="plan"
          :run="run"
          :issues="workspace.issues"
          :selected="selectedIds"
          readonly
          mode="plan"
          @open-evidence="openEvidence"
          @open-issue="openIssueDetail"
          @open-plan="openPlanSheet"
        />
        <ConfirmationPanel
          v-if="plan"
          :plan="plan"
          :scope="selectedIds"
          :applicable="applicable"
          :stale="stale"
          :stale-reason="workspace.planDetail?.stale_reason"
          :busy="Boolean(workspace.loading.runStart)"
          @confirm="confirmStart"
          @open-plan="openPlanSheet"
        />
      </template>

      <!-- 运行中 -->
      <template v-else-if="mode === 'running'">
        <AcceptanceLedger
          ref="ledger"
          :task="task"
          :plan="plan"
          :run="run"
          :issues="workspace.issues"
          :selected="selectedIds"
          readonly
          mode="running"
          @open-evidence="openEvidence"
          @open-issue="openIssueDetail"
          @analyze="analyze"
          @open-plan="openPlanSheet"
        />
        <div class="actionbar">
          <div class="bar-meta">
            <span>运行中 · {{ elapsed }}</span>
            <span>已完成 {{ completedCases }} / {{ totalCases }} 条检查</span>
            <span>关闭面板不会中断运行</span>
          </div>
          <button type="button" class="ui-btn danger" @click="stop">停止</button>
        </div>
      </template>

      <!-- 结果 -->
      <template v-else>
        <AcceptanceLedger
          ref="ledger"
          :task="task"
          :plan="plan"
          :run="run"
          :issues="workspace.issues"
          :selected="selectedIds"
          readonly
          mode="result"
          @open-evidence="openEvidence"
          @record-manual="recordManual"
          @open-issue="openIssueDetail"
          @analyze="analyze"
          @open-plan="openPlanSheet"
        />
        <div class="actionbar">
          <div class="bar-meta">
            <span>已完成 {{ completedCases }} / {{ totalCases }} 条检查</span>
            <span v-if="unverified">未核实 {{ unverified }}</span>
            <span v-if="counts.incomplete">未完成 {{ counts.incomplete }}</span>
            <span v-if="manualPending">待人工 {{ manualPending }}</span>
          </div>
          <div class="bar-actions">
            <button type="button" class="ui-link" @click="openPlanSheet">查看计划</button>
            <button v-if="!historical" type="button" class="ui-link" @click="beginScopeEdit">
              调整范围
            </button>
            <button type="button" class="ui-link" @click="openReportSheet">报告</button>
            <button v-if="historical" type="button" class="ui-btn" @click="back">返回当前</button>
            <button
              v-else-if="hasUnpassed"
              type="button"
              class="ui-btn primary"
              :disabled="Boolean(workspace.loading.runStart)"
              @click="rerun"
            >
              重跑未通过项
            </button>
            <button
              v-else
              type="button"
              class="ui-btn primary"
              :disabled="Boolean(workspace.loading.runStart)"
              @click="confirmStart"
            >
              再次运行
            </button>
          </div>
        </div>
      </template>
    </template>

    <PlanDocument v-if="workspace.sheet === 'plan'" />
    <ReportDocument v-if="workspace.sheet === 'report'" />
    <EvidenceViewer v-if="evidenceOpen" :focus-ref="evidenceRef" @close="evidenceOpen = false" />
    <ManualForm
      v-if="manualOpen"
      :case-id="manualCaseId"
      @close="closeManual"
      @saved="closeManual"
    />
  </div>
</template>

<style scoped>
.test {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - var(--header-h));
  padding: 0 var(--space-4) var(--space-4);
}

.gate {
  max-width: 560px;
  margin: var(--space-6) auto 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.gate h1 {
  font-size: var(--fs-20);
}

.candidates {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--line-strong);
}

.candidate {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 8px;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: transparent;
  color: var(--text-2);
  text-align: left;
}

.candidate.on {
  border-left: 2px solid var(--accent);
  background: var(--surface-raised);
}

.candidate strong {
  color: var(--text);
  font-size: var(--fs-12);
}

.candidate span {
  color: var(--text-3);
  font-size: var(--fs-10);
  line-height: 1.45;
}

.candidate .source {
  font-family: var(--font-mono);
}

.goal-field {
  display: block;
}

.gate-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
}

.head {
  padding: 12px 0 0;
}

.titleline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  position: relative;
}

.title-main {
  min-width: 0;
}

.crumb {
  color: var(--text-3);
  font-size: var(--fs-9, 9px);
  letter-spacing: 0.02em;
}

.titleline h1 {
  margin-top: 2px;
  font-size: var(--fs-18);
  line-height: 1.35;
  letter-spacing: -0.01em;
}

.task-switch {
  position: relative;
  flex: none;
}

.task-menu {
  position: absolute;
  right: 0;
  top: 34px;
  z-index: 10;
  width: 260px;
  padding: 4px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  box-shadow: var(--shadow-pop);
}

.menu-item {
  display: block;
  width: 100%;
  padding: 6px 7px;
  border: 0;
  background: transparent;
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.4;
  text-align: left;
}

.menu-item:hover,
.menu-item.on {
  background: var(--surface-raised);
  color: var(--text);
}

.menu-item.new {
  border-top: 1px solid var(--line);
  color: var(--text-3);
}

.runline {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-3);
  margin: 7px 0 9px;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  line-height: 1.45;
}

.state {
  white-space: nowrap;
}

.stateword {
  font-size: var(--fs-12);
  font-weight: 600;
}

.stateword.running {
  color: var(--accent);
}

.stateword.passed {
  color: var(--passed);
}

.stateword.failed {
  color: var(--failed);
}

.stateword.incomplete {
  color: var(--incomplete);
}

.smoke-note {
  padding: 4px 0;
}

.l1 {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
  color: var(--text-3);
  font-size: var(--fs-10);
}

.l1 span:last-child {
  font-family: var(--font-mono);
  color: var(--text-2);
  text-align: right;
}

.view-error,
.view-warning {
  margin-bottom: 6px;
}

.view-warning {
  color: var(--incomplete);
  font-size: var(--fs-11);
  line-height: 1.5;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 7px 0;
}

.toolbar-actions {
  display: flex;
  gap: 10px;
  flex: none;
}

.scope-panel {
  border-top: 1px solid var(--line-strong);
  padding: 12px 0 0;
}

.scope-copy h2 {
  margin: 0 0 3px;
  font-size: var(--fs-14);
}

.scope-copy p {
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.5;
}

.scope-tools {
  display: flex;
  gap: 10px;
  padding: 7px 0 3px;
}

.scope-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
}

.scope-row {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  color: var(--text-2);
  font-size: var(--fs-12);
  line-height: 1.45;
}

.scope-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  border-top: 1px solid var(--line);
  padding: 9px 0 2px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.actionbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  border-top: 1px solid var(--line-strong);
  padding: 9px 0 2px;
  margin-top: auto;
}

.bar-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  line-height: 1.5;
}

.bar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}

@media (max-width: 520px) {
  .test {
    padding: 0 var(--space-3) var(--space-3);
  }

  .titleline {
    gap: var(--space-2);
  }

  .titleline h1 {
    font-size: var(--fs-16);
  }

  .toolbar {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .scope-action,
  .actionbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .bar-actions {
    width: 100%;
    justify-content: flex-end;
    flex-wrap: wrap;
  }
}
</style>
