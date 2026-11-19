<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import CaseLedger from "@/components/CaseLedger.vue";
import ErrorState from "@/components/ErrorState.vue";
import EvidenceDialog from "@/components/EvidenceDialog.vue";
import ManualResultDialog from "@/components/ManualResultDialog.vue";
import PageHeader from "@/components/PageHeader.vue";
import ReportStrip from "@/components/ReportStrip.vue";
import ResultSummary from "@/components/ResultSummary.vue";
import StatusMark from "@/components/StatusMark.vue";
import StatusText from "@/components/StatusText.vue";
import {
  currentPlan,
  currentRun,
  itemOutcome,
  reportsForRun,
  sortedRunItems,
  unselectedItems,
} from "@/domain/derive";
import type { CaseResult, Run, RunCase, Task } from "@/domain/types";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";
import type { EvidenceInput } from "@/repositories/ProjectRepository";

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
const run = computed<Run | null>(() => (task.value ? currentRun(task.value) : null));
const plan = computed(() => (task.value ? currentPlan(task.value) : null));
const filter = ref<"all" | "problems" | "unfinished">("all");
const caseLedgerRef = ref<InstanceType<typeof CaseLedger> | null>(null);
const manualOpen = ref(false);
const manualCase = ref<RunCase | null>(null);
const evidenceOpen = ref(false);
const evidenceCaseId = ref("");

const items = computed(() => (run.value ? sortedRunItems(run.value) : []));
const outcomes = computed<Record<string, CaseResult>>(() => {
  const map: Record<string, CaseResult> = {};
  if (run.value) {
    run.value.items.forEach((item) => {
      map[item.itemId] = itemOutcome(run.value as Run, item.itemId);
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
    items.value.filter((item) => (outcomes.value[item.itemId] ?? "not_run") !== "passed").length,
);
const reports = computed(() =>
  project.value && run.value ? reportsForRun(project.value, run.value.id) : [],
);
const latestReport = computed(() => reports.value[0] ?? null);
const unselected = computed(() =>
  task.value && run.value ? unselectedItems(task.value, run.value.scope.acceptanceItemIds) : [],
);
const supportsEvidence = computed(() => workspace.capabilities.evidenceFiles);
const manualBusy = computed(() =>
  manualCase.value ? workspace.busyKey(`manual:${manualCase.value.id}`) : false,
);
let disposed = false;

// 宿主 worker 在运行状态结束后才异步构建报告；结果页短暂轮询版本列表。
async function loadReportsUntilReady(): Promise<void> {
  for (let attempt = 0; attempt < 30 && !disposed; attempt += 1) {
    if (!run.value || reports.value.length > 0) {
      return;
    }
    try {
      await workspace.ensureReportVersions(projectId.value, run.value.id);
    } catch {
      // 报告拉取失败不阻塞结果页；下一轮重试。
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

async function saveEvidence(evidence: EvidenceInput[]): Promise<void> {
  if (!run.value) {
    return;
  }
  try {
    for (const input of evidence) {
      await workspace.addEvidence(projectId.value, run.value.id, input);
    }
    await workspace.regenerateReport(projectId.value, run.value.id);
    evidenceOpen.value = false;
    ui.showToast(`已登记 ${evidence.length} 个证据文件；报告已更新。`, "success");
  } catch {
    // store 已提示。
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

async function rerun(): Promise<void> {
  if (!run.value) {
    return;
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
  } catch {
    // store 已提示。
  }
}

async function regenerate(): Promise<void> {
  if (!run.value) {
    return;
  }
  try {
    await workspace.regenerateReport(projectId.value, run.value.id);
    ui.showToast("报告已重新生成。", "success");
  } catch {
    // store 已提示。
  }
}

async function exportReport(reportId: string): Promise<void> {
  try {
    await workspace.exportReport(projectId.value, reportId);
  } catch {
    // store 已提示。
  }
}

function rerunLabel(): string {
  if (run.value?.verdict === "passed") {
    return "再次运行";
  }
  if (run.value?.verdict === "failed") {
    return "重跑未通过项";
  }
  return "重跑未完成项";
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
  <div v-else class="page">
    <PageHeader
      kicker="结果"
      :title="task.title"
      :subtitle="`${plan ? `计划 ${plan.no} · 第 ${plan.revision} 版` : '计划快照缺失'} · ${new Date(run.startedAt).toLocaleString('zh-CN')} · 由 ${run.confirmedBy} 确认`"
    >
      <template #actions>
        <span class="badge" :class="run.simulated ? 'is-neutral' : 'is-accent'">
          {{ run.simulated ? "本地模拟" : "真实运行" }}
        </span>
      </template>
    </PageHeader>

    <ResultSummary :run="run" :task="task" @select="caseLedgerRef?.focusItem($event)" />

    <section class="section">
      <div class="section-head">
        <span class="section-title">项目级检查</span>
        <span class="section-note">单独统计，不进入验收项覆盖</span>
      </div>
      <div class="check-list">
        <div v-for="check in run.l1" :key="check.key" class="check-row">
          <StatusMark :result="check.result" />
          <span class="check-name">{{ check.name }}</span>
          <span class="check-method">{{ check.method }}</span>
          <StatusText :result="check.result" />
        </div>
      </div>
    </section>

    <ReportStrip
      :report="latestReport"
      :version-count="reports.length"
      :run-status="run.status"
      @open="(reportId) => router.push(`/projects/${projectId}/reports/${reportId}`)"
      @regenerate="regenerate"
      @export="exportReport"
    />

    <div class="toolbar">
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
          只看问题<span class="filter-count">{{ problemCount }}</span>
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
      </div>
      <span class="section-note">按最差结果排序 · 编号为验收项序号</span>
      <button v-if="supportsEvidence" class="link-btn" type="button" @click="openEvidence">
        补充证据
      </button>
      <span v-else class="section-note">宿主模式证据由 CLI 追加</span>
    </div>

    <CaseLedger
      ref="caseLedgerRef"
      :run="run"
      :items="items"
      :filter="filter"
      allow-manual
      :manual-busy-case-id="manualCase?.id ?? ''"
      @manual="openManual"
    />

    <details v-if="unselected.length" class="unselected-block">
      <summary>未纳入本次 {{ unselected.length }} 项 <span class="muted">不计入结论</span></summary>
      <ul class="list-plain">
        <li v-for="item in unselected" :key="item.id">{{ item.no }} {{ item.text }}</li>
      </ul>
    </details>

    <div class="actionbar">
      <div class="actionbar-inner">
        <div class="actionbar-facts">
          <span class="fact-strong"
            >计划 {{ plan?.no ?? "—" }} · 第 {{ plan?.revision ?? "—" }} 版</span
          >
          <span>本次 {{ run.counts.selected }} 项 / 共 {{ run.counts.applicable }} 项</span>
          <span>{{ latestReport ? `报告第 ${latestReport.version} 版` : "未生成报告" }}</span>
        </div>
        <div class="actionbar-actions">
          <button class="btn btn-ghost" type="button" @click="adjustScope">调整范围</button>
          <button class="btn" type="button" @click="rerun">{{ rerunLabel() }}</button>
        </div>
      </div>
    </div>

    <ManualResultDialog
      v-model="manualOpen"
      :test-case="manualCase"
      :busy="manualBusy"
      :allow-files="supportsEvidence"
      @save="saveManual"
    />
    <EvidenceDialog
      v-if="supportsEvidence"
      v-model="evidenceOpen"
      :cases="run.cases"
      :default-case-id="evidenceCaseId"
      :busy="workspace.busyKey(`evidence:${run.id}`)"
      @save="saveEvidence"
    />
  </div>
</template>
