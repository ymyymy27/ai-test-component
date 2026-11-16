<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import {
  IMPORTANCE_LABEL,
  LAYER_LABEL,
  dependencyLabel,
  methodLabel,
  sourceLabel,
} from "@/api/labels";
import type {
  GeneratedPlan,
  Layer,
  Plan,
  PlanCase,
  TargetCandidate,
} from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Field from "@/components/Field.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import StepRail from "@/components/StepRail.vue";
import { go, session } from "@/state/session";

interface DraftItem {
  id: string;
  text: string;
  savedText: string;
  observable: boolean;
  enabled: boolean;
  source: string;
  status: "active" | "pending";
}

const steps = [
  { title: "测什么", hint: "选一个目标，或自己写" },
  { title: "验收标准", hint: "可观察、可判定" },
  { title: "范围", hint: "按验收项选择" },
  { title: "确认", hint: "看计划再开始" },
];

const draft = reactive({
  step: 1,
  goal: "",
  candidateIndex: -1,
  candidates: [] as TargetCandidate[],
  fallback: "",
  suggestLoading: false,
  suggestError: null as FriendlyError | null,
  taskId: "",
  taskGoal: "",
  items: [] as DraftItem[],
  itemsLoading: false,
  itemsError: null as FriendlyError | null,
  itemsDirty: false,
  preset: "all_applicable" as "all_applicable" | "selected",
  selected: [] as string[],
  moduleIds: [] as string[],
  plan: null as GeneratedPlan | null,
  planDetail: null as Plan | null,
  planLoading: false,
  planError: null as FriendlyError | null,
  planSignature: "",
  starting: false,
  startError: null as FriendlyError | null,
});

const newItem = ref("");
const suggestStarted = ref(false);
const localSeq = ref(0);

const projectId = computed(() => session.project?.project_id ?? "");

const applicableItems = computed(() =>
  draft.items.filter((item) => item.observable && item.status === "active"),
);
const selectedCount = computed(() =>
  draft.preset === "all_applicable"
    ? applicableItems.value.length
    : applicableItems.value.filter((item) => draft.selected.includes(item.id)).length,
);
const applicableCount = computed(() => applicableItems.value.length);
const canNextFromCriteria = computed(
  () => draft.items.some((item) => item.observable && item.text.trim().length > 0),
);
const planSignature = computed(() =>
  [
    draft.preset,
    draft.moduleIds.slice().sort().join(","),
    draft.preset === "selected"
      ? draft.selected.slice().sort().join(",")
      : "all",
  ].join("|"),
);

const casesByItem = computed(() => {
  const map = new Map<string, PlanCase[]>();
  for (const planCase of draft.planDetail?.cases ?? []) {
    const key = planCase.acceptance_item_id ?? "";
    const list = map.get(key) ?? [];
    list.push(planCase);
    map.set(key, list);
  }
  return map;
});

const layerCounts = computed(() => {
  const counts: Record<Layer, number> = { L1: 0, L2: 0, L3: 0 };
  for (const planCase of draft.planDetail?.cases ?? []) {
    counts[planCase.layer] += 1;
  }
  return counts;
});

const selectedItems = computed(() =>
  applicableItems.value.filter(
    (item) => draft.preset === "all_applicable" || draft.selected.includes(item.id),
  ),
);

const projectCases = computed(() =>
  (draft.planDetail?.cases ?? []).filter((planCase) => !planCase.acceptance_item_id),
);

const groupedCases = computed(() =>
  selectedItems.value.map((item) => ({
    item,
    cases: casesByItem.value.get(item.id) ?? [],
  })),
);

const planSummary = computed(() => {
  const coverage = draft.plan?.coverage;
  return {
    selected: coverage?.selected ?? selectedCount.value,
    applicable: coverage?.applicable ?? applicableCount.value,
    cases: draft.planDetail?.cases.length ?? draft.plan?.cases ?? 0,
  };
});

async function loadSuggestions(): Promise<void> {
  if (suggestStarted.value || !projectId.value) {
    return;
  }
  suggestStarted.value = true;
  draft.suggestLoading = true;
  draft.suggestError = null;
  try {
    const result = await api.suggestTargets(projectId.value);
    draft.candidates = result.candidates ?? [];
    draft.fallback = result.fallback ?? "";
  } catch (error) {
    draft.suggestError = describeError(error);
  } finally {
    draft.suggestLoading = false;
  }
}

function chooseCandidate(index: number): void {
  const candidate = draft.candidates[index];
  if (!candidate) {
    return;
  }
  draft.candidateIndex = index;
  draft.goal = candidate.title;
}

function chooseFallback(): void {
  draft.candidateIndex = -2;
  if (draft.fallback) {
    draft.goal = draft.fallback;
  }
}

function onGoalInput(): void {
  if (draft.candidateIndex >= 0 && draft.candidates[draft.candidateIndex]?.title !== draft.goal) {
    draft.candidateIndex = -1;
  }
}

function goStep(step: number): void {
  if (step < 1 || step > 4 || step > draft.step) {
    return;
  }
  draft.step = step;
  draft.startError = null;
  if (step === 4) {
    void ensurePlan();
  }
}

async function next(): Promise<void> {
  if (draft.step === 1) {
    if (!draft.goal.trim() || !projectId.value) {
      return;
    }
    draft.step = 2;
    await ensureTask();
    return;
  }
  if (draft.step === 2) {
    if (!canNextFromCriteria.value) {
      return;
    }
    const saved = await saveItems();
    if (!saved) {
      return;
    }
    draft.step = 3;
    return;
  }
  if (draft.step === 3) {
    if (selectedCount.value === 0) {
      return;
    }
    draft.step = 4;
    await ensurePlan();
  }
}

async function ensureTask(): Promise<void> {
  if (draft.itemsLoading || (draft.taskId && draft.items.length > 0)) {
    return;
  }
  draft.itemsLoading = true;
  draft.itemsError = null;
  try {
    const created = await api.addTask({
      projectId: projectId.value,
      title: draft.goal.trim(),
      goal: draft.goal.trim(),
      requirement: draft.goal.trim(),
    });
    draft.taskId = created.task_id;
    draft.taskGoal = draft.goal.trim();
    await loadTask(created.task_id);
  } catch (error) {
    draft.itemsError = describeError(error);
  } finally {
    draft.itemsLoading = false;
  }
}

async function loadTask(taskId: string): Promise<void> {
  const data = await api.showTask(projectId.value, taskId);
  const enabled = new Set<string>();
  draft.items = data.task.acceptance_items.map((item) => {
    const row: DraftItem = {
      id: item.item_id,
      text: item.text,
      savedText: item.text,
      observable: item.observable,
      enabled: item.observable && item.status === "active",
      source: item.source ?? "",
      status: item.status,
    };
    if (row.enabled) {
      enabled.add(row.id);
    }
    return row;
  });
  draft.selected = [...enabled];
  draft.preset =
    draft.items.length > 0 && enabled.size === draft.items.filter((item) => item.observable).length
      ? "all_applicable"
      : "selected";
  draft.itemsDirty = false;
}

function toggleEnabled(item: DraftItem): void {
  if (!item.observable) {
    return;
  }
  item.enabled = !item.enabled;
  if (item.enabled) {
    if (!draft.selected.includes(item.id)) {
      draft.selected.push(item.id);
    }
  } else {
    draft.selected = draft.selected.filter((id) => id !== item.id);
  }
  draft.preset = "selected";
}

function toggleSelected(item: DraftItem): void {
  if (!item.observable) {
    return;
  }
  if (draft.selected.includes(item.id)) {
    draft.selected = draft.selected.filter((id) => id !== item.id);
  } else {
    draft.selected.push(item.id);
    item.enabled = true;
  }
}

function setPreset(preset: "all_applicable" | "selected"): void {
  draft.preset = preset;
  if (preset === "all_applicable") {
    draft.selected = applicableItems.value.map((item) => item.id);
  }
}

function markEdited(): void {
  draft.itemsDirty = true;
}

function toggleObservable(item: DraftItem): void {
  item.observable = !item.observable;
  if (!item.observable) {
    item.enabled = false;
    draft.selected = draft.selected.filter((id) => id !== item.id);
    draft.preset = "selected";
  } else {
    item.enabled = true;
    if (!draft.selected.includes(item.id)) {
      draft.selected.push(item.id);
    }
  }
  draft.itemsDirty = true;
}

function retrySuggest(): void {
  suggestStarted.value = false;
  void loadSuggestions();
}

function addItem(): void {
  const text = newItem.value.trim();
  if (!text) {
    return;
  }
  localSeq.value += 1;
  const item: DraftItem = {
    id: `local-${localSeq.value}`,
    text,
    savedText: "",
    observable: true,
    enabled: true,
    source: "手动补充",
    status: "active",
  };
  draft.items.push(item);
  draft.selected.push(item.id);
  draft.preset = "selected";
  draft.itemsDirty = true;
  newItem.value = "";
}

function removeItem(item: DraftItem): void {
  draft.items = draft.items.filter((row) => row.id !== item.id);
  draft.selected = draft.selected.filter((id) => id !== item.id);
  draft.itemsDirty = true;
}

const goalChanged = computed(
  () => Boolean(draft.taskGoal) && draft.taskGoal !== draft.goal.trim(),
);

async function reextract(): Promise<void> {
  draft.taskId = "";
  draft.taskGoal = "";
  draft.items = [];
  draft.itemsError = null;
  draft.selected = [];
  draft.preset = "all_applicable";
  draft.itemsDirty = false;
  await ensureTask();
}

async function saveItems(): Promise<boolean> {
  if (!draft.itemsDirty) {
    return true;
  }
  const filled = draft.items.filter((item) => item.text.trim().length > 0);
  if (filled.length === 0) {
    draft.itemsError = {
      title: "至少保留一条验收标准",
      detail: "",
      next: "补充一条可观察的标准，或在第一步改一下目标",
    };
    return false;
  }
  draft.itemsLoading = true;
  draft.itemsError = null;
  try {
    const created = await api.addTask({
      projectId: projectId.value,
      title: draft.goal.trim(),
      goal: draft.goal.trim(),
      acceptanceItems: filled.map((item) => ({
        text: item.text.trim(),
        observable: item.observable,
        source: item.source || "手动补充",
      })),
    });
    draft.taskId = created.task_id;
    const enabledFlags = filled.map((item) => item.enabled);
    await loadTask(created.task_id);
    draft.items.forEach((item, index) => {
      item.enabled = enabledFlags[index] ?? item.enabled;
    });
    draft.selected = draft.items.filter((item) => item.enabled).map((item) => item.id);
    if (draft.selected.length !== applicableCount.value) {
      draft.preset = "selected";
    }
    draft.itemsDirty = false;
    return true;
  } catch (error) {
    draft.itemsError = describeError(error);
    return false;
  } finally {
    draft.itemsLoading = false;
  }
}

function toggleModule(moduleId: string): void {
  if (draft.moduleIds.includes(moduleId)) {
    draft.moduleIds = draft.moduleIds.filter((id) => id !== moduleId);
  } else {
    draft.moduleIds = [...draft.moduleIds, moduleId];
  }
}

async function ensurePlan(force = false): Promise<void> {
  if (!draft.taskId) {
    return;
  }
  if (!force && draft.planDetail && draft.planSignature === planSignature.value) {
    return;
  }
  draft.planLoading = true;
  draft.planError = null;
  draft.startError = null;
  try {
    const generated = await api.generatePlan({
      projectId: projectId.value,
      taskId: draft.taskId,
      preset: draft.preset,
      moduleIds: draft.moduleIds,
      acceptanceItemIds:
        draft.preset === "selected"
          ? draft.selected
          : applicableItems.value.map((item) => item.id),
    });
    draft.plan = generated;
    const detail = await api.showPlan(projectId.value, generated.plan_id);
    draft.planDetail = detail.plan;
    draft.planSignature = planSignature.value;
  } catch (error) {
    draft.plan = null;
    draft.planDetail = null;
    draft.planError = describeError(error);
  } finally {
    draft.planLoading = false;
  }
}

function backToScope(): void {
  draft.step = 3;
  draft.startError = null;
}

async function startRun(): Promise<void> {
  if (!draft.plan || draft.starting) {
    return;
  }
  draft.starting = true;
  draft.startError = null;
  try {
    const started = await api.startRun(projectId.value, draft.plan.plan_id);
    session.activeRunId = started.run_id;
    session.activePlanId = draft.plan.plan_id;
    session.activeTaskId = draft.taskId;
    session.plan = draft.planDetail;
    session.result = null;
    go("run");
  } catch (error) {
    draft.startError = describeError(error);
  } finally {
    draft.starting = false;
  }
}

function exitWizard(): void {
  go("project");
}

onMounted(() => {
  void loadSuggestions();
});
</script>

<template>
  <div class="wizard" data-testid="wizard">
    <div class="wizard-head">
      <button type="button" class="back" @click="exitWizard">
        <Icon name="arrow-left" :size="15" />
        返回项目
      </button>
      <span class="context">{{ session.project?.name }}</span>
    </div>

    <StepRail :steps="steps" :current="draft.step" @select="goStep" />

    <!-- 第 1 步：测什么 -->
    <section v-if="draft.step === 1" class="step" data-testid="wizard-step-1">
      <header class="step-head">
        <h1>这次要验证什么？</h1>
        <p class="muted">
          可以选一个建议，也可以把目标改得更具体。写清楚“谁在什么场景下做什么、结果应该是什么”。
        </p>
      </header>

      <SkeletonBlock v-if="draft.suggestLoading" :lines="3" label="正在生成目标候选" />
      <ErrorNotice
        v-else-if="draft.suggestError"
        :error="draft.suggestError"
        retry-label="重新获取"
        @retry="retrySuggest"
      />

      <div v-else-if="draft.candidates.length" class="candidates">
        <button
          v-for="(candidate, index) in draft.candidates"
          :key="candidate.title"
          type="button"
          class="candidate"
          :class="{ chosen: draft.candidateIndex === index }"
          :aria-pressed="draft.candidateIndex === index"
          data-testid="candidate"
          @click="chooseCandidate(index)"
        >
          <span class="candidate-title">{{ candidate.title }}</span>
          <span class="candidate-meta">
            <span v-if="candidate.reason">{{ candidate.reason }}</span>
            <span v-if="sourceLabel(candidate.source)" class="source">
              来源：{{ sourceLabel(candidate.source) }}
            </span>
          </span>
          <span class="candidate-mark" aria-hidden="true">
            <Icon v-if="draft.candidateIndex === index" name="check" :size="13" />
          </span>
        </button>
      </div>

      <div v-else-if="!draft.suggestLoading" class="no-signal">
        <div class="no-signal-copy">
          <p class="muted">
            这个项目还没有足够的提交或历史记录，先给一个通用目标：
          </p>
          <button type="button" class="fallback" @click="chooseFallback">
            {{ draft.fallback || "验证项目能否启动并跑通主流程" }}
          </button>
        </div>
      </div>

      <Field
        label="本次目标"
        for-id="goal-input"
        hint="可以直接修改上面的建议；也可以完全自己写。"
        required
      >
        <textarea
          id="goal-input"
          v-model="draft.goal"
          class="textarea"
          rows="3"
          placeholder="例如：验证下单成功后订单出现在列表里，库存扣减一次"
          @input="onGoalInput"
        ></textarea>
      </Field>

      <div class="step-actions">
        <AppButton
          variant="primary"
          :disabled="!draft.goal.trim()"
          data-testid="wizard-next"
          @click="next"
        >
          下一步：确认验收标准
        </AppButton>
      </div>
    </section>

    <!-- 第 2 步：验收标准 -->
    <section v-else-if="draft.step === 2" class="step" data-testid="wizard-step-2">
      <header class="step-head">
        <h1>哪些结果可以判定？</h1>
        <p class="muted">
          验收标准要能被观察到：看到什么、查到什么、返回什么。左侧勾选决定这次是否纳入，下方“可观察”决定它能不能作为验收依据；不可观察的条目不会进入范围。
        </p>
      </header>

      <SkeletonBlock
        v-if="draft.itemsLoading && !draft.items.length"
        :lines="4"
        label="正在从目标里提取验收标准"
      />

      <template v-else>
        <ErrorNotice
          v-if="draft.itemsError"
          :error="draft.itemsError"
          retry-label="重新提取"
          :busy="draft.itemsLoading"
          @retry="ensureTask"
        />

        <div v-if="!draft.items.length" class="empty-note">
          <p class="muted">
            {{
              draft.itemsError
                ? "也可以先自己补一条可观察的标准继续。"
                : "没有提取出可观察的验收标准。可以直接补一条，例如“下单后订单列表出现该订单，且库存只减一次”。"
            }}
          </p>
        </div>

        <ul v-else class="items" data-testid="acceptance-list">
          <li
            v-for="item in draft.items"
            :key="item.id"
            class="item"
            :class="{ off: !item.observable }"
          >
            <label class="enable">
              <input
                type="checkbox"
                :checked="item.enabled"
                :disabled="!item.observable"
                :aria-label="`启用：${item.text}`"
                @change="toggleEnabled(item)"
              />
              <span class="enable-text">启用</span>
            </label>
            <div class="item-main">
              <input
                v-model="item.text"
                class="item-input"
                type="text"
                :disabled="!item.observable"
                :aria-label="`验收标准：${item.text}`"
                @input="markEdited"
              />
              <div class="item-meta">
                <label class="observable">
                  <input
                    type="checkbox"
                    :checked="item.observable"
                    @change="toggleObservable(item)"
                  />
                  可观察
                </label>
                <span v-if="item.source" class="quiet">来源：{{ item.source }}</span>
                <span v-if="!item.observable" class="unobservable">
                  不可观察：不能作为验收依据，暂不进入范围
                </span>
              </div>
            </div>
            <button
              type="button"
              class="remove"
              :aria-label="`移除：${item.text}`"
              @click="removeItem(item)"
            >
              <Icon name="close" :size="14" />
            </button>
          </li>
        </ul>

        <form class="add-item" @submit.prevent="addItem">
          <input
            v-model="newItem"
            class="add-input"
            type="text"
            placeholder="补一条可观察的验收标准"
            aria-label="补充验收标准"
          />
          <AppButton html-type="submit" size="sm" :disabled="!newItem.trim()">
            <Icon name="plus" :size="13" />
            添加
          </AppButton>
        </form>

        <div v-if="goalChanged" class="goal-change">
          <p class="hint">目标已修改；当前验收标准仍来自上一次提取。</p>
          <AppButton size="sm" variant="ghost" :loading="draft.itemsLoading" @click="reextract">
            按新目标重新提取
          </AppButton>
        </div>
        <p v-if="draft.itemsDirty" class="hint">
          改动会保存到这次任务的验收标准；下一步时自动保存。
        </p>
        <p v-if="!canNextFromCriteria" class="error-text">
          至少需要一条可观察的验收标准才能继续。
        </p>
      </template>

      <div class="step-actions">
        <AppButton variant="ghost" @click="goStep(1)">上一步</AppButton>
        <AppButton
          variant="primary"
          :disabled="!canNextFromCriteria"
          :loading="draft.itemsLoading"
          data-testid="wizard-next"
          @click="next"
        >
          下一步：选择范围
        </AppButton>
      </div>
    </section>

    <!-- 第 3 步：范围 -->
    <section v-else-if="draft.step === 3" class="step" data-testid="wizard-step-3">
      <header class="step-head">
        <h1>这次覆盖哪些验收标准？</h1>
        <p class="muted">
          选择单位是验收项，不挑用例。范围越小结论越窄，未选的条目不计入本次结论。
        </p>
      </header>

      <div v-if="!applicableCount" class="empty-note">
        <p class="muted">还没有可观察的验收标准，回到上一步补充。</p>
        <AppButton size="sm" variant="secondary" @click="goStep(2)">返回验收标准</AppButton>
      </div>

      <template v-else>
        <div class="scope-head">
          <div class="segmented" role="radiogroup" aria-label="范围方式">
            <button
              type="button"
              role="radio"
              :aria-checked="draft.preset === 'all_applicable'"
              :class="{ on: draft.preset === 'all_applicable' }"
              @click="setPreset('all_applicable')"
            >
              全部适用
            </button>
            <button
              type="button"
              role="radio"
              :aria-checked="draft.preset === 'selected'"
              :class="{ on: draft.preset === 'selected' }"
              @click="setPreset('selected')"
            >
              自选
            </button>
          </div>
          <div class="count" data-testid="range-count">
            已选 <strong class="numeric">{{ selectedCount }}</strong>
            <span class="quiet">/ 适用</span>
            <span class="numeric">{{ applicableCount }}</span>
            条
          </div>
        </div>

        <div class="meter" aria-hidden="true">
          <span
            class="meter-fill"
            :style="{
              width: `${applicableCount ? Math.round((selectedCount / applicableCount) * 100) : 0}%`,
            }"
          ></span>
        </div>

        <div v-if="session.project && session.project.modules.length" class="modules">
          <div class="modules-head">
            <h2>涉及模块（可选）</h2>
            <span class="hint">模块信息随计划保存，用来说明改动范围；验收项仍按条目选择。</span>
          </div>
          <div class="chips">
            <button
              v-for="module in session.project.modules"
              :key="module.module_id"
              type="button"
              class="chip"
              :class="{ on: draft.moduleIds.includes(module.module_id) }"
              :aria-pressed="draft.moduleIds.includes(module.module_id)"
              @click="toggleModule(module.module_id)"
            >
              {{ module.name }}
            </button>
          </div>
        </div>

        <ul class="scope-list" data-testid="scope-list">
          <li v-for="item in draft.items" :key="item.id" :class="{ off: !item.observable }">
            <label class="scope-row">
              <input
                type="checkbox"
                :checked="
                  item.observable &&
                  (draft.preset === 'all_applicable' ||
                    draft.selected.includes(item.id))
                "
                :disabled="!item.observable || draft.preset === 'all_applicable'"
                @change="toggleSelected(item)"
              />
              <span class="scope-text">{{ item.text }}</span>
              <span v-if="!item.observable" class="unobservable">不可观察</span>
            </label>
          </li>
        </ul>
      </template>

      <div class="step-actions sticky">
        <AppButton variant="ghost" @click="goStep(2)">上一步</AppButton>
        <AppButton
          variant="primary"
          :disabled="selectedCount === 0"
          data-testid="wizard-next"
          @click="next"
        >
          下一步：生成计划
        </AppButton>
      </div>
    </section>

    <!-- 第 4 步：确认 -->
    <section v-else class="step" data-testid="wizard-step-4">
      <header class="step-head">
        <h1>确认计划后开始</h1>
        <p class="muted">
          计划按验收项生成并已发布。开始测试会新建一次运行，并记录确认时间。
        </p>
      </header>

      <SkeletonBlock v-if="draft.planLoading" :lines="5" label="正在生成测试计划" />
      <ErrorNotice
        v-else-if="draft.planError"
        :error="draft.planError"
        retry-label="重新生成"
        :busy="draft.planLoading"
        @retry="ensurePlan(true)"
      />

      <template v-else-if="draft.planDetail">
        <div class="summary" role="list" aria-label="计划摘要">
          <div class="summary-item" role="listitem">
            <span class="summary-label">范围</span>
            <span class="summary-value numeric">
              {{ planSummary.selected }}<span class="quiet">/{{ planSummary.applicable }}</span>
            </span>
            <span class="summary-note">已选 / 适用验收项</span>
          </div>
          <div class="summary-item" role="listitem">
            <span class="summary-label">用例</span>
            <span class="summary-value numeric">{{ planSummary.cases }}</span>
            <span class="summary-note">
              L1 {{ layerCounts.L1 }} · L2 {{ layerCounts.L2 }} · L3 {{ layerCounts.L3 }}
            </span>
          </div>
          <div class="summary-item" role="listitem">
            <span class="summary-label">环境</span>
            <span class="summary-value">本机</span>
            <span class="summary-note">使用项目已配置的启动方式与依赖</span>
          </div>
        </div>

        <div class="effects">
          <div class="effect">
            <span class="effect-key">副作用</span>
            <span>可能写入测试数据；默认不自动清理，使用前先确认数据环境。</span>
          </div>
          <div class="effect">
            <span class="effect-key">依赖</span>
            <span>缺少依赖时用例标为阻塞，不会自动安装，也不会伪造通过。</span>
          </div>
          <div class="effect">
            <span class="effect-key">预计耗时</span>
            <span>后端未提供估算；按用例数量与项目启动时间而定。</span>
          </div>
        </div>

        <div class="groups">
          <article v-for="group in groupedCases" :key="group.item.id" class="group">
            <header class="group-head">
              <h2>{{ group.item.text }}</h2>
              <span class="quiet">{{ group.cases.length }} 条用例</span>
            </header>
            <ul class="case-list">
              <li v-for="planCase in group.cases" :key="planCase.case_id" class="plan-case">
                <div class="plan-case-head">
                  <Badge tone="neutral">{{ LAYER_LABEL[planCase.layer] }}</Badge>
                  <span v-if="planCase.importance" class="importance">
                    {{ IMPORTANCE_LABEL[planCase.importance] ?? planCase.importance }}
                  </span>
                  <span class="method">{{ methodLabel(planCase.verify_method) }}</span>
                </div>
                <p class="expected">{{ planCase.title || planCase.expected }}</p>
                <p v-if="planCase.title && planCase.expected" class="expected-note">
                  预期：{{ planCase.expected }}
                </p>
                <details v-if="planCase.steps?.length || planCase.preconditions?.length" class="more">
                  <summary>查看步骤</summary>
                  <div v-if="planCase.preconditions?.length" class="more-block">
                    <h3>前置条件</h3>
                    <ul>
                      <li v-for="(line, index) in planCase.preconditions" :key="index">{{ line }}</li>
                    </ul>
                  </div>
                  <div v-if="planCase.steps?.length" class="more-block">
                    <h3>步骤</h3>
                    <ol>
                      <li v-for="(line, index) in planCase.steps" :key="index">{{ line }}</li>
                    </ol>
                  </div>
                </details>
                <p v-if="dependencyLabel(planCase.depends_on)" class="dep">
                  {{ dependencyLabel(planCase.depends_on) }}
                </p>
              </li>
            </ul>
          </article>
          <article v-if="projectCases.length" class="group">
            <header class="group-head">
              <h2>项目级检查</h2>
              <span class="quiet">{{ projectCases.length }} 条</span>
            </header>
            <ul class="case-list">
              <li v-for="planCase in projectCases" :key="planCase.case_id" class="plan-case">
                <div class="plan-case-head">
                  <Badge tone="neutral">{{ LAYER_LABEL[planCase.layer] }}</Badge>
                  <span class="method">{{ methodLabel(planCase.verify_method) }}</span>
                </div>
                <p class="expected">{{ planCase.title || planCase.expected }}</p>
                <p v-if="planCase.title && planCase.expected" class="expected-note">
                  预期：{{ planCase.expected }}
                </p>
                <p v-if="dependencyLabel(planCase.depends_on)" class="dep">
                  {{ dependencyLabel(planCase.depends_on) }}
                </p>
              </li>
            </ul>
          </article>
          <p v-if="!groupedCases.length && !projectCases.length" class="hint">计划里还没有用例。</p>
        </div>

        <p class="hint">
          关闭面板不会中断运行；重新打开可以在历史里找到这次运行。
        </p>

        <ErrorNotice
          v-if="draft.startError"
          :error="draft.startError"
          :show-retry="false"
          @retry="() => {}"
        />

        <div class="step-actions sticky">
          <AppButton variant="secondary" @click="backToScope">返回修改范围</AppButton>
          <AppButton
            variant="primary"
            :loading="draft.starting"
            data-testid="start-run"
            @click="startRun"
          >
            <Icon name="play" :size="14" />
            开始测试
          </AppButton>
        </div>
      </template>

      <div v-else class="empty-note">
        <p class="muted">还没有生成计划。</p>
        <AppButton size="sm" variant="secondary" @click="ensurePlan(true)">重新生成</AppButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.wizard {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-4);
}

.wizard > * {
  min-width: 0;
}

.wizard-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 2px;
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

.context {
  font-size: var(--fs-12);
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 50%;
}

.step {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  animation: enter 180ms ease-out;
}

@media (min-width: 720px) {
  .wizard {
    grid-template-columns: 168px minmax(0, 1fr);
  }

  .wizard-head {
    grid-column: 1 / -1;
  }

  .rail {
    grid-column: 1;
    grid-row: 2;
    align-self: start;
    position: sticky;
    top: 60px;
  }

  .step {
    grid-column: 2;
    grid-row: 2;
  }
}

@keyframes enter {
  from {
    opacity: 0;
    transform: translateY(3px);
  }
}

.step-head {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-head h1 {
  font-size: var(--fs-20);
}

.step-head p {
  font-size: var(--fs-13);
  max-width: 62ch;
  line-height: 1.6;
}

.candidates {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.candidate {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-3) var(--space-4);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  text-align: left;
  transition:
    border-color 140ms ease,
    background-color 140ms ease;
}

.candidate:hover {
  border-color: var(--line-strong);
  background: var(--surface-raised);
}

.candidate.chosen {
  border-color: var(--accent-line);
  background: var(--accent-fill);
}

.candidate-title {
  font-size: var(--fs-13);
  font-weight: 500;
  padding-right: 24px;
  line-height: 1.5;
}

.candidate-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
}

.source {
  color: var(--text-3);
}

.candidate-mark {
  position: absolute;
  top: 12px;
  right: 12px;
  color: var(--accent);
}

.no-signal {
  padding: var(--space-3) var(--space-4);
  background: var(--surface);
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-control);
}

.no-signal-copy {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  align-items: flex-start;
}

.fallback {
  padding: 7px 10px;
  background: var(--surface-raised);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--accent);
  font-size: var(--fs-13);
  text-align: left;
}

.fallback:hover {
  border-color: var(--accent-line);
}

.textarea,
.item-input,
.add-input {
  width: 100%;
  padding: 7px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-13);
  line-height: 1.55;
  resize: vertical;
}

.textarea::placeholder,
.add-input::placeholder {
  color: var(--text-disabled);
}

.textarea:focus,
.item-input:focus,
.add-input:focus {
  border-color: var(--accent-line);
  outline: none;
  box-shadow: 0 0 0 2px rgba(130, 175, 205, 0.16);
}

.items {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--line);
}

.item:last-child {
  border-bottom: 0;
}

.item.off {
  opacity: 0.72;
}

.item-main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.item-input {
  background: transparent;
  border-color: transparent;
  padding: 2px 4px;
}

.item-input:hover:not(:disabled) {
  background: var(--surface-sunken);
  border-color: var(--line);
}

.item-input:disabled {
  color: var(--text-3);
}

.enable {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 5px;
  flex: none;
}

.enable-text {
  font-size: 11px;
  color: var(--text-3);
}

.item-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--fs-12);
  color: var(--text-3);
  padding-left: 4px;
}

.observable {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
}

.unobservable {
  color: var(--incomplete);
}

.remove {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  background: none;
  border: 1px solid transparent;
  border-radius: 6px;
  color: var(--text-3);
}

.remove:hover {
  color: var(--failed);
  border-color: var(--line);
  background: var(--surface-raised);
}

.empty-note {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  align-items: flex-start;
  padding: var(--space-4);
  background: var(--surface);
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-control);
}

.goal-change {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background: var(--accent-fill);
  border: 1px solid rgba(130, 175, 205, 0.22);
  border-radius: var(--r-control);
}

.add-item {
  display: flex;
  gap: var(--space-2);
}

.add-item .add-input {
  flex: 1 1 auto;
}

.scope-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.segmented {
  display: inline-flex;
  padding: 2px;
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}

.segmented button {
  padding: 5px 12px;
  background: none;
  border: 0;
  border-radius: 6px;
  color: var(--text-2);
  font-size: var(--fs-12);
}

.segmented button.on {
  background: var(--surface-raised);
  color: var(--text);
  box-shadow: inset 0 0 0 1px var(--line-strong);
}

.count {
  font-size: var(--fs-13);
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}

.count strong {
  color: var(--text);
  font-size: var(--fs-16);
  font-weight: 600;
}

.meter {
  height: 3px;
  border-radius: 2px;
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  overflow: hidden;
}

.meter-fill {
  display: block;
  height: 100%;
  background: var(--accent-line);
  transition: width 180ms ease;
}

.modules {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}

.modules-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.modules-head h2 {
  font-size: var(--fs-13);
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  padding: 3px 9px;
  background: var(--surface-raised);
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--text-2);
  font-size: var(--fs-12);
}

.chip:hover {
  border-color: var(--line-strong);
  color: var(--text);
}

.chip.on {
  border-color: var(--accent-line);
  background: var(--accent-fill);
  color: var(--accent);
}

.scope-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.scope-list li {
  border-bottom: 1px solid var(--line);
}

.scope-list li:last-child {
  border-bottom: 0;
}

.scope-list li.off {
  opacity: 0.6;
}

.scope-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  cursor: pointer;
}

.scope-row:hover {
  background: var(--surface-raised);
}

.scope-list input[type="checkbox"] {
  margin-top: 3px;
}

.scope-text {
  flex: 1 1 auto;
  font-size: var(--fs-13);
  line-height: 1.55;
}

.summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: var(--space-4);
}

.summary-item + .summary-item {
  border-left: 1px solid var(--line);
}

.summary-label {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.summary-value {
  font-size: var(--fs-20);
  font-weight: 600;
  line-height: 1.2;
}

.summary-note {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
}

.effects {
  display: flex;
  flex-direction: column;
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  overflow: hidden;
}

.effect {
  display: grid;
  grid-template-columns: 76px 1fr;
  gap: var(--space-3);
  padding: 9px var(--space-4);
  background: var(--surface);
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.55;
}

.effect-key {
  color: var(--text-3);
}

.groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.group {
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--line);
  background: var(--surface-raised);
}

.group-head h2 {
  font-size: var(--fs-13);
  line-height: 1.5;
}

.case-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.plan-case {
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--line);
}

.plan-case:last-child {
  border-bottom: 0;
}

.plan-case-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: 5px;
}

.importance,
.method {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.method::before,
.importance::before {
  content: "·";
  margin-right: 5px;
  color: var(--text-disabled);
}

.expected {
  font-size: var(--fs-13);
  line-height: 1.55;
  word-break: break-word;
}

.expected-note {
  margin-top: 3px;
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.55;
}

.more {
  margin-top: 6px;
}

.more summary {
  cursor: pointer;
  font-size: var(--fs-12);
  color: var(--text-3);
  list-style: none;
}

.more summary::before {
  content: "";
  display: inline-block;
  width: 5px;
  height: 5px;
  margin-right: 6px;
  border-right: 1px solid currentColor;
  border-bottom: 1px solid currentColor;
  transform: rotate(-45deg) translateY(-1px);
}

.more[open] summary::before {
  transform: rotate(45deg) translateY(-1px);
}

.more-block {
  margin-top: 6px;
}

.more-block h3 {
  font-size: var(--fs-12);
  color: var(--text-3);
  font-weight: 500;
  margin-bottom: 3px;
}

.more-block ul,
.more-block ol {
  margin: 0;
  padding-left: 18px;
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.6;
}

.dep {
  margin-top: 5px;
  font-size: var(--fs-12);
  color: var(--incomplete);
}

.step-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding-top: var(--space-1);
}

.step-actions.sticky {
  position: sticky;
  bottom: 0;
  z-index: 6;
  margin: 0 calc(-1 * var(--space-2));
  padding: var(--space-3) var(--space-2);
  background: var(--bg);
  border-top: 1px solid var(--line);
}

input[type="checkbox"] {
  width: 14px;
  height: 14px;
  accent-color: var(--accent-line);
  cursor: pointer;
}

input[type="checkbox"]:disabled {
  cursor: not-allowed;
}

@media (max-width: 640px) {
  .summary {
    grid-template-columns: 1fr;
  }

  .summary-item + .summary-item {
    border-left: 0;
    border-top: 1px solid var(--line);
  }

  .effect {
    grid-template-columns: 1fr;
    gap: 2px;
  }

  .step-actions {
    flex-direction: column-reverse;
  }

  .step-actions :deep(.btn) {
    width: 100%;
  }
}
</style>
