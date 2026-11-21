<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import FeatureGroup from "@/components/FeatureGroup.vue";
import Icon from "@/components/Icon.vue";
import PageHeader from "@/components/PageHeader.vue";
import ScopeModeTabs from "@/components/ScopeModeTabs.vue";
import { applicableItems, featureGroups, pendingItems } from "@/domain/derive";
import type { Task } from "@/domain/types";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const taskId = computed(() => String(route.params.taskId ?? ""));
const task = computed<Task | null>(
  () => workspace.currentProject?.tasks.find((entry) => entry.id === taskId.value) ?? null,
);
const selected = ref<string[]>([]);
const mode = ref<"recommended" | "custom" | "all">("recommended");
const moduleFilter = ref("all");
const initialised = ref(false);

const items = computed(() => (task.value ? applicableItems(task.value) : []));
const pending = computed(() => (task.value ? pendingItems(task.value) : []));
const modules = computed(() => [...new Set(items.value.map((item) => item.moduleName))]);
const groups = computed(() => {
  if (!task.value) {
    return [];
  }
  return featureGroups(task.value)
    .map((group) => ({
      ...group,
      items:
        moduleFilter.value === "all"
          ? group.items
          : group.items.filter((item) => item.moduleName === moduleFilter.value),
    }))
    .filter((group) => group.items.length > 0);
});
const selectedSet = computed(() => new Set(selected.value));
const saveState = ref<"idle" | "saving" | "saved" | "error">("idle");
const unselected = computed(() => items.value.filter((item) => !selectedSet.value.has(item.id)));
const unselectedLine = computed(() => {
  const first = unselected.value
    .slice(0, 5)
    .map((item) => `${item.no} ${item.text}`)
    .join(" · ");
  return unselected.value.length > 5 ? `${first} · 等 ${unselected.value.length} 项` : first;
});
const moduleLine = computed(() => {
  const counts = new Map<string, number>();
  items.value
    .filter((item) => selectedSet.value.has(item.id))
    .forEach((item) => counts.set(item.moduleName, (counts.get(item.moduleName) ?? 0) + 1));
  return [...counts.entries()].map(([name, count]) => `${name} ${count}`).join(" · ");
});
const generating = computed(() => workspace.busyKey(`plan:${taskId.value}`));
const canGenerate = computed(() => selected.value.length > 0 && !generating.value);
const canDiscard = computed(
  () =>
    Boolean(task.value) &&
    !task.value?.previousStage &&
    (task.value?.plans.length ?? 0) === 0 &&
    (task.value?.runs.length ?? 0) === 0,
);
const modeNote = computed(() => {
  if (mode.value === "all") return "当前理解范围内的全部可测内容";
  if (mode.value === "recommended") {
    return (task.value?.recommendedItemIds.length ?? 0) > 0
      ? "按目标候选预选，可直接修改"
      : "没有目标候选；推荐范围回退为全量";
  }
  return "按业务功能逐项选择";
});

function syncSelection(): void {
  if (!task.value) {
    return;
  }
  // 不适用/待补充的验收项不能出现在选中范围里，否则勾选保存会被仓储拒绝。
  const applicable = new Set(items.value.map((item) => item.id));
  selected.value = task.value.selectedItemIds.filter((id) => applicable.has(id));
  if (!initialised.value) {
    initialised.value = true;
    const recommended = task.value.recommendedItemIds;
    const all = items.value.map((item) => item.id);
    if (all.length > 0 && all.every((id) => selected.value.includes(id))) {
      mode.value = "all";
    } else if (
      recommended.length > 0 &&
      recommended.length === selected.value.length &&
      recommended.every((id) => selected.value.includes(id))
    ) {
      mode.value = "recommended";
    } else {
      mode.value = "custom";
    }
  }
}

watch(
  () => task.value?.id,
  () => {
    initialised.value = false;
    syncSelection();
  },
  { immediate: true },
);

let saveChain: Promise<unknown> = Promise.resolve();
let saveFailed = false;

function persist(ids: string[]): void {
  saveState.value = "saving";
  saveChain = saveChain
    .then(() => workspace.selectScope(projectId.value, taskId.value, ids))
    .then(() => {
      if (!saveFailed) {
        saveState.value = "saved";
      }
    })
    .catch(() => {
      saveFailed = true;
      saveState.value = "error";
      selected.value = task.value ? [...task.value.selectedItemIds] : [];
    });
}

function toggleItem(itemId: string): void {
  const next = selectedSet.value.has(itemId)
    ? selected.value.filter((id) => id !== itemId)
    : [...selected.value, itemId];
  selected.value = next;
  mode.value = "custom";
  persist(next);
}

function toggleFeature(itemIds: string[], selectAll: boolean): void {
  const current = new Set(selected.value);
  itemIds.forEach((id) => {
    if (selectAll) {
      current.add(id);
    } else {
      current.delete(id);
    }
  });
  selected.value = [...current];
  mode.value = "custom";
  persist(selected.value);
}

function changeMode(next: "recommended" | "custom" | "all"): void {
  mode.value = next;
  if (next === "custom") {
    return;
  }
  const ids =
    next === "all"
      ? items.value.map((item) => item.id)
      : task.value?.recommendedItemIds.length
        ? task.value.recommendedItemIds
        : items.value.map((item) => item.id);
  selected.value = [...ids];
  persist(selected.value);
}

function selectAll(): void {
  selected.value = items.value.map((item) => item.id);
  mode.value = "all";
  persist(selected.value);
}

async function clearAll(): Promise<void> {
  const answer = await ui.askConfirm({
    title: "清空本次范围？",
    body: "清空后不会启动；可以全选或重新勾选恢复。",
    confirmLabel: "清空",
    tone: "danger",
  });
  if (answer === null) {
    return;
  }
  selected.value = [];
  mode.value = "custom";
  persist(selected.value);
}

async function generatePlan(): Promise<void> {
  if (!canGenerate.value) {
    return;
  }
  await saveChain;
  if (saveFailed) {
    saveFailed = false;
    ui.showToast("范围保存失败，未生成计划；请重新选择后重试。", "error");
    return;
  }
  try {
    await workspace.generatePlan(projectId.value, taskId.value);
    await router.push(`/projects/${projectId.value}/tasks/${taskId.value}/plan`);
  } catch {
    // store 已提示。
  }
}

async function discardTask(): Promise<void> {
  if (!task.value || !canDiscard.value) {
    return;
  }
  const answer = await ui.askConfirm({
    title: "取消并删除这次任务？",
    body: "还没有生成计划或运行，删除后不会留下记录。",
    confirmLabel: "删除任务",
    tone: "danger",
  });
  if (answer === null) {
    return;
  }
  try {
    await workspace.deleteTask(projectId.value, taskId.value);
    await router.push(`/projects/${projectId.value}/overview`);
  } catch {
    // store 已提示。
  }
}

async function cancelAdjust(): Promise<void> {
  if (!task.value) {
    return;
  }
  try {
    const restored = await workspace.cancelRevision(projectId.value, taskId.value);
    const stage = restored.stage;
    const target =
      stage === "result"
        ? "result"
        : stage === "planned"
          ? "plan"
          : stage === "running"
            ? "run"
            : "scope";
    await router.push(`/projects/${projectId.value}/tasks/${taskId.value}/${target}`);
  } catch {
    // store 已提示。
  }
}

onMounted(async () => {
  if (!task.value) {
    return;
  }
  if (workspace.currentTask?.id !== taskId.value) {
    try {
      await workspace.setActiveTask(projectId.value, taskId.value);
    } catch {
      // store 已提示。
    }
  }
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
  <div v-else class="page">
    <PageHeader
      kicker="本次范围"
      :title="task.title"
      :subtitle="`本次 ${selected.length} 项`"
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

    <div v-if="task.previousStage" class="notice">
      <Icon name="info" :size="14" />
      <span>正在调整已发布计划的范围；生成新计划后生效，旧计划与旧运行保留自己的快照。</span>
    </div>

    <EmptyState
      v-if="items.length === 0"
      title="还没有识别到可测业务功能"
      text="可以改用一句目标测试，或先做快速检查；不会阻塞本地测试。"
    >
      <button
        class="btn btn-sm"
        type="button"
        @click="router.push(`/projects/${projectId}/tasks/new`)"
      >
        改用目标测试
      </button>
      <button
        class="btn btn-sm"
        type="button"
        @click="router.push(`/projects/${projectId}/quick-check`)"
      >
        先做快速检查
      </button>
    </EmptyState>

    <template v-else>
      <ScopeModeTabs :mode="mode" :note="modeNote" @change="changeMode" />

      <div class="scope-summary">
        <span class="scope-count" aria-live="polite">
          本次 <strong class="num">{{ selected.length }}</strong> / 当前理解
          <strong class="num">{{ items.length }}</strong> 项
          <span v-if="unselected.length" class="scope-unselected-count">
            · 未选 {{ unselected.length }} 项
          </span>
          <span v-if="pending.length" class="badge is-warn">待补充 {{ pending.length }}</span>
        </span>
        <div class="scope-controls">
          <label class="scope-filter">
            <span>模块</span>
            <select v-model="moduleFilter" aria-label="按模块筛选验收项">
              <option value="all">全部模块</option>
              <option v-for="module in modules" :key="module" :value="module">{{ module }}</option>
            </select>
          </label>
          <span class="scope-tools">
            <button class="link-btn" type="button" @click="selectAll">全选</button>
            <button class="link-btn" type="button" @click="clearAll">清空</button>
          </span>
        </div>
      </div>

      <p class="scope-modules">
        <span>覆盖模块</span>
        <b>{{ moduleLine || "还没有选择验收项" }}</b>
      </p>

      <div class="scope-groups">
        <FeatureGroup
          v-for="group in groups"
          :key="group.name"
          :feature="group"
          :selected-ids="selected"
          :filtered="moduleFilter !== 'all'"
          @toggle-item="toggleItem"
          @toggle-feature="toggleFeature"
        />
      </div>

      <p class="scope-unselected">
        <template v-if="unselected.length">
          <strong>未选 {{ unselected.length }} 项</strong> · {{ unselectedLine }}
        </template>
        <template v-else>本次已覆盖当前理解的全部可测项。</template>
      </p>

      <div v-if="pending.length" class="scope-pending">
        <h3>待补充 {{ pending.length }} 项（不进入范围）</h3>
        <p v-for="item in pending" :key="item.id">
          {{ item.no }} {{ item.text }} · 还写不出可观察的结果，补齐后再纳入。
        </p>
      </div>

      <div class="actionbar">
        <div class="actionbar-inner">
          <div class="actionbar-facts">
            <span v-if="saveState === 'saving'" class="field-hint">正在保存范围…</span>
            <span v-else-if="saveState === 'saved'" class="field-hint">范围已保存</span>
            <span v-else-if="saveState === 'error'" class="field-error">范围保存失败</span>
            <span>生成后仍可调整；范围变化会生成新计划。</span>
            <span v-if="selected.length === 0" class="field-error"
              >至少选择 1 项才能生成计划。</span
            >
          </div>
          <div class="actionbar-actions">
            <button
              v-if="canDiscard"
              class="btn btn-ghost"
              type="button"
              :disabled="generating"
              @click="discardTask"
            >
              取消任务
            </button>
            <button
              v-if="task.previousStage"
              class="btn btn-ghost"
              type="button"
              :disabled="generating"
              @click="cancelAdjust"
            >
              取消调整
            </button>
            <button
              class="btn btn-primary"
              type="button"
              :disabled="!canGenerate"
              :aria-busy="generating"
              @click="generatePlan"
            >
              {{ generating ? "正在生成计划…" : "生成计划" }}
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
