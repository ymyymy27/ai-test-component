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
import { TEMPLATE_DISPOSITION_LABEL } from "@/domain/labels";
import type { CaseResult, Plan, Task, TemplateResult } from "@/domain/types";
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
const templateDrawerOpen = ref(false);
const availableTemplates = ref<Array<{ id: string; name: string; version: string; itemCount: number }>>([]);
const loadingTemplates = ref(false);
const applyingTemplateId = ref("");

// 合并任务级与计划快照内的模板参考检查（保持独立展示，绝不计入 S/M 主覆盖分母）
const appliedTemplates = computed<TemplateResult[]>(() => {
  const merged = new Map<string, TemplateResult>();
  (plan.value?.templates ?? []).forEach((item) => merged.set(item.templateId, item));
  (task.value?.templates ?? []).forEach((item) => merged.set(item.templateId, item));
  return Array.from(merged.values());
});

async function openTemplateDrawer(): Promise<void> {
  templateDrawerOpen.value = true;
  if (availableTemplates.value.length > 0 || loadingTemplates.value) {
    return;
  }
  loadingTemplates.value = true;
  try {
    availableTemplates.value = await workspace.listTemplates();
  } finally {
    loadingTemplates.value = false;
  }
}

async function applyTemplate(templateId: string): Promise<void> {
  if (!task.value || applyingTemplateId.value) {
    return;
  }
  applyingTemplateId.value = templateId;
  try {
    await workspace.applyTemplate(projectId.value, taskId.value, templateId);
    ui.showToast("已套用参考核查模板（结果作为参考展示，不计入 S/M 分母）。", "success");
    templateDrawerOpen.value = false;
  } catch {
    // store 已提示。
  } finally {
    applyingTemplateId.value = "";
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
  if (event.key === "Escape") {
    if (templateDrawerOpen.value) {
      templateDrawerOpen.value = false;
      return;
    }
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
        <button
          class="btn btn-sm"
          type="button"
          @click="openTemplateDrawer"
        >
          检查模板
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

    <!-- 开放式覆盖率与任务摘要条 -->
    <section class="plan-summary-strip" aria-label="覆盖率与任务摘要">
      <div class="row-between" style="flex-wrap: wrap; gap: 10px;">
        <p class="page-subtitle">
          本次纳入 <strong class="num">{{ plan.scope.coverage.selected }}</strong> / 共
          <strong class="num">{{ plan.scope.coverage.applicable }}</strong> 项验收 ·
          包含 <strong class="num">{{ plan.cases.length }}</strong> 条检查 ·
          规则：{{ rule ? `${rule.name} v${rule.version}` : "项目默认" }}
        </p>
        <span v-if="plan.cases.some((c) => !c.expectationVerified)" class="badge is-warn">
          {{ plan.cases.filter((c) => !c.expectationVerified).length }} 条预期待核实
        </span>
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

        <!-- 独立的检查模板区：绝不计入 S/M 主覆盖率分母 -->
        <section
          v-if="appliedTemplates.length"
          class="elevated-panel stack-sm"
          aria-label="检查模板"
        >
          <div class="row-between">
            <h3 class="section-title">检查模板（不计入 S/M）</h3>
            <span class="badge is-info">仅供参考</span>
          </div>
          <p class="field-hint">
            模板检查结果独立记录，不计入 S/M 通过率分母。
          </p>
          <div v-for="tpl in appliedTemplates" :key="tpl.templateId" class="stack-sm">
            <div class="row-between">
              <strong>{{ tpl.name }} v{{ tpl.version }}</strong>
              <span class="muted">
                适用 {{ tpl.summary.applicable }} · 不适用 {{ tpl.summary.not_applicable }}
              </span>
            </div>
            <ul class="list-plain">
              <li v-for="item in tpl.items" :key="item.id">
                <span class="badge is-neutral">{{ item.layer }}</span>
                <strong>{{ item.objective }}</strong>
                <span class="muted"> · {{ TEMPLATE_DISPOSITION_LABEL[item.disposition] }} · {{ item.reason }}</span>
              </li>
            </ul>
          </div>
        </section>

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
              class="btn btn-ghost btn-sm"
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

    <!-- 参考核查模板抽屉 -->
    <div
      v-if="templateDrawerOpen"
      class="drawer-backdrop"
      role="presentation"
      @click.self="templateDrawerOpen = false"
    >
      <aside class="drawer-panel" role="dialog" aria-modal="true" aria-label="检查模板">
        <div class="drawer-head">
          <div>
            <h2 class="drawer-title">套用检查模板</h2>
            <p class="drawer-sub">不计入 S/M 分母</p>
          </div>
          <button
            class="btn btn-ghost btn-sm"
            type="button"
            aria-label="关闭模板抽屉"
            @click="templateDrawerOpen = false"
          >
            <Icon name="close" :size="15" />
          </button>
        </div>
        <div class="drawer-body">
          <p v-if="loadingTemplates" class="field-hint">正在获取可用模板…</p>
          <p v-else-if="availableTemplates.length === 0" class="side-empty">
            暂无可用的参考核查模板。
          </p>
          <div v-else class="stack-sm">
            <div v-for="tpl in availableTemplates" :key="tpl.id" class="elevated-panel row-between">
              <div>
                <strong>{{ tpl.name }}</strong>
                <p class="field-hint">版本 {{ tpl.version }} · 包含 {{ tpl.itemCount }} 项检查</p>
              </div>
              <button
                class="btn btn-sm btn-primary"
                type="button"
                :disabled="applyingTemplateId === tpl.id"
                :aria-busy="applyingTemplateId === tpl.id"
                @click="applyTemplate(tpl.id)"
              >
                {{ applyingTemplateId === tpl.id ? "套用中…" : "套用到当前计划" }}
              </button>
            </div>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>
