<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import type { ModuleSuggestion, TemplateDisposition, TemplateSummary } from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import AppDrawer from "@/components/AppDrawer.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import {
  addProposalItem,
  applicableCount,
  applyProposalTemplate,
  closeDrawers,
  flash,
  removeProposalItem,
  saveAdjustment,
  selectedCount,
  session,
  setProposalPreset,
  toggleProposalItem,
  toggleProposalModule,
  updateProposalItem,
  type DraftItem,
} from "@/state/session";

type DeliveryTextKey =
  | "done"
  | "not_done"
  | "changed_modules"
  | "interface_changes"
  | "run_commands"
  | "test_data"
  | "dependencies"
  | "mocks"
  | "self_test_evidence";

const DELIVERY_FIELDS: { key: DeliveryTextKey; label: string; hint: string; rows: number }[] = [
  { key: "done", label: "已完成", hint: "每行一条", rows: 2 },
  { key: "not_done", label: "未完成", hint: "每行一条；没有就留空", rows: 2 },
  { key: "changed_modules", label: "改动模块", hint: "每行一个模块名", rows: 2 },
  { key: "interface_changes", label: "接口变更", hint: "每行一条", rows: 2 },
  { key: "run_commands", label: "运行命令", hint: "每行一条；不知道就留空", rows: 2 },
  { key: "test_data", label: "测试数据", hint: "说明测试数据环境", rows: 2 },
  { key: "dependencies", label: "依赖", hint: "每行一个依赖", rows: 2 },
  { key: "mocks", label: "Mock", hint: "每行一个被 Mock 的依赖", rows: 2 },
  { key: "self_test_evidence", label: "自测证据", hint: "每行一条证据", rows: 2 },
];

const DISPOSITION_LABEL: Record<TemplateDisposition, string> = {
  applicable: "适用",
  not_applicable: "不适用",
  unsupported: "不支持",
  unknown: "未知",
};

const proposal = computed(() => session.proposal);
const saving = ref(false);
const saveError = ref<FriendlyError | null>(null);
const editingId = ref("");
const editText = ref("");
const editObservable = ref(true);
const editInput = ref<HTMLInputElement | null>(null);
const showAdd = ref(false);
const newItem = ref("");
const addInput = ref<HTMLInputElement | null>(null);

const templatePanel = reactive({
  options: [] as TemplateSummary[],
  choice: "",
  loading: false,
  applying: false,
  error: null as FriendlyError | null,
  loaded: false,
});

const deliveryText = reactive<Record<DeliveryTextKey, string>>({
  done: "",
  not_done: "",
  changed_modules: "",
  interface_changes: "",
  run_commands: "",
  test_data: "",
  dependencies: "",
  mocks: "",
  self_test_evidence: "",
});

const delivery = reactive({
  loading: false,
  saving: false,
  generated: false,
  error: null as FriendlyError | null,
  saved: "",
});
const deliveryCommit = ref("");

const moduleSuggest = reactive({
  items: [] as ModuleSuggestion[],
  loading: false,
  saving: false,
  error: null as FriendlyError | null,
  shown: false,
});

const appliedTemplates = computed(() => proposal.value.previewPlan?.templates ?? []);
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

const hasTask = computed(() => Boolean(session.proposal.taskId));
const deliverySummary = computed(() => [
  { key: "changed_modules", label: "变更模块", items: splitLines("changed_modules") },
  { key: "run_commands", label: "运行方式", items: splitLines("run_commands") },
  { key: "mocks", label: "Mock", items: splitLines("mocks") },
  { key: "not_done", label: "已知问题", items: splitLines("not_done") },
]);
const hasDelivery = computed(() =>
  DELIVERY_FIELDS.some((field) => deliveryText[field.key].trim().length > 0),
);
const deliveryState = computed(() =>
  delivery.saved ? "已保存" : hasDelivery.value ? "未保存" : "未填写",
);
const templateState = computed(() =>
  appliedTemplates.value.length ? `已应用 ${appliedTemplates.value.length} 个` : "未应用",
);
const moduleState = computed(() =>
  moduleSuggest.items.length
    ? `${moduleSuggest.items.length} 个待确认`
    : moduleSuggest.shown
      ? "没有建议"
      : "未生成",
);

function splitLines(key: DeliveryTextKey): string[] {
  return deliveryText[key]
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function fillDraft(draft: Record<DeliveryTextKey, string[]>): void {
  for (const field of DELIVERY_FIELDS) {
    const value: unknown = draft[field.key];
    deliveryText[field.key] = Array.isArray(value)
      ? value.filter((line): line is string => typeof line === "string").join("\n")
      : typeof value === "string"
        ? value
        : "";
  }
}

async function loadTemplates(): Promise<void> {
  if (templatePanel.loading || templatePanel.loaded) {
    return;
  }
  templatePanel.loading = true;
  try {
    const data = await api.listTemplates();
    templatePanel.options = data.templates ?? [];
    if (!templatePanel.choice && templatePanel.options.length > 0) {
      templatePanel.choice = templatePanel.options[0].template_id;
    }
    templatePanel.loaded = true;
  } catch {
    templatePanel.options = [];
  } finally {
    templatePanel.loading = false;
  }
}

async function applyTemplate(): Promise<void> {
  if (!templatePanel.choice || templatePanel.applying) {
    return;
  }
  templatePanel.applying = true;
  templatePanel.error = null;
  const error = await applyProposalTemplate(templatePanel.choice);
  if (error) {
    templatePanel.error = error;
  }
  if (!proposal.value.itemsDirty) {
    // 应用模板可能先保存了验收项；把当前草稿作为新的取消基线。
    snapshot = capture();
  }
  templatePanel.applying = false;
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

function commitEdit(item: DraftItem): void {
  updateProposalItem(item.id, {
    text: editText.value,
    observable: editObservable.value,
  });
  editingId.value = "";
}

function cancelEdit(): void {
  editingId.value = "";
}

function remove(item: DraftItem): void {
  removeProposalItem(item.id);
  editingId.value = "";
}

async function openAdd(): Promise<void> {
  showAdd.value = true;
  await nextTick();
  addInput.value?.focus();
}

function submitAdd(): void {
  addProposalItem(newItem.value);
  newItem.value = "";
  showAdd.value = false;
}

async function draftDelivery(): Promise<void> {
  const projectId = session.project?.project_id;
  const taskId = session.proposal.taskId;
  if (!projectId || !taskId) {
    return;
  }
  delivery.loading = true;
  delivery.error = null;
  delivery.saved = "";
  try {
    const result = await api.draftDelivery(projectId, taskId);
    fillDraft(result.draft);
    delivery.generated = true;
    flash("已生成交付摘要；保存后才会写入工作空间");
  } catch (error) {
    delivery.error = describeError(error);
  } finally {
    delivery.loading = false;
  }
}

async function saveDelivery(): Promise<void> {
  const projectId = session.project?.project_id;
  const taskId = session.proposal.taskId;
  if (!projectId || !taskId) {
    return;
  }
  delivery.saving = true;
  delivery.error = null;
  try {
    const result = await api.saveDelivery({
      projectId,
      taskId,
      commit: deliveryCommit.value.trim(),
      done: splitLines("done"),
      not_done: splitLines("not_done"),
      changed_modules: splitLines("changed_modules"),
      interface_changes: splitLines("interface_changes"),
      run_commands: splitLines("run_commands"),
      test_data: deliveryText.test_data.trim(),
      dependencies: splitLines("dependencies"),
      mocks: splitLines("mocks"),
      self_test_evidence: splitLines("self_test_evidence"),
    });
    delivery.saved = result.complete
      ? "交付说明已保存，运行命令与提交都已填写。"
      : "交付说明已保存，但缺运行命令或提交，标记为不完整。";
    flash(delivery.saved);
  } catch (error) {
    delivery.error = describeError(error);
  } finally {
    delivery.saving = false;
  }
}

async function suggestModules(): Promise<void> {
  const projectId = session.project?.project_id;
  if (!projectId) {
    return;
  }
  moduleSuggest.loading = true;
  moduleSuggest.error = null;
  moduleSuggest.shown = false;
  try {
    const result = await api.suggestModules(projectId);
    moduleSuggest.items = result.modules ?? [];
    moduleSuggest.shown = true;
  } catch (error) {
    moduleSuggest.items = [];
    moduleSuggest.error = describeError(error);
  } finally {
    moduleSuggest.loading = false;
  }
}

async function confirmSuggestions(): Promise<void> {
  const projectId = session.project?.project_id;
  if (!projectId || moduleSuggest.items.length === 0) {
    return;
  }
  moduleSuggest.saving = true;
  moduleSuggest.error = null;
  try {
    for (const item of moduleSuggest.items) {
      await api.addModule(projectId, {
        name: item.name,
        responsibility: item.responsibility,
        owner: item.owner,
        deps: item.deps,
      });
    }
    const data = await api.showProject(projectId);
    session.project = data.project;
    flash(`已写入 ${moduleSuggest.items.length} 个模块`);
    moduleSuggest.items = [];
    moduleSuggest.shown = false;
  } catch (error) {
    moduleSuggest.error = describeError(error);
  } finally {
    moduleSuggest.saving = false;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  saveError.value = null;
  const ok = await saveAdjustment();
  saving.value = false;
  if (ok) {
    committed = true;
    closeDrawers();
  } else {
    saveError.value = session.proposal.itemsError;
  }
}

interface Snapshot {
  items: DraftItem[];
  preset: "all_applicable" | "selected";
  selected: string[];
  moduleIds: string[];
  itemsDirty: boolean;
}

let snapshot: Snapshot | null = null;
let committed = false;

function capture(): Snapshot {
  const proposal = session.proposal;
  return {
    items: proposal.items.map((item) => ({ ...item })),
    preset: proposal.preset,
    selected: [...proposal.selected],
    moduleIds: [...proposal.moduleIds],
    itemsDirty: proposal.itemsDirty,
  };
}

function restore(snapshotValue: Snapshot): void {
  const proposal = session.proposal;
  proposal.items = snapshotValue.items.map((item) => ({ ...item }));
  proposal.preset = snapshotValue.preset;
  proposal.selected = [...snapshotValue.selected];
  proposal.moduleIds = [...snapshotValue.moduleIds];
  proposal.itemsDirty = snapshotValue.itemsDirty;
  proposal.previewPlan = null;
  proposal.previewSignature = "";
}

watch(
  () => session.adjustOpen,
  (open) => {
    if (open) {
      snapshot = capture();
      committed = false;
      void loadTemplates();
      return;
    }
    if (snapshot) {
      if (!committed) {
        restore(snapshot);
      }
      snapshot = null;
    }
  },
);
</script>

<template>
  <AppDrawer
    :open="session.adjustOpen"
    title="调整测试单"
    note="改的是这次测试单；保存后才写回任务，不启动运行。"
    @close="closeDrawers"
  >
    <div class="adjust" data-testid="adjust-drawer">
      <section class="block">
        <div class="section-title">
          <h3>验收标准</h3>
          <span class="section-note">勾选纳入，编辑或移除单条</span>
        </div>

        <ul v-if="proposal.items.length" class="item-list">
          <li
            v-for="item in proposal.items"
            :key="item.id"
            class="item-row"
            :class="{ off: !item.observable }"
          >
            <template v-if="editingId === item.id">
              <div class="edit">
                <input
                  :ref="setEditInput"
                  v-model="editText"
                  class="input"
                  type="text"
                  aria-label="编辑验收标准"
                  @keydown.enter.prevent="commitEdit(item)"
                />
                <label class="check-line">
                  <input v-model="editObservable" type="checkbox" />
                  可作为验收依据
                </label>
                <div class="row">
                  <AppButton size="sm" @click="commitEdit(item)">完成</AppButton>
                  <AppButton size="sm" variant="ghost" @click="cancelEdit">取消</AppButton>
                  <AppButton size="sm" variant="danger" @click="remove(item)">移除</AppButton>
                </div>
              </div>
            </template>
            <template v-else>
              <label class="check-line item-check">
                <input
                  type="checkbox"
                  :checked="item.enabled"
                  :disabled="!item.observable"
                  :aria-label="`纳入：${item.text}`"
                  @change="toggleProposalItem(item)"
                />
                <span class="item-text">{{ item.text }}</span>
              </label>
              <span v-if="!item.observable" class="quiet tiny">不可观察，暂不纳入</span>
              <button type="button" class="link" @click="startEdit(item)">编辑</button>
            </template>
          </li>
        </ul>
        <p v-else class="hint">还没有验收标准，先补一条。</p>

        <form v-if="showAdd" class="add-row" @submit.prevent="submitAdd">
          <input
            ref="addInput"
            v-model="newItem"
            class="input"
            type="text"
            placeholder="例如：下单后订单列表出现该订单，且库存只减一次"
            aria-label="补充验收标准"
          />
          <div class="row">
            <AppButton html-type="submit" size="sm" :disabled="!newItem.trim()">添加</AppButton>
            <AppButton size="sm" variant="ghost" @click="showAdd = false; newItem = ''">
              取消
            </AppButton>
          </div>
        </form>
        <button v-else type="button" class="add-link" @click="openAdd">
          <Icon name="plus" :size="13" />
          添加一条
        </button>
      </section>

      <section class="block">
        <div class="section-title">
          <h3>范围</h3>
          <span class="section-note numeric">
            {{ selectedCount() }} / {{ applicableCount() }} 条适用
          </span>
        </div>
        <div class="segmented" role="radiogroup" aria-label="范围方式">
          <button
            type="button"
            role="radio"
            :aria-checked="proposal.preset === 'all_applicable'"
            :class="{ on: proposal.preset === 'all_applicable' }"
            @click="setProposalPreset('all_applicable')"
          >
            全部适用
          </button>
          <button
            type="button"
            role="radio"
            :aria-checked="proposal.preset === 'selected'"
            :class="{ on: proposal.preset === 'selected' }"
            @click="setProposalPreset('selected')"
          >
            自选
          </button>
        </div>
        <p class="hint">范围越小结论越窄；未选的条目不进入本次结论。</p>

        <div v-if="session.project?.modules.length" class="module-block">
          <span class="label">按模块标记（可选）</span>
          <div class="module-chips">
            <button
              v-for="module in session.project.modules"
              :key="module.module_id"
              type="button"
              class="chip"
              :class="{ on: proposal.moduleIds.includes(module.module_id) }"
              :aria-pressed="proposal.moduleIds.includes(module.module_id)"
              @click="toggleProposalModule(module.module_id)"
            >
              {{ module.name }}
            </button>
          </div>
          <p class="hint">模块只用于说明改动范围，不缩小本次验收项。</p>
        </div>
      </section>

      <details class="block fold" data-testid="delivery-fold">
        <summary class="fold-head">
          <span class="fold-title">交付说明</span>
          <span class="fold-state">{{ deliveryState }}</span>
          <Icon class="fold-chev" name="chevron-right" :size="13" />
        </summary>
        <div class="fold-body">
          <div class="fold-tools">
            <span class="hint">README、项目清单与 git 改动可自动起草，字段都能改。</span>
            <AppButton
              size="sm"
              variant="secondary"
              :loading="delivery.loading"
              :disabled="!hasTask"
              data-testid="delivery-draft-button"
              @click="draftDelivery"
            >
              AI 起草
            </AppButton>
          </div>

          <div v-if="hasDelivery" class="summary">
            <div v-for="group in deliverySummary" :key="group.key" class="summary-group">
              <span class="summary-key">{{ group.label }}</span>
              <span v-if="group.items.length" class="chips">
                <span v-for="item in group.items" :key="item" class="chip">{{ item }}</span>
              </span>
              <span v-else class="quiet tiny">未记录</span>
            </div>
          </div>
          <p v-else class="hint">
            AI 读取 README、项目清单与 git 改动，起草变更模块、运行方式、Mock 与已知问题；字段都可以改。
          </p>

          <details class="delivery-fields">
            <summary>编辑交付字段（9 项）</summary>
            <label class="field">
              <span class="label">提交（可选）</span>
              <input
                v-model="deliveryCommit"
                class="input mono"
                type="text"
                placeholder="例如 abc123"
                spellcheck="false"
              />
            </label>
            <label v-for="field in DELIVERY_FIELDS" :key="field.key" class="field">
              <span class="label">{{ field.label }}</span>
              <textarea
                v-model="deliveryText[field.key]"
                class="input textarea"
                :rows="field.rows"
                :aria-label="field.label"
              ></textarea>
              <span class="hint">{{ field.hint }}</span>
            </label>
            <div class="row">
              <AppButton
                size="sm"
                variant="primary"
                :loading="delivery.saving"
                :disabled="!hasTask"
                data-testid="delivery-save-button"
                @click="saveDelivery"
              >
                保存交付说明
              </AppButton>
              <span class="hint">保存只写入工作空间。</span>
            </div>
            <ErrorNotice
              v-if="delivery.error"
              :error="delivery.error"
              :show-retry="false"
              @retry="() => {}"
            />
            <p v-if="delivery.saved" class="hint">{{ delivery.saved }}</p>
          </details>
          <p v-if="!hasTask" class="hint">先生成一条验收标准，才能起草交付说明。</p>
        </div>
      </details>

      <details class="block fold" data-testid="template-fold">
        <summary class="fold-head">
          <span class="fold-title">模板检查项</span>
          <span class="fold-state">{{ templateState }}</span>
          <Icon class="fold-chev" name="chevron-right" :size="13" />
        </summary>
        <div class="fold-body">
          <p class="hint">只读提示，不进入必测分母。</p>

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
            <span class="quiet">{{ appliedTemplates.map((template) => template.name).join("、") }}</span>
          </div>
          <p v-else class="hint">还没有应用模板。应用后只读展示每项的处置与理由。</p>

          <div class="template-controls">
            <select
              v-model="templatePanel.choice"
              class="input"
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
              :disabled="!templatePanel.choice || templatePanel.options.length === 0 || !hasTask"
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

          <div v-for="template in appliedTemplates" :key="template.template_id" class="template">
            <div class="template-head">
              <h4>{{ template.name }}</h4>
              <span class="quiet tiny">{{ template.version }} · {{ template.applied_at }}</span>
            </div>
            <ul class="template-items">
              <li v-for="item in template.items" :key="item.item_id">
                <span class="disposition" :class="`disposition-${item.disposition}`">
                  {{ DISPOSITION_LABEL[item.disposition] }}
                </span>
                <span>{{ item.objective || item.item_id }}</span>
                <span class="template-reason">{{ item.reason || "未提供理由" }}</span>
              </li>
            </ul>
          </div>
        </div>
      </details>

      <details class="block fold" data-testid="module-fold">
        <summary class="fold-head">
          <span class="fold-title">模块建议</span>
          <span class="fold-state">{{ moduleState }}</span>
          <Icon class="fold-chev" name="chevron-right" :size="13" />
        </summary>
        <div class="fold-body">
          <div class="fold-tools">
            <span class="hint">建议不写库；确认后再逐条登记。</span>
            <AppButton
              size="sm"
              :loading="moduleSuggest.loading"
              data-testid="module-suggest-button"
              @click="suggestModules"
            >
              AI 建议模块
            </AppButton>
          </div>
          <ul v-if="moduleSuggest.items.length" class="suggestions" data-testid="module-suggestions">
            <li v-for="item in moduleSuggest.items" :key="item.name">
              <div class="suggestion-head">
                <strong>{{ item.name }}</strong>
                <span v-if="item.owner" class="quiet tiny">负责人：{{ item.owner }}</span>
              </div>
              <p v-if="item.responsibility" class="suggestion-note">{{ item.responsibility }}</p>
              <p v-if="item.deps.length" class="suggestion-note">
                依赖：{{ item.deps.map((dep) => `${dep.to}（${dep.type}）`).join("、") }}
              </p>
            </li>
          </ul>
          <p v-else-if="moduleSuggest.shown" class="hint">模型没有给出模块建议。</p>
          <div v-if="moduleSuggest.items.length" class="row">
            <AppButton
              size="sm"
              variant="primary"
              :loading="moduleSuggest.saving"
              data-testid="module-confirm-button"
              @click="confirmSuggestions"
            >
              确认写入 {{ moduleSuggest.items.length }} 个模块
            </AppButton>
            <AppButton
              size="sm"
              variant="ghost"
              @click="moduleSuggest.items = []; moduleSuggest.shown = false"
            >
              取消
            </AppButton>
          </div>
          <ErrorNotice
            v-if="moduleSuggest.error"
            :error="moduleSuggest.error"
            :show-retry="false"
            @retry="() => {}"
          />
        </div>
      </details>

      <ErrorNotice
        v-if="saveError"
        :error="saveError"
        :show-retry="false"
        @retry="() => {}"
      />
    </div>

    <template #footer>
      <AppButton variant="ghost" @click="closeDrawers">取消</AppButton>
      <AppButton
        variant="primary"
        :loading="saving || proposal.itemsLoading"
        data-testid="adjust-save"
        @click="save"
      >
        保存调整
      </AppButton>
    </template>
  </AppDrawer>
</template>

<style scoped>
.adjust {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.block h3 {
  font-size: var(--fs-13);
}

.block .section-title {
  margin-bottom: 0;
  align-items: center;
}

.fold {
  border-top: 1px solid var(--line);
  padding-top: var(--space-3);
  gap: 0;
}

.fold-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  list-style: none;
}

.fold-head::-webkit-details-marker {
  display: none;
}

.fold-title {
  font-size: var(--fs-13);
  font-weight: 600;
}

.fold-state {
  margin-left: auto;
  font-size: var(--fs-12);
  color: var(--text-3);
}

.fold-chev {
  flex: none;
  color: var(--text-3);
  transition: transform 160ms ease;
}

.fold[open] .fold-chev {
  transform: rotate(90deg);
}

.fold-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
}

.fold-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.item-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: 1px solid var(--line);
}

.item-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 7px 0;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}

.item-row.off {
  color: var(--text-3);
}

.item-check {
  flex: 1 1 200px;
  min-width: 0;
  align-items: flex-start;
}

.item-text {
  font-size: var(--fs-12);
  line-height: 1.55;
  word-break: break-word;
}

.check-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-12);
}

.item-check input {
  margin-top: 3px;
  flex: none;
}

.edit {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 100%;
}

.link {
  padding: 0;
  background: none;
  border: 0;
  color: var(--accent);
  font-size: var(--fs-12);
  text-decoration: underline;
  text-underline-offset: 3px;
}

.add-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 0;
  background: none;
  border: 0;
  color: var(--text-3);
  font-size: var(--fs-12);
  width: fit-content;
}

.add-link:hover {
  color: var(--accent);
}

.add-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.segmented {
  display: inline-flex;
  padding: 2px;
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  width: fit-content;
}

.segmented button {
  padding: 5px 12px;
  background: none;
  border: 0;
  border-radius: 6px;
  color: var(--text-3);
  font-size: var(--fs-12);
}

.segmented button.on {
  background: var(--surface-raised);
  color: var(--text);
  box-shadow: inset 0 0 0 1px var(--line-strong);
}

.module-block {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding-top: var(--space-2);
}

.module-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.chip {
  padding: 1px 7px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-sunken);
  color: var(--text-2);
  font-size: var(--fs-12);
}

.chip.on {
  border-color: var(--accent-line);
  background: var(--accent-fill);
  color: var(--text);
}

.label {
  font-size: var(--fs-12);
  color: var(--text-2);
  font-weight: 500;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.input {
  width: 100%;
  min-height: 32px;
  padding: 6px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-12);
}

.input::placeholder {
  color: var(--text-disabled);
}

.input:focus {
  border-color: var(--accent-line);
  outline: none;
  box-shadow: 0 0 0 2px var(--accent-glow);
}

.textarea {
  resize: vertical;
  line-height: 1.55;
}

.summary {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.summary-group {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 6px var(--space-2);
  align-items: baseline;
}

.summary-key {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.chips {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
}

.delivery-fields {
  border-top: 1px dashed var(--line);
  padding-top: var(--space-2);
}

.delivery-fields summary {
  cursor: pointer;
  font-size: var(--fs-12);
  color: var(--text-2);
  padding: 4px 0;
}

.delivery-fields .field {
  margin-top: var(--space-2);
}

.template-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px var(--space-2);
}

.disposition {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 6px;
  border-radius: var(--r-chip);
  border: 1px solid var(--line);
  background: var(--surface-sunken);
  font-size: var(--fs-12);
  color: var(--text-2);
}

.disposition-applicable {
  color: var(--passed);
  border-color: var(--passed-border);
}

.disposition-not_applicable {
  color: var(--neutral);
}

.disposition-unsupported,
.disposition-unknown {
  color: var(--incomplete);
  border-color: var(--incomplete-border);
}

.template-controls {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}

.template-controls .input {
  flex: 1 1 auto;
  min-width: 0;
}

.template {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-top: var(--space-2);
  border-top: 1px dashed var(--line);
}

.template-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}

.template-head h4 {
  margin: 0;
  font-size: var(--fs-12);
}

.template-items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--fs-12);
  color: var(--text-2);
}

.template-items li {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 2px var(--space-2);
  align-items: baseline;
}

.template-reason {
  grid-column: 2;
  color: var(--text-3);
  line-height: 1.45;
}

.suggestions {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  background: var(--surface-sunken);
}

.suggestions li {
  padding: 7px var(--space-3);
  border-bottom: 1px solid var(--line);
}

.suggestions li:last-child {
  border-bottom: 0;
}

.suggestion-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--fs-12);
}

.suggestion-note {
  margin-top: 2px;
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
}
</style>
