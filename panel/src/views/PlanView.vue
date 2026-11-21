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
const planError = computed(
  () => workspace.planFailures[taskId.value] ?? "",
);
const retryingPlan = computed(() => workspace.busyKey(`plan:${taskId.value}`));
const starting = computed(() => workspace.busyKey(`start:${taskId.value}`));
const revising = computed(() => workspace.busyKey(`revise:${taskId.value}`));
const startError = ref("");

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
    return "先勾选页面下方“执行前确认”";
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
const unselected = computed(() =>
  task.value && plan.value ? unselectedItems(task.value, plan.value.scope.acceptanceItemIds) : [],
);
const rule = computed(
  () =>
    project.value?.rules.find((entry) => entry.id === plan.value?.rulesVersionId) ?? null,
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
  <div v-else class="page">
    <PageHeader
      :title="task.title"
      :subtitle="`计划 ${plan.no} · 第 ${plan.revision} 版`"
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

    <section class="surface-strip plan-summary" aria-label="计划摘要">
      <div class="row-between">
        <div>
          <p class="page-subtitle">
            本次 <strong class="num">{{ plan.scope.coverage.selected }}</strong> 项 / 共
            <strong class="num">{{ plan.scope.coverage.applicable }}</strong> 项 ·
            {{ plan.cases.length }} 条检查
          </p>
        </div>
        <span class="badge" :class="stale ? 'is-warn' : 'is-neutral'">{{ statusBadge }}</span>
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
          <dd>计划记录第 {{ plan.taskRevision }} 版</dd>
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

    <section class="section" aria-labelledby="plan-items-title">
      <div class="section-head">
        <h2 id="plan-items-title" class="section-title">计划纳入 {{ plan.scope.acceptanceItemIds.length }} 项</h2>
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

    <div class="actionbar">
      <div class="actionbar-inner">
        <div class="actionbar-facts">
          <span v-if="confirmHint" id="plan-start-hint" class="field-hint">{{ confirmHint }}</span>
          <span v-else class="field-hint">确认后开始；Ctrl / Cmd + Enter 也可。</span>
        </div>
        <div class="actionbar-actions">
          <button
            class="btn btn-ghost"
            type="button"
            :disabled="busy"
            :aria-busy="revising"
            @click="adjustScope"
          >
            {{ revising ? "正在调整…" : "调整范围" }}
          </button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="!canStart"
            :title="confirmHint || undefined"
            :aria-busy="starting"
            :aria-describedby="confirmHint ? 'plan-start-hint' : undefined"
            @click="start"
          >
            {{ starting ? "正在启动…" : "开始测试" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
