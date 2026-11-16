<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from "vue";

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
  TemplateDisposition,
  TemplateSummary,
} from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import StepRail from "@/components/StepRail.vue";
import UncoveredList from "@/components/UncoveredList.vue";
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
  { title: "测什么", hint: "选一句或自己写" },
  { title: "验收标准", hint: "默认全部纳入" },
  { title: "范围", hint: "默认全部适用" },
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

// 正常路径不常驻输入框：写目标、编辑验收项、补一条都按需展开。
const writing = ref(false);
const writingInput = ref<HTMLInputElement | null>(null);
const editingId = ref("");
const editText = ref("");
const editObservable = ref(true);
const editInput = ref<HTMLInputElement | null>(null);
const showAdd = ref(false);
const addInput = ref<HTMLInputElement | null>(null);
const scopeExpanded = ref(false);

const DISPOSITION_LABEL: Record<TemplateDisposition, string> = {
  applicable: "适用",
  not_applicable: "不适用",
  unsupported: "不支持",
  unknown: "未知",
};

const templatePanel = reactive({
  options: [] as TemplateSummary[],
  choice: "",
  loading: false,
  applying: false,
  error: null as FriendlyError | null,
});

const appliedTemplates = computed(() => draft.planDetail?.templates ?? []);

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
const excludedCount = computed(() => Math.max(0, applicableCount.value - selectedCount.value));
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

const uncoveredItems = computed(() => {
  const selectedIds = new Set(selectedItems.value.map((item) => item.id));
  return applicableItems.value
    .filter((item) => !selectedIds.has(item.id))
    .map((item) => `${item.id}：${item.text}`);
});

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

const dispositionChips = computed(() => {
  const totals: Record<TemplateDisposition, number> = {
    applicable: 0,
    not_applicable: 0,
    unsupported: 0,
    unknown: 0,
  };
  for (const template of appliedTemplates.value) {
    for (const key of Object.keys(totals) as TemplateDisposition[]) {
      totals[key] += template.summary?.[key] ?? 0;
    }
  }
  return (Object.keys(totals) as TemplateDisposition[])
    .filter((key) => totals[key] > 0)
    .map((key) => ({ key, label: DISPOSITION_LABEL[key], count: totals[key] }));
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
  writing.value = false;
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

async function openWriter(): Promise<void> {
  writing.value = true;
  draft.candidateIndex = -1;
  await nextTick();
  writingInput.value?.focus();
}

function closeWriter(): void {
  writing.value = false;
  draft.goal = "";
  draft.candidateIndex = -1;
}

async function pickCandidate(index: number): Promise<void> {
  chooseCandidate(index);
  await next();
}

async function chooseFallbackAndGo(): Promise<void> {
  chooseFallback();
  await next();
}

function goStep(step: number): void {
  if (step < 1 || step > 4 || step > draft.step) {
    return;
  }
  draft.step = step;
  draft.startError = null;
  if (step === 1 && draft.goal && draft.candidateIndex < 0) {
    // 回到第一步时，自己写的目标仍然可见可改。
    writing.value = true;
  }
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
  editingId.value = "";
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
  scopeExpanded.value = preset === "selected";
  if (preset === "all_applicable") {
    draft.selected = applicableItems.value.map((item) => item.id);
  }
}

function setEditInput(el: unknown): void {
  editInput.value = (el as HTMLInputElement | null) ?? null;
}

async function startEdit(item: DraftItem): Promise<void> {
  editingId.value = item.id;
  editText.value = item.text;
  editObservable.value = item.observable;
  await nextTick();
  editInput.value?.focus();
}

function cancelEdit(): void {
  editingId.value = "";
}

function commitEdit(item: DraftItem): void {
  const text = editText.value.trim();
  if (text) {
    item.text = text;
  }
  if (editObservable.value !== item.observable) {
    item.observable = editObservable.value;
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
  }
  draft.itemsDirty = true;
  editingId.value = "";
}

function retrySuggest(): void {
  suggestStarted.value = false;
  void loadSuggestions();
}

async function openAdd(): Promise<void> {
  showAdd.value = true;
  await nextTick();
  addInput.value?.focus();
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
  showAdd.value = false;
}

function removeItem(item: DraftItem): void {
  draft.items = draft.items.filter((row) => row.id !== item.id);
  draft.selected = draft.selected.filter((id) => id !== item.id);
  draft.itemsDirty = true;
  editingId.value = "";
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
  draft.step = 2;
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

async function loadTemplates(): Promise<void> {
  if (templatePanel.loading || templatePanel.options.length > 0) {
    return;
  }
  templatePanel.loading = true;
  try {
    const data = await api.listTemplates();
    templatePanel.options = data.templates ?? [];
    if (!templatePanel.choice && templatePanel.options.length > 0) {
      templatePanel.choice = templatePanel.options[0].template_id;
    }
  } catch {
    templatePanel.options = [];
  } finally {
    templatePanel.loading = false;
  }
}

async function applyTemplate(): Promise<void> {
  if (!projectId.value || !draft.taskId || !draft.plan || !templatePanel.choice) {
    return;
  }
  templatePanel.applying = true;
  templatePanel.error = null;
  try {
    await api.applyTemplate({
      projectId: projectId.value,
      taskId: draft.taskId,
      templateName: templatePanel.choice,
      planId: draft.plan.plan_id,
    });
    const detail = await api.showPlan(projectId.value, draft.plan.plan_id);
    draft.planDetail = detail.plan;
  } catch (error) {
    templatePanel.error = describeError(error);
  } finally {
    templatePanel.applying = false;
  }
}

async function ensurePlan(force = false): Promise<void> {
  if (!draft.taskId) {
    return;
  }
  void loadTemplates();
  if (!force && draft.planDetail && draft.planSignature === planSignature.value) {
    return;
  }
  draft.planLoading = true;
  draft.planError = null;
  draft.startError = null;
  try {
    const payload = {
      projectId: projectId.value,
      taskId: draft.taskId,
      preset: draft.preset,
      moduleIds: draft.moduleIds,
      acceptanceItemIds:
        draft.preset === "selected"
          ? draft.selected
          : applicableItems.value.map((item) => item.id),
    };
    let generated;
    try {
      generated = await api.generatePlan(payload);
    } catch (error) {
      const code = (error as { code?: string } | null)?.code;
      if (code !== "HOST_CALL_FAILED" && code !== "PANEL_TIMEOUT") {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 800));
      generated = await api.generatePlan(payload);
    }
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
        <p class="muted">选一句最接近的，或自己写一句。验收标准会从目标里提取。</p>
      </header>

      <SkeletonBlock v-if="draft.suggestLoading" :lines="3" label="正在生成目标候选" />
      <ErrorNotice
        v-else-if="draft.suggestError"
        :error="draft.suggestError"
        retry-label="重新获取"
        @retry="retrySuggest"
      />

      <template v-else>
        <div v-if="draft.candidates.length" class="candidates" role="list">
          <button
            v-for="(candidate, index) in draft.candidates"
            :key="candidate.title"
            type="button"
            class="candidate"
            :class="{ chosen: draft.candidateIndex === index }"
            :aria-pressed="draft.candidateIndex === index"
            data-testid="candidate"
            role="listitem"
            @click="pickCandidate(index)"
          >
            <span class="candidate-title">{{ candidate.title }}</span>
            <span class="candidate-meta">
              <span v-if="candidate.reason">{{ candidate.reason }}</span>
              <span v-if="sourceLabel(candidate.source)" class="source">
                来源：{{ sourceLabel(candidate.source) }}
              </span>
            </span>
            <span class="candidate-mark" aria-hidden="true">
              <Icon name="chevron-right" :size="14" />
            </span>
          </button>
        </div>

        <button
          v-else-if="!draft.suggestLoading"
          type="button"
          class="fallback"
          @click="chooseFallbackAndGo"
        >
          <span class="fallback-label">先用一个通用目标</span>
          <span class="fallback-text">{{ draft.fallback || "验证项目能否启动并跑通主流程" }}</span>
        </button>
      </template>

      <div class="writer">
        <button v-if="!writing" type="button" class="writer-link" @click="openWriter">
          <Icon name="plus" :size="13" />
          或自己写一句
        </button>
        <form v-else class="writer-form" @submit.prevent="next">
          <input
            id="goal-input"
            ref="writingInput"
            v-model="draft.goal"
            class="writer-input"
            type="text"
            placeholder="例如：下单成功后订单出现在列表里，库存只扣一次"
            autocomplete="off"
            @input="onGoalInput"
          />
          <AppButton
            html-type="submit"
            variant="primary"
            size="sm"
            :disabled="!draft.goal.trim()"
            data-testid="wizard-next"
          >
            继续
          </AppButton>
          <AppButton size="sm" variant="ghost" @click="closeWriter">收起</AppButton>
        </form>
      </div>
    </section>

    <!-- 第 2 步：验收标准 -->
    <section v-else-if="draft.step === 2" class="step" data-testid="wizard-step-2">
      <header class="step-head">
        <h1>确认验收标准</h1>
        <p class="muted">默认全部纳入。关掉不需要的，或补一条；每条都要能被观察到。</p>
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

        <ul v-if="draft.items.length" class="items" data-testid="acceptance-list">
          <li
            v-for="item in draft.items"
            :key="item.id"
            class="item"
            :class="{ off: !item.observable, editing: editingId === item.id }"
          >
            <template v-if="editingId === item.id">
              <div class="edit">
                <input
                  :ref="setEditInput"
                  v-model="editText"
                  class="edit-input"
                  type="text"
                  aria-label="编辑验收标准"
                  @keydown.enter.prevent="commitEdit(item)"
                />
                <div class="edit-row">
                  <label class="observable">
                    <input v-model="editObservable" type="checkbox" />
                    可作为验收依据
                  </label>
                  <span class="edit-actions">
                    <AppButton size="sm" @click="commitEdit(item)">完成</AppButton>
                    <AppButton size="sm" variant="ghost" @click="cancelEdit">取消</AppButton>
                    <AppButton size="sm" variant="danger" @click="removeItem(item)">移除</AppButton>
                  </span>
                </div>
              </div>
            </template>
            <template v-else>
              <button
                type="button"
                class="switch"
                role="switch"
                :aria-checked="item.enabled"
                :disabled="!item.observable"
                :aria-label="`纳入：${item.text}`"
                @click="toggleEnabled(item)"
              >
                <span class="switch-track" aria-hidden="true"><span class="switch-knob"></span></span>
              </button>
              <div class="item-main">
                <span class="item-text">{{ item.text }}</span>
                <span class="item-meta">
                  <span v-if="!item.observable" class="unobservable">不可观察，暂不纳入</span>
                  <span v-else-if="item.source" class="quiet">{{ item.source }}</span>
                </span>
              </div>
              <button type="button" class="edit-link" @click="startEdit(item)">编辑</button>
            </template>
          </li>
        </ul>

        <div v-if="!draft.items.length && !draft.itemsError" class="empty-note">
          <p class="muted">没有提取出可观察的验收标准，补一条就可以继续。</p>
        </div>

        <form v-if="showAdd" class="add-item" @submit.prevent="addItem">
          <input
            ref="addInput"
            v-model="newItem"
            class="add-input"
            type="text"
            placeholder="例如：下单后订单列表出现该订单，且库存只减一次"
            aria-label="补充验收标准"
          />
          <AppButton html-type="submit" size="sm" :disabled="!newItem.trim()">添加</AppButton>
          <AppButton size="sm" variant="ghost" @click="showAdd = false; newItem = ''">
            取消
          </AppButton>
        </form>
        <button v-else type="button" class="add-link" @click="openAdd">
          <Icon name="plus" :size="13" />
          添加一条
        </button>

        <div v-if="goalChanged" class="goal-change">
          <span class="hint">目标改了，当前标准仍来自上一次提取。</span>
          <AppButton size="sm" variant="ghost" :loading="draft.itemsLoading" @click="reextract">
            按新目标重新提取
          </AppButton>
        </div>
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
        <h1>这次覆盖多少</h1>
        <p class="muted">默认全部适用。范围越小结论越窄，未选的条目不进入本次结论。</p>
      </header>

      <div v-if="!applicableCount" class="empty-note">
        <p class="muted">还没有可观察的验收标准，回到上一步补充。</p>
        <AppButton size="sm" variant="secondary" @click="goStep(2)">返回验收标准</AppButton>
      </div>

      <template v-else>
        <div class="scope-card">
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

          <p class="hint scope-note">
            <template v-if="draft.preset === 'all_applicable'">
              全部 {{ applicableCount }} 条适用验收项都会进入本次范围。
            </template>
            <template v-else-if="excludedCount > 0">
              已排除 {{ excludedCount }} 条；这些不进入本次结论。
            </template>
            <template v-else>已选中全部适用条目。</template>
          </p>
        </div>

        <details v-if="session.project?.modules.length" class="modules">
          <summary>按模块筛选（可选）</summary>
          <div class="modules-body">
            <p class="hint">模块随计划保存，用来说明改动范围；验收项仍按条目选择。</p>
            <div class="module-chips">
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
        </details>

        <ul v-if="scopeExpanded" class="scope-list" data-testid="scope-list">
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
        <p class="muted">计划已按验收项生成。开始测试会新建一次运行并记录确认时间。</p>
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

        <UncoveredList v-if="uncoveredItems.length" :items="uncoveredItems" class="uncovered-block" />

        <ul class="effects">
          <li class="effect">
            <span class="effect-key">副作用</span>
            <span>可能写入测试数据；默认不自动清理，使用前先确认数据环境。</span>
          </li>
          <li class="effect">
            <span class="effect-key">依赖</span>
            <span>缺少依赖时用例标为阻塞，不自动安装，也不伪造通过。</span>
          </li>
          <li class="effect">
            <span class="effect-key">耗时</span>
            <span>后端未提供估算；按用例数量与项目启动时间而定。</span>
          </li>
        </ul>

        <section class="template-block" data-testid="template-block">
          <div class="template-head">
            <h2>模板检查项</h2>
            <span class="hint">只读提示：不进入必测分母，也不改变验收项</span>
          </div>
          <div v-if="appliedTemplates.length" class="template-summary">
            <span
              v-for="chip in dispositionChips"
              :key="chip.key"
              class="disposition"
              :class="`disposition-${chip.key}`"
            >
              {{ chip.label }}
              <span class="numeric">{{ chip.count }}</span>
            </span>
            <span class="quiet template-names">
              {{ appliedTemplates.map((template) => template.name).join("、") }}
            </span>
          </div>
          <p v-else class="hint">还没有应用模板。</p>

          <details class="template-more">
            <summary>{{ appliedTemplates.length ? "查看或应用模板" : "应用模板" }}</summary>
            <div class="template-controls">
              <select
                v-model="templatePanel.choice"
                class="template-select"
                aria-label="选择模板"
                :disabled="templatePanel.loading || templatePanel.options.length === 0"
              >
                <option v-if="templatePanel.options.length === 0" value="">没有可用模板</option>
                <option
                  v-for="template in templatePanel.options"
                  :key="template.template_id"
                  :value="template.template_id"
                >
                  {{ template.name }}（{{ template.item_count }} 项）
                </option>
              </select>
              <AppButton
                size="sm"
                :loading="templatePanel.applying"
                :disabled="!templatePanel.choice || templatePanel.options.length === 0"
                data-testid="apply-template"
                @click="applyTemplate"
              >
                应用模板
              </AppButton>
            </div>
            <ErrorNotice
              v-if="templatePanel.error"
              :error="templatePanel.error"
              :show-retry="false"
              @retry="() => {}"
            />
            <div
              v-for="template in appliedTemplates"
              :key="template.template_id"
              class="template-result"
            >
              <div class="template-result-head">
                <h3>{{ template.name }}</h3>
                <span class="quiet">{{ template.version }} · {{ template.applied_at }}</span>
              </div>
              <ul class="template-items">
                <li v-for="item in template.items" :key="item.item_id">
                  <span class="disposition" :class="`disposition-${item.disposition}`">
                    {{ DISPOSITION_LABEL[item.disposition] }}
                  </span>
                  <span class="template-objective">{{ item.objective || item.item_id }}</span>
                  <span class="template-reason">{{ item.reason || "未提供理由" }}</span>
                </li>
              </ul>
            </div>
          </details>
        </section>

        <details class="cases-details" data-testid="case-details">
          <summary>查看用例明细（{{ planSummary.cases }} 条）</summary>
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
        </details>

        <p class="hint">关闭面板不会中断运行；重新打开可以在历史里找到这次运行。</p>

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
  gap: 5px;
}

.step-head h1 {
  font-size: var(--fs-20);
}

.step-head p {
  font-size: var(--fs-13);
  max-width: 60ch;
  line-height: 1.6;
}

/* 第 1 步：候选卡片 */

.candidates {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-3);
}

.candidate {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: var(--space-3) var(--space-4);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  text-align: left;
  transition:
    border-color 140ms ease,
    background-color 140ms ease,
    transform 140ms ease;
}

.candidate:hover {
  border-color: var(--accent-line);
  background: var(--surface-raised);
  transform: translateY(-1px);
}

.candidate.chosen {
  border-color: var(--accent-line);
  background: var(--accent-fill);
}

.candidate-title {
  font-size: var(--fs-13);
  font-weight: 500;
  padding-right: 22px;
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

.candidate-mark {
  position: absolute;
  top: 12px;
  right: 10px;
  color: var(--text-disabled);
  transition: color 140ms ease;
}

.candidate:hover .candidate-mark {
  color: var(--accent);
}

.fallback {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: var(--space-3) var(--space-4);
  background: var(--surface);
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-control);
  text-align: left;
}

.fallback:hover {
  border-color: var(--accent-line);
  background: var(--surface-raised);
}

.fallback-label {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.fallback-text {
  font-size: var(--fs-13);
  color: var(--accent);
}

.writer {
  padding-top: var(--space-1);
}

.writer-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: none;
  border: 0;
  padding: 2px 0;
  color: var(--text-3);
  font-size: var(--fs-12);
}

.writer-link:hover {
  color: var(--accent);
}

.writer-form {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.writer-input,
.edit-input,
.add-input {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 32px;
  padding: 6px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-13);
}

.writer-input::placeholder,
.edit-input::placeholder,
.add-input::placeholder {
  color: var(--text-disabled);
}

.writer-input:focus,
.edit-input:focus,
.add-input:focus {
  border-color: var(--accent-line);
  outline: none;
  box-shadow: 0 0 0 2px rgba(130, 175, 205, 0.16);
}

/* 第 2 步：验收项开关列表 */

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
  padding: 11px var(--space-4);
  border-bottom: 1px solid var(--line);
}

.item:last-child {
  border-bottom: 0;
}

.item.editing {
  background: var(--surface-raised);
}

.item.off {
  opacity: 0.66;
}

.item-main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.item-text {
  font-size: var(--fs-13);
  line-height: 1.55;
}

.item-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: var(--fs-12);
  color: var(--text-3);
}

.unobservable {
  color: var(--incomplete);
}

.switch {
  display: grid;
  place-items: center;
  width: 34px;
  height: 24px;
  flex: none;
  padding: 0;
  background: none;
  border: 0;
  margin-top: 1px;
}

.switch-track {
  position: relative;
  display: block;
  width: 28px;
  height: 16px;
  border-radius: 999px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  transition:
    background-color 140ms ease,
    border-color 140ms ease;
}

.switch-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--text-3);
  transition:
    transform 160ms cubic-bezier(0.16, 1, 0.3, 1),
    background-color 140ms ease;
}

.switch[aria-checked="true"] .switch-track {
  background: var(--accent-fill-strong);
  border-color: var(--accent-line);
}

.switch[aria-checked="true"] .switch-knob {
  background: var(--accent);
  transform: translateX(12px);
}

.switch:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.edit-link {
  flex: none;
  background: none;
  border: 0;
  padding: 2px 4px;
  font-size: var(--fs-12);
  color: var(--text-3);
  border-radius: 4px;
}

.edit-link:hover {
  color: var(--accent);
  background: var(--surface-sunken);
}

.edit {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  flex: 1 1 auto;
  min-width: 0;
}

.edit-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.edit-actions {
  display: flex;
  gap: var(--space-2);
}

.observable {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-12);
  color: var(--text-3);
  cursor: pointer;
}

.add-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  align-self: flex-start;
  padding: 4px 0;
  background: none;
  border: 0;
  color: var(--text-3);
  font-size: var(--fs-12);
}

.add-link:hover {
  color: var(--accent);
}

.add-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
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

/* 第 3 步：范围 */

.scope-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
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
  font-size: var(--fs-20);
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

.scope-note {
  margin: 0;
}

.modules {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  padding: var(--space-3) var(--space-4);
}

.modules summary,
.template-more summary,
.cases-details summary {
  cursor: pointer;
  list-style: none;
  font-size: var(--fs-12);
  color: var(--text-3);
  width: fit-content;
}

.modules summary::-webkit-details-marker,
.template-more summary::-webkit-details-marker,
.cases-details summary::-webkit-details-marker {
  display: none;
}

.modules summary::before,
.template-more summary::before,
.cases-details summary::before {
  content: "";
  display: inline-block;
  width: 5px;
  height: 5px;
  margin-right: 6px;
  border-right: 1px solid currentColor;
  border-bottom: 1px solid currentColor;
  transform: rotate(-45deg) translateY(-1px);
}

.modules[open] summary::before,
.template-more[open] summary::before,
.cases-details[open] summary::before {
  transform: rotate(45deg) translateY(-1px);
}

.modules summary:hover,
.template-more summary:hover,
.cases-details summary:hover {
  color: var(--text-2);
}

.modules-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.module-chips {
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

/* 第 4 步：确认 */

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

.uncovered-block {
  padding: var(--space-4);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}

.effects {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  background: var(--surface);
  overflow: hidden;
}

.effect {
  display: grid;
  grid-template-columns: 58px 1fr;
  gap: var(--space-3);
  padding: 7px var(--space-3);
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.55;
}

.effect + .effect {
  border-top: 1px solid var(--line);
}

.effect-key {
  color: var(--text-3);
}

.template-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}

.template-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.template-head h2 {
  font-size: var(--fs-13);
}

.template-head .hint {
  max-width: 52ch;
}

.template-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.template-names {
  font-size: var(--fs-12);
}

.template-more {
  margin-top: var(--space-1);
}

.template-controls {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
  margin-top: var(--space-2);
}

.template-select {
  min-height: 30px;
  padding: 4px 8px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-12);
}

.template-result {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding-top: var(--space-2);
  border-top: 1px solid var(--line);
}

.template-result-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.template-result-head h3 {
  font-size: var(--fs-13);
}

.template-items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.template-items li {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--fs-12);
  line-height: 1.55;
}

.disposition {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 0 7px;
  border-radius: var(--r-chip);
  border: 1px solid var(--line-strong);
  color: var(--text-2);
  white-space: nowrap;
  font-size: var(--fs-12);
}

.disposition-applicable {
  color: var(--accent);
  border-color: var(--accent-line);
}

.disposition-unsupported,
.disposition-unknown {
  color: var(--incomplete);
}

.template-objective {
  flex: 1 1 auto;
  min-width: 0;
}

.template-reason {
  color: var(--text-3);
}

.cases-details {
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}

.groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.group {
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  overflow: hidden;
}

.group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
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
  padding: var(--space-3);
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

@media (max-width: 520px) {
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

  .writer-form,
  .add-item {
    flex-wrap: wrap;
  }

  .writer-form :deep(.btn),
  .add-item :deep(.btn) {
    flex: 1 1 auto;
  }
}
</style>
