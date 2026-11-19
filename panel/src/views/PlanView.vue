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
import { currentPlan as readCurrentPlan, unselectedItems } from "@/domain/derive";
import type { CaseResult, Plan, Task } from "@/domain/types";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const taskId = computed(() => String(route.params.taskId ?? ""));
const project = computed(() => workspace.currentProject);
const task = computed<Task | null>(
  () => project.value?.tasks.find((entry) => entry.id === taskId.value) ?? null,
);
const plan = computed<Plan | null>(() => (task.value ? readCurrentPlan(task.value) : null));
const planLedgerRef = ref<InstanceType<typeof PlanLedger> | null>(null);
const checked = ref(false);
const warningsAcknowledged = ref(false);
const busy = computed(
  () => workspace.busyKey(`start:${taskId.value}`) || workspace.busyKey(`revise:${taskId.value}`),
);
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
const items = computed(() =>
  (plan.value?.scope.acceptanceItemIds ?? []).map((itemId) => {
    const item = task.value?.acceptanceItems.find((entry) => entry.id === itemId);
    return { itemId, no: item?.no ?? "", text: item?.text ?? "验收项已归档" };
  }),
);
const outcomes = computed<Record<string, CaseResult>>(() => {
  const map: Record<string, CaseResult> = {};
  items.value.forEach((item) => {
    map[item.itemId] = "not_run";
  });
  return map;
});
const unselected = computed(() =>
  task.value && plan.value ? unselectedItems(task.value, plan.value.scope.acceptanceItemIds) : [],
);
const rule = computed(
  () => project.value?.rules.find((entry) => entry.id === project.value?.rulesVersionId) ?? null,
);

watch(
  () => plan.value?.id,
  () => {
    checked.value = false;
    warningsAcknowledged.value = false;
  },
);

async function start(): Promise<void> {
  if (!plan.value || !canStart.value) {
    return;
  }
  try {
    await workspace.confirmAndStart(projectId.value, taskId.value, plan.value.id);
    await router.push(`/projects/${projectId.value}/tasks/${taskId.value}/run`);
  } catch {
    checked.value = false;
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
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
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
    <PageHeader kicker="计划" :title="task.title" subtitle="这个任务还没有计划。" />
    <EmptyState title="还没有计划" text="先选本次范围，再生成计划；计划会作为这次运行的合同。">
      <button
        class="btn btn-primary btn-sm"
        type="button"
        @click="router.push(`/projects/${projectId}/tasks/${taskId}/scope`)"
      >
        去选范围
      </button>
    </EmptyState>
  </div>
  <div v-else class="page">
    <PageHeader
      kicker="计划"
      :title="task.title"
      :subtitle="`计划 ${plan.no} · 第 ${plan.revision} 版 · 只读；确认后才会开始运行`"
    >
      <template #actions>
        <button
          class="btn btn-sm"
          type="button"
          @click="router.push(`/projects/${projectId}/tasks/${taskId}/details`)"
        >
          任务资料
        </button>
      </template>
    </PageHeader>

    <div v-if="stale" class="notice is-warn" role="alert">
      <Icon name="warn" :size="14" />
      <span>{{ plan.staleReason || "任务契约已变化，计划过期；重新生成计划后才能启动。" }}</span>
    </div>
    <div v-for="warning in plan.warnings" :key="warning" class="notice">
      <Icon name="info" :size="14" />
      <span>{{ warning }}</span>
    </div>

    <section class="surface-strip plan-summary" aria-label="计划摘要">
      <div class="row-between">
        <div>
          <p class="page-kicker">待执行</p>
          <p class="page-subtitle">
            本次 <strong class="num">{{ plan.scope.coverage.selected }}</strong> 项 / 共
            <strong class="num">{{ plan.scope.coverage.applicable }}</strong> 项 ·
            {{ plan.cases.length }} 条检查
          </p>
        </div>
        <span class="badge is-neutral">待确认</span>
      </div>
      <CoverageRuler
        :items="items"
        :selected-ids="plan.scope.acceptanceItemIds"
        :outcomes="outcomes"
        planned
        @select="planLedgerRef?.focusItem($event)"
      />
      <dl class="info-grid plan-facts">
        <div class="info-cell">
          <dt>规则版本</dt>
          <dd>{{ rule ? `${rule.name} v${rule.version}` : "未绑定" }}</dd>
        </div>
        <div class="info-cell">
          <dt>任务修订</dt>
          <dd>第 {{ task.revision }} 版</dd>
        </div>
        <div class="info-cell">
          <dt>依赖</dt>
          <dd>
            {{ [...new Set(plan.cases.flatMap((c) => c.dependencies))].join("、") || "无额外依赖" }}
          </dd>
        </div>
        <div class="info-cell">
          <dt>预期未核实</dt>
          <dd>{{ plan.cases.filter((c) => !c.expectationVerified).length }} 条</dd>
        </div>
      </dl>
    </section>

    <section class="section">
      <div class="section-head">
        <span class="section-title">计划纳入 {{ plan.scope.acceptanceItemIds.length }} 项</span>
        <span class="section-note">每个验收项至少一条用例；计划只读</span>
      </div>
      <PlanLedger ref="planLedgerRef" :plan="plan" :task="task" />
    </section>

    <details v-if="unselected.length" class="unselected-block">
      <summary>未纳入本次 {{ unselected.length }} 项 <span class="muted">不计入结论</span></summary>
      <ul class="list-plain">
        <li v-for="item in unselected" :key="item.id">{{ item.no }} {{ item.text }}</li>
      </ul>
    </details>

    <div v-if="plan.cases.length === 0" class="notice is-warn" role="alert">
      <Icon name="warn" :size="14" />
      <span>这个计划没有用例，不能启动；回到范围页重新选择。</span>
    </div>

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
      @update:checked="checked = $event"
      @update:warnings-acknowledged="warningsAcknowledged = $event"
      @start="start"
    />

    <div class="actionbar">
      <div class="actionbar-inner">
        <div class="actionbar-facts">
          <span class="fact-strong">计划 {{ plan.no }} · 第 {{ plan.revision }} 版</span>
          <span>{{ plan.cases.length }} 条检查 · {{ plan.scope.coverage.selected }} 项验收</span>
        </div>
        <div class="actionbar-actions">
          <button class="btn btn-ghost" type="button" :disabled="busy" @click="adjustScope">
            调整范围
          </button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="!canStart"
            :aria-busy="busy"
            @click="start"
          >
            {{ busy ? "正在启动…" : "开始测试" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
