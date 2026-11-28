<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ConfirmationPanel from "@/components/ConfirmationPanel.vue";
import CoverageRuler from "@/components/CoverageRuler.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import Icon from "@/components/Icon.vue";
import PageHeader from "@/components/PageHeader.vue";
import PlanLedger from "@/components/PlanLedger.vue";
import {
  compareItemNo,
  currentPlan as readCurrentPlan,
  unselectedItems,
} from "@/domain/derive";
import type { CaseResult, Plan, PlanReviewItem, Task } from "@/domain/types";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

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
const plan = computed<Plan | null>(() => (task.value ? readCurrentPlan(task.value) : null));
const planError = computed(() => workspace.planFailures[taskId.value] ?? "");
const retryingPlan = computed(() => workspace.busyKey(`plan:${taskId.value}`));
const starting = computed(() => workspace.busyKey(`start:${taskId.value}`));
const revising = computed(() => workspace.busyKey(`revise:${taskId.value}`));
const draftingDelivery = computed(() => workspace.busyKey(`delivery:${taskId.value}`));
const startError = ref("");

const viewByFlow = ref(true);
const deliveryDrawerOpen = ref(false);
const reviewLoading = ref(false);
const reviewItems = ref<PlanReviewItem[]>([]);
const reviewExpanded = ref(false);
const selectedReviewIds = ref<string[]>([]);
const mergingReviews = ref(false);

const highSeverityReviews = computed(() =>
  reviewItems.value.filter(
    (entry) =>
      entry.severity === "high" ||
      entry.kind === "blind_conflict" ||
      entry.kind === "permission_gap",
  ),
);
const coverageGaps = computed(() =>
  (plan.value?.coverageLedger ?? []).filter(
    (row) => row.status === "gap" || row.status === "unsupported",
  ),
);

async function triggerPlanReview(refresh = false): Promise<void> {
  if (!plan.value || workspace.mode !== "host" || reviewLoading.value) {
    return;
  }
  const targetPlanId = plan.value.id;
  reviewLoading.value = true;
  try {
    const res = await workspace.reviewPlan(projectId.value, targetPlanId, refresh);
    if (plan.value?.id !== targetPlanId) {
      return;
    }
    reviewItems.value = res.reviews;
    selectedReviewIds.value = res.reviews
      .filter((r) => (r.patchCases?.length ?? 0) > 0 && r.severity === "high")
      .map((r) => r.reviewId);
  } catch {
    // 静默复核失败不阻塞主流程阅读与 run.start
  } finally {
    reviewLoading.value = false;
  }
}

function toggleReviewSelection(reviewId: string): void {
  const target = reviewItems.value.find((r) => r.reviewId === reviewId);
  if (!target || target.patchCases.length === 0) {
    return;
  }
  selectedReviewIds.value = selectedReviewIds.value.includes(reviewId)
    ? selectedReviewIds.value.filter((id) => id !== reviewId)
    : [...selectedReviewIds.value, reviewId];
}

async function mergeSelectedReviews(): Promise<void> {
  if (!plan.value || mergingReviews.value) {
    return;
  }
  const chosen = reviewItems.value.filter(
    (r) => selectedReviewIds.value.includes(r.reviewId) && r.patchCases.length > 0,
  );
  if (chosen.length === 0) {
    return;
  }
  mergingReviews.value = true;
  try {
    const nextRev = await workspace.mergePlanReviewCases(projectId.value, plan.value.id, chosen);
    reviewExpanded.value = false;
    reviewItems.value = [];
    selectedReviewIds.value = [];
    checked.value = false;
    ui.showToast(`已将 ${chosen.length} 条评审补丁合并进计划（第 ${nextRev} 版），请重新确认。`, "success");
  } catch {
    // store 已提示。
  } finally {
    mergingReviews.value = false;
  }
}

async function redraftDelivery(): Promise<void> {
  if (!task.value || draftingDelivery.value) {
    return;
  }
  try {
    await workspace.draftDelivery(projectId.value, taskId.value);
    ui.showToast("已重新自动整理任务交付说明与待核对材料。", "success");
  } catch {
    // store 已提示。
  }
}

async function retryPlan(): Promise<void> {
  if (!task.value || retryingPlan.value) {
    return;
  }
  startError.value = "";
  try {
    if (task.value.stage !== "scope") {
      await workspace.reviseScope(projectId.value, taskId.value);
    }
    await workspace.generatePlan(projectId.value, taskId.value);
  } catch {
    // store 已提示；本页显示原因。
  }
}

function adjustToRange(): void {
  void router.push(`/projects/${projectId.value}/tasks/${taskId.value}/scope`);
}

const planLedgerRef = ref<InstanceType<typeof PlanLedger> | null>(null);
const checked = ref(false);
const warningsAcknowledged = ref(false);
const busy = computed(() => starting.value || revising.value);
const stale = computed(() => Boolean(plan.value?.stale));
const hasWarnings = computed(() => (plan.value?.warnings.length ?? 0) > 0);
const startDisabled = computed(
  () => !plan.value || stale.value || plan.value.cases.length === 0 || busy.value,
);
const canStart = computed(
  () =>
    Boolean(plan.value) &&
    !startDisabled.value &&
    checked.value &&
    (!hasWarnings.value || warningsAcknowledged.value),
);
const confirmHint = computed(() => {
  if (!plan.value) {
    return "";
  }
  if (stale.value) {
    return "计划已过期，先重新生成";
  }
  if (plan.value.cases.length === 0) {
    return "计划没有用例，不能启动";
  }
  if (!checked.value) {
    return "请先勾选上方确认框";
  }
  if (hasWarnings.value && !warningsAcknowledged.value) {
    return "还需勾选“已逐条查看计划警告”";
  }
  return "";
});
const statusBadge = computed(() => (stale.value ? "已过期" : "待确认"));
const items = computed(() =>
  (plan.value?.scope.acceptanceItemIds ?? [])
    .map((itemId) => {
      const item = task.value?.acceptanceItems.find((entry) => entry.id === itemId);
      return {
        itemId,
        no: item?.no ?? "",
        text: plan.value?.itemTexts?.[itemId] ?? item?.text ?? "验收项已归档",
      };
    })
    .sort((first, second) => compareItemNo(first.no, second.no)),
);
const outcomes = computed<Record<string, CaseResult>>(() => {
  const map: Record<string, CaseResult> = {};
  items.value.forEach((item) => {
    map[item.itemId] = "not_run";
  });
  return map;
});
const unselected = computed(() => {
  if (!task.value || !plan.value) {
    return [];
  }
  if (plan.value.excludedItems !== undefined) {
    return plan.value.excludedItems.map((entry, index) => ({
      id: entry.id,
      no:
        entry.no ||
        task.value?.acceptanceItems.find((item) => item.id === entry.id)?.no ||
        String(index + 1).padStart(2, "0"),
      text: entry.text,
    }));
  }
  return unselectedItems(task.value, plan.value.scope.acceptanceItemIds);
});

watch(
  () => plan.value?.id,
  () => {
    checked.value = false;
    warningsAcknowledged.value = false;
    reviewItems.value = [];
    reviewExpanded.value = false;
    selectedReviewIds.value = [];
    void triggerPlanReview(false);
  },
  { immediate: true },
);

async function start(): Promise<void> {
  if (!plan.value || !canStart.value) {
    return;
  }
  startError.value = "";
  try {
    await workspace.confirmAndStart(projectId.value, taskId.value, plan.value.id);
    await router.push(`/projects/${projectId.value}/tasks/${taskId.value}/run`);
  } catch (cause) {
    startError.value =
      cause instanceof Error && cause.message
        ? `启动失败：${cause.message}`
        : "启动失败；确认信息仍在，修正后可重试。";
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

function onGlobalKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    if (deliveryDrawerOpen.value) {
      deliveryDrawerOpen.value = false;
      return;
    }
  }
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    const target = event.target;
    if (
      target instanceof HTMLElement &&
      target.closest('input, textarea, select, [contenteditable="true"], dialog[open]')
    ) {
      return;
    }
    event.preventDefault();
    void start();
  }
}

onMounted(async () => {
  window.addEventListener("keydown", onGlobalKeydown);
  if (workspace.environment.length === 0) {
    await workspace.refreshEnvironment();
  }
  if (task.value && workspace.currentTask?.id !== taskId.value) {
    try {
      await workspace.setActiveTask(projectId.value, taskId.value);
    } catch {
      // store 已提示。
    }
  }
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onGlobalKeydown);
});
</script>

<template>
  <div v-if="!task" class="page">
    <ErrorState
      title="任务不存在"
      text="回到项目概览重新选择任务。"
      retry-label="回到概览"
      @retry="router.push(`/projects/${projectId}/overview`)"
    />
  </div>
  <div v-else-if="!plan" class="page">
    <PageHeader
      kicker="计划"
      :title="task.title"
      :subtitle="
        task.origin === 'goal' ? '目标已确认，计划尚未生成。' : '这个任务还没有计划。'
      "
    />
    <ErrorState
      v-if="task.origin === 'goal'"
      title="计划没有生成"
      :text="
        planError ||
        '模型没有返回可发布的计划。可以重试；也可以主动改为范围测试。'
      "
      hide-retry
    >
      <button
        class="btn btn-primary btn-sm"
        type="button"
        :disabled="retryingPlan"
        :aria-busy="retryingPlan"
        @click="retryPlan"
      >
        {{ retryingPlan ? "正在重试…" : "重试生成计划" }}
      </button>
      <button class="btn btn-ghost btn-sm" type="button" @click="adjustToRange">
        调整为范围测试
      </button>
    </ErrorState>
    <EmptyState
      v-else
      title="还没有计划"
      text="先选本次范围，再生成计划；计划会作为这次运行的合同。"
    >
      <button class="btn btn-primary btn-sm" type="button" @click="adjustToRange">
        去选范围
      </button>
    </EmptyState>
  </div>
  <div v-else class="page page-wide">
    <PageHeader
      title="测试计划"
      :subtitle="`${task.title} · 计划 ${plan.no}（第 ${plan.revision} 版）`"
    >
      <template #actions>
        <span class="badge" :class="stale ? 'is-warn' : 'is-neutral'">{{ statusBadge }}</span>
        <button
          class="btn btn-ghost btn-sm"
          type="button"
          @click="deliveryDrawerOpen = true"
        >
          任务说明
        </button>
      </template>
    </PageHeader>

      <div v-if="stale" class="notice is-warn" role="alert">
      <Icon name="warn" :size="14" />
      <span>{{ plan.staleReason || "任务契约已变化，计划过期；重新生成计划后才能启动。" }}</span>
      <button
        class="btn btn-sm"
        type="button"
        :disabled="retryingPlan"
        :aria-busy="retryingPlan"
        @click="retryPlan"
      >
        {{ retryingPlan ? "正在重新生成…" : "重新生成计划" }}
      </button>
    </div>

    <!-- 仅当无锁双盲评审发现高危冲突或权限盲区时冒泡一条提醒；无问题时零打扰 -->
    <section
      v-if="highSeverityReviews.length > 0"
      class="notice is-warn stack-sm"
      style="flex-direction: column; align-items: stretch;"
      role="status"
      aria-label="双盲冲突与权限盲区提醒"
    >
      <div class="row-between" style="flex-wrap: wrap; gap: 8px;">
        <div class="row" style="gap: 8px;">
          <Icon name="warn" :size="14" />
          <strong>
            发现 {{ highSeverityReviews.length }} 处需求/代码口径冲突或缺失权限边界
          </strong>
        </div>
        <div class="row" style="gap: 8px;">
          <button
            class="btn btn-ghost btn-sm"
            type="button"
            @click="reviewExpanded = !reviewExpanded"
          >
            {{ reviewExpanded ? "收起详情" : "查看并勾选采纳" }}
          </button>
          <button
            v-if="selectedReviewIds.length > 0"
            class="btn btn-primary btn-sm"
            type="button"
            :disabled="mergingReviews"
            :aria-busy="mergingReviews"
            @click="mergeSelectedReviews"
          >
            {{ mergingReviews ? "正在合并…" : `一键合并进计划 (${selectedReviewIds.length})` }}
          </button>
        </div>
      </div>
      <div v-if="reviewExpanded" class="stack-sm" style="margin-top: 6px;">
        <label
          v-for="item in highSeverityReviews"
          :key="item.reviewId"
          class="repair-row"
          style="align-items: flex-start;"
        >
          <input
            type="checkbox"
            :checked="selectedReviewIds.includes(item.reviewId)"
            :disabled="item.patchCases.length === 0"
            :title="item.patchCases.length === 0 ? '仅文字提醒，无补丁用例可合并' : '勾选合并进计划'"
            @change="toggleReviewSelection(item.reviewId)"
          />
          <div class="stack-sm" style="gap: 2px;">
            <div>
              <span class="badge is-warn">{{ item.kind === "permission_gap" ? "权限盲区" : "口径冲突" }}</span>
              <strong>{{ item.statement }}</strong>
            </div>
            <span v-if="item.evidence.length" class="field-hint">
              依据：{{ item.evidence.join(" · ") }}
              <template v-if="item.patchCases.length">
                · 附 {{ item.patchCases.length }} 条补丁用例
              </template>
              <template v-else> · 仅提醒，无可直接合并补丁</template>
            </span>
          </div>
        </label>
      </div>
    </section>

    <!-- 开放式覆盖率与任务摘要条 -->
    <section class="plan-summary-strip" aria-label="覆盖率与任务摘要">
      <div class="row-between" style="flex-wrap: wrap; gap: 10px;">
        <p class="page-subtitle">
          本次纳入 <strong class="num">{{ plan.scope.coverage.selected }}</strong> / 共
          <strong class="num">{{ plan.scope.coverage.applicable }}</strong> 项验收 ·
          包含 <strong class="num">{{ plan.cases.length }}</strong> 条检查
        </p>
        <div class="row" style="flex-wrap: wrap; gap: 6px;">
          <span v-if="plan.cases.some((c) => !c.expectationVerified)" class="badge is-warn">
            {{ plan.cases.filter((c) => !c.expectationVerified).length }} 条预期待核实
          </span>
          <span
            v-for="gap in coverageGaps"
            :key="gap.dimension"
            class="badge"
            :class="gap.status === 'unsupported' ? 'is-fail' : 'is-warn'"
            :title="gap.note"
          >
            {{ gap.dimension }}·{{ gap.status === "unsupported" ? "不支持" : "缺口" }}
          </span>
        </div>
      </div>
      <CoverageRuler
        :items="items"
        :selected-ids="plan.scope.acceptanceItemIds"
        :outcomes="outcomes"
        planned
        @select="planLedgerRef?.focusItem($event)"
      />
    </section>

    <div class="workbench-split" style="margin-top: var(--space-4);">
      <!-- 左栏：业务流程分组检查列表 + 独立检查模板区 -->
      <div class="stack">
        <div class="row-between">
          <h2 class="section-title">检查清单 ({{ plan.scope.acceptanceItemIds.length }})</h2>
          <div class="tab-pills" role="group" aria-label="计划视图切换">
            <button
              class="tab-pill"
              :class="{ 'is-active': viewByFlow }"
              type="button"
              @click="viewByFlow = true"
            >
              按业务流程
            </button>
            <button
              class="tab-pill"
              :class="{ 'is-active': !viewByFlow }"
              type="button"
              @click="viewByFlow = false"
            >
              按验收清单
            </button>
          </div>
        </div>

        <PlanLedger
          ref="planLedgerRef"
          :plan="plan"
          :task="task"
          :group-by-flow="viewByFlow"
        />

        <details v-if="unselected.length" class="details-tech">
          <summary>未纳入本次范围 {{ unselected.length }} 项（不计入本次覆盖结论）</summary>
          <ul class="list-plain">
            <li v-for="item in unselected" :key="item.id">{{ item.no }} {{ item.text }}</li>
          </ul>
        </details>

        <div v-if="plan.cases.length === 0" class="notice is-warn" role="alert">
          <Icon name="warn" :size="14" />
          <span>这个计划没有用例，不能启动；请回到范围页重新勾选验收项。</span>
        </div>
      </div>

      <!-- 右栏：固定焦点人工确认与启动操作区 -->
      <aside class="workbench-sticky stack">
        <div class="elevated-panel stack">
          <ConfirmationPanel
            v-if="project"
            :plan="plan"
            :task="task"
            :project="project"
            :environment="workspace.environment"
            :checked="checked"
            :warnings-acknowledged="warningsAcknowledged"
            :busy="busy"
            :start-disabled="startDisabled"
            :start-error="startError"
            @update:checked="checked = $event"
            @update:warnings-acknowledged="warningsAcknowledged = $event"
            @start="start"
          />

          <p v-if="confirmHint" id="plan-start-hint" class="field-hint">{{ confirmHint }}</p>

          <div class="stack-sm">
            <button
              class="btn btn-primary"
              type="button"
              style="width: 100%;"
              :disabled="!canStart"
              :title="confirmHint || undefined"
              :aria-busy="starting"
              :aria-describedby="confirmHint ? 'plan-start-hint' : undefined"
              @click="start"
            >
              <Icon name="play" :size="15" />
              {{ starting ? "正在启动测试…" : "确认并开始测试" }}
            </button>
            <button
              class="btn btn-sm"
              type="button"
              style="width: 100%;"
              :disabled="busy"
              :aria-busy="revising"
              @click="adjustScope"
            >
              {{ revising ? "正在回到范围选择…" : "调整本次纳入范围" }}
            </button>
          </div>
        </div>
      </aside>
    </div>

    <!-- 任务交付说明抽屉 -->
    <div
      v-if="deliveryDrawerOpen"
      class="drawer-backdrop"
      role="presentation"
      @click.self="deliveryDrawerOpen = false"
    >
      <aside class="drawer-panel" role="dialog" aria-modal="true" aria-label="任务说明">
        <div class="drawer-head">
          <div>
            <h2 class="drawer-title">任务说明</h2>
            <p class="drawer-sub">第 {{ plan.taskRevision }} 版</p>
          </div>
          <button
            class="btn btn-ghost btn-sm"
            type="button"
            aria-label="关闭说明抽屉"
            @click="deliveryDrawerOpen = false"
          >
            <Icon name="close" :size="15" />
          </button>
        </div>
        <div class="drawer-body">
          <div class="stack-sm">
            <strong>业务目标</strong>
            <p class="doc-p">{{ task.goal || "未填写" }}</p>
          </div>
          <div class="stack-sm">
            <strong>当前纳入说明</strong>
            <p class="doc-p" style="white-space: pre-wrap;">{{ task.scope || "已按系统分析自动汇总当前适用范围" }}</p>
          </div>
          <div class="stack-sm">
            <strong>环境与前置约束</strong>
            <p class="doc-p">{{ task.preconditions.join("；") || "无需额外前置步骤" }}</p>
          </div>
        </div>
        <div class="drawer-foot">
          <button
            class="btn btn-ghost btn-sm"
            type="button"
            @click="router.push(`/projects/${projectId}/tasks/${taskId}/details`)"
          >
            编辑完整任务资料
          </button>
          <button
            class="btn btn-primary btn-sm"
            type="button"
            :disabled="draftingDelivery"
            :aria-busy="draftingDelivery"
            @click="redraftDelivery"
          >
            {{ draftingDelivery ? "正在整理…" : "重新自动整理说明" }}
          </button>
        </div>
      </aside>
    </div>
  </div>
</template>
