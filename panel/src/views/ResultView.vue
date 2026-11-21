<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
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
  displayItemOutcome,
  reportsForRun,
  sortedRunItems,
  unselectedItems,
} from "@/domain/derive";
import type { CaseResult, EvidenceRef, Run, RunCase, Task } from "@/domain/types";
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
  // 确认必须对应当前勾选；改选后重新确认。
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
    // store 已提示安装失败；停留在本页保留选择。
    return;
  }
  // 安装成功后生成新的重跑计划；这一步失败与安装失败分开提示。
  const prepared = await rerun();
  if (!prepared) {
    ui.showToast("依赖已安装，但准备重跑计划失败；可稍后点“按未完成项重新生成计划”。", "error");
  }
}
const filter = ref<"all" | "problems" | "unfinished">(
  route.query.filter === "problems" || route.query.filter === "unfinished"
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
const reports = computed(() =>
  project.value && run.value ? reportsForRun(project.value, run.value.id) : [],
);
const latestReport = computed(() => reports.value[0] ?? null);
const exporting = ref(false);
const reportBusy = computed(
  () => exporting.value || Boolean(run.value && workspace.busyKey(`report:${run.value.id}`)),
);
const unselected = computed(() =>
  task.value && run.value ? unselectedItems(task.value, run.value.scope.acceptanceItemIds) : [],
);
const supportsEvidence = computed(() => workspace.capabilities.evidenceFiles);
const evidenceLimits = computed(() => ({
  singleFileMb: project.value?.config.evidence?.singleFileMb,
  mediaMb: project.value?.config.evidence?.mediaMb,
}));
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
    // 中断/取消/执行出错不会再有报告；后处理已失败也不再空轮询。
    if (
      ["interrupted", "cancelled", "execution_error"].includes(run.value.status) ||
      run.value.postprocess === "error"
    ) {
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

async function focusFromSummary(itemId: string): Promise<void> {
  if (filter.value !== "all") {
    filter.value = "all";
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
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
      `已登记 ${saved} 个；${failed.length} 个失败：${failed.join("、")}。重试相同文件不会重复登记。`,
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
    // store 已提示。
    return false;
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
  if (exporting.value) {
    return;
  }
  exporting.value = true;
  try {
    await workspace.exportReport(projectId.value, reportId);
  } catch {
    // store 已提示。
  } finally {
    exporting.value = false;
  }
}

async function showEvidence(evidence: EvidenceRef): Promise<void> {
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

function rerunLabel(): string {
  if (run.value?.verdict === "passed") {
    return "重新生成计划";
  }
  if (run.value?.verdict === "failed") {
    return "按未通过项重新生成计划";
  }
  if (run.value?.verdict === "incomplete") {
    return "按未完成项重新生成计划";
  }
  return "按原范围重新生成计划";
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
      :title="task.title"
      :subtitle="`${plan ? `计划 ${plan.no} · 第 ${plan.revision} 版` : '计划快照缺失'} · ${new Date(run.startedAt).toLocaleString('zh-CN')} · ${run.confirmedBy} 确认`"
    />

    <p v-if="environmentRows.length" class="run-environment muted">
      环境：{{ environmentRows.join(" · ") }}
    </p>

    <section
      v-if="workspace.mode === 'host' && repairSuggestions.length"
      class="section"
      aria-label="依赖修复建议"
    >
      <div class="section-head">
        <h2 class="section-title">依赖修复建议</h2>
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
          <span class="repair-module">缺失模块：{{ entry.module }}</span>
          <span class="repair-source">
            {{
              entry.source === "declared"
                ? "项目测试依赖"
                : entry.source === "ai"
                  ? "模型建议"
                  : "内置映射"
            }}
          </span>
          <span class="muted">{{ entry.reason }}</span>
        </label>
      </div>
      <div class="empty-actions">
        <label class="repair-confirm">
          <input v-model="repairConfirmed" type="checkbox" />
          我已确认安装以上依赖，并接受更新运行环境与证据记录
        </label>
        <button
          class="btn btn-primary btn-sm"
          type="button"
          :disabled="
            repairSelection.length === 0 || !repairConfirmed || repairing
          "
          :aria-busy="repairing"
          @click="installDependenciesAndRerun"
        >
          {{ repairing ? "正在安装…" : "安装并准备重跑计划" }}
        </button>
      </div>
      <details v-if="repairHistory.length" class="unselected-block">
        <summary>依赖修复记录 {{ repairHistory.length }} 次</summary>
        <ul class="list-plain">
          <li v-for="entry in repairHistory" :key="`${entry.at}-${entry.confirmedBy}`">
            {{ new Date(entry.at).toLocaleString("zh-CN") }} · {{ entry.confirmedBy }} ·
            {{ entry.ok ? "安装成功" : "安装失败" }}
            <span class="muted">
              ·
              {{
                Object.entries(entry.packages)
                  .map(([name, version]) => `${name} ${version || "版本未记录"}`)
                  .join("；") || "没有包版本"
              }}
            </span>
            <span v-if="entry.evidenceRef" class="muted"> · 证据 {{ entry.evidenceRef }}</span>
          </li>
        </ul>
      </details>
    </section>

    <div v-if="unmappedCases.length" class="notice is-warn" role="alert">
      <span>
        {{ unmappedCases.length }} 条用例缺少计划快照，无法挂到验收项；预期按未核实处理：
        {{ unmappedCases.map((testCase) => testCase.id).join("、") }}
      </span>
    </div>

    <ResultSummary
      :run="run"
      :task="task"
      :conclusion="latestReport?.conclusion"
      @select="focusFromSummary"
    />

    <section v-if="run.l1.length" class="section" aria-labelledby="result-l1-title">
      <div class="section-head">
        <h2 id="result-l1-title" class="section-title">项目级检查</h2>
        <span class="section-note">项目级检查不计入 S/M 覆盖</span>
      </div>
      <div class="check-list">
        <div v-for="check in run.l1" :key="check.key" class="check-row">
          <StatusMark :result="check.result" />
          <span class="check-name">{{ check.name }}</span>
          <span class="check-method">{{ check.method }}</span>
          <StatusText :result="check.result" />
          <span v-if="check.reason" class="muted">{{ check.reason }}</span>
        </div>
      </div>
    </section>

    <ReportStrip
      :report="latestReport"
      :version-count="reports.length"
      :run-status="run.status"
      :postprocess="run.postprocess"
      :busy="reportBusy"
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
      </div>
      <span class="section-note">按最差结果排序</span>
      <button v-if="supportsEvidence" class="link-btn" type="button" @click="openEvidence">
        补充证据
      </button>
    </div>

    <div class="actionbar">
      <div class="actionbar-inner">
        <div class="actionbar-facts">
          <span class="fact-strong"
            >计划 {{ plan?.no ?? "—" }} · 第 {{ plan?.revision ?? "—" }} 版</span
          >
          <span>本次 {{ run.counts.selected }} 项 / 共 {{ run.counts.applicable }} 项</span>
        </div>
        <div class="actionbar-actions">
          <button class="btn btn-ghost" type="button" @click="adjustScope">调整范围</button>
          <button class="btn" type="button" @click="rerun">{{ rerunLabel() }}</button>
        </div>
      </div>
    </div>

    <CaseLedger
      ref="caseLedgerRef"
      :run="run"
      :items="items"
      :filter="filter"
      allow-manual
      :manual-busy-case-id="manualCase?.id ?? ''"
      @manual="openManual"
      @show-evidence="showEvidence"
      @reset-filter="filter = 'all'"
    />

    <details v-if="unselected.length" class="unselected-block" open>
      <summary>未纳入本次 {{ unselected.length }} 项 <span class="muted">不计入结论</span></summary>
      <ul class="list-plain">
        <li v-for="item in unselected" :key="item.id">{{ item.no }} {{ item.text }}</li>
      </ul>
    </details>

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
  </div>
</template>
