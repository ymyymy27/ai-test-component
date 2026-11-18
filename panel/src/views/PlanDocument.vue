<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "@/api/commands";
import { describeError } from "@/api/errors";
import { INTENT_LABEL, LAYER_LABEL, formatDateTime, methodLabel } from "@/api/labels";
import type {
  Delivery,
  ModuleSuggestion,
  Plan,
  PlanCase,
  Task,
  TemplateSummary,
} from "@/api/types";
import {
  closeSheet,
  selectTask,
  workspace,
} from "@/state/workspace";

const task = computed<Task | null>(() =>
  workspace.historical ? (workspace.run?.task ?? workspace.task) : workspace.task,
);
const plan = computed<Plan | null>(() => {
  if (workspace.historical && workspace.run?.plan) {
    return workspace.run.plan;
  }
  return workspace.plan;
});
const stale = computed(
  () => !workspace.historical && workspace.planDetail?.stale === true,
);
const locked = computed(() => workspace.historical || workspace.stage === "running");
const planReadonly = computed(() => locked.value || !plan.value || stale.value);

const title = ref("");
const goal = ref("");
const intent = ref<"smoke" | "business">("business");
const items = ref<
  {
    item_id?: string;
    text: string;
    observable: boolean;
    status: "active" | "pending";
    source?: string;
    module_id?: string | null;
  }[]
>([]);
const cases = ref<PlanCase[]>([]);
const delivery = ref<Delivery | null>(null);
const deliveryCommit = ref("");
const deliveryCommands = ref("");
const deliveryDone = ref("");
const deliveryNotDone = ref("");
const deliveryTestData = ref("");
const draftNote = ref("");
const drafting = ref(false);
const templates = ref<TemplateSummary[]>([]);
const templateNote = ref("");
const templateError = ref("");
const applyingTemplate = ref("");
const editingTask = ref(false);
const editingDelivery = ref(false);
const editingCase = ref("");
const moduleSuggestions = ref<ModuleSuggestion[]>([]);
const moduleRegistered = ref<string[]>([]);
const moduleNote = ref("");
const moduleError = ref("");
const suggestingModules = ref(false);
const adoptingModule = ref("");
const note = ref("");
const error = ref("");

const planLabel = computed(() => {
  if (!plan.value) {
    return "还没有计划";
  }
  const number = plan.value.plan_no ? `计划 ${plan.value.plan_no}` : "计划";
  return plan.value.revision > 1 ? `${number} · 第 ${plan.value.revision} 版` : number;
});

const selectedCount = computed(() => plan.value?.scope.acceptance_item_ids.length ?? 0);

function scopeState(item: { item_id?: string; status: string }): string {
  if (item.status !== "active") {
    return "待补充";
  }
  const id = item.item_id ?? "";
  const chosen = plan.value?.scope.acceptance_item_ids ?? [];
  return id && chosen.includes(id) ? "本次" : "未纳入";
}
const applicable = computed(() => plan.value?.applicable_count ?? 0);

function fill(): void {
  const current = task.value;
  title.value = current?.title ?? "";
  goal.value = current?.goal ?? "";
  intent.value = current?.intent ?? "business";
  items.value = (current?.acceptance_items ?? []).map((item) => ({
    item_id: item.item_id,
    text: item.text,
    observable: item.observable,
    status: item.status,
    source: item.source,
    module_id: item.module_id ?? null,
  }));
  cases.value = (plan.value?.cases ?? []).map((item) => ({
    ...item,
    preconditions: [...(item.preconditions ?? [])],
    steps: [...(item.steps ?? [])],
  }));
}

async function loadDelivery(): Promise<void> {
  if (!workspace.project || !task.value) {
    return;
  }
  try {
    const data = await api.showDelivery(workspace.project.project_id, task.value.task_id);
    delivery.value = data.delivery;
    deliveryCommit.value = data.delivery?.commit ?? "";
    deliveryCommands.value = (data.delivery?.run_commands ?? []).join("\n");
    deliveryDone.value = (data.delivery?.done ?? []).join("\n");
    deliveryNotDone.value = (data.delivery?.not_done ?? []).join("\n");
    deliveryTestData.value = data.delivery?.test_data ?? "";
  } catch {
    delivery.value = null;
  }
}

function addItem(): void {
  items.value.push({ text: "", observable: true, status: "active" });
}

function removeItem(index: number): void {
  items.value.splice(index, 1);
}

function lines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function saveTask(): Promise<void> {
  if (!workspace.project || !task.value || locked.value) {
    return;
  }
  note.value = "";
  error.value = "";
  const acceptance = items.value
    .map((item) => ({
      ...(item.item_id ? { item_id: item.item_id } : {}),
      text: item.text.trim(),
      observable: item.observable,
      status: item.status,
      source: item.source ?? "",
      module_id: item.module_id ?? null,
    }))
    .filter((item) => item.text);
  if (acceptance.length === 0) {
    error.value = "至少保留一条验收项。";
    return;
  }
  try {
    await api.saveTask({
      projectId: workspace.project.project_id,
      taskId: task.value.task_id,
      revision: task.value.revision,
      patch: {
        title: title.value.trim() || task.value.title,
        goal: goal.value.trim(),
        intent: intent.value,
        acceptance_items: acceptance,
      },
    });
    await selectTask(task.value.task_id);
    closeSheet();
  } catch (failure) {
    const friendly = describeError(failure);
    error.value = `${friendly.title}。${friendly.next}`;
  }
}

async function saveCases(): Promise<void> {
  if (!workspace.project || !plan.value || planReadonly.value) {
    return;
  }
  note.value = "";
  error.value = "";
  try {
    await api.updatePlan({
      projectId: workspace.project.project_id,
      planId: plan.value.plan_id,
      revision: plan.value.revision,
      cases: cases.value,
    });
    await selectTask(plan.value.task_id);
    workspace.run = null;
    workspace.historical = false;
    workspace.stage = "plan";
    closeSheet();
  } catch (failure) {
    const friendly = describeError(failure);
    error.value = `${friendly.title}。${friendly.next}`;
  }
}

async function draftDelivery(): Promise<void> {
  if (!workspace.project || !task.value || locked.value) {
    return;
  }
  draftNote.value = "";
  drafting.value = true;
  try {
    const data = await api.draftDelivery(
      workspace.project.project_id,
      task.value.task_id,
      deliveryCommit.value.trim() || undefined,
    );
    if (!data.draft) {
      draftNote.value =
        data.message ||
        (data.reason === "model_missing"
          ? "模型未配置，未生成起草。"
          : "未生成起草，请手工填写。");
      return;
    }
    deliveryDone.value = data.draft.done.join("\n");
    deliveryNotDone.value = data.draft.not_done.join("\n");
    deliveryCommands.value = data.draft.run_commands.join("\n");
    deliveryTestData.value = data.draft.test_data.join("；");
    editingDelivery.value = true;
    draftNote.value = "AI 已起草；请检查后再保存。";
  } catch (failure) {
    const friendly = describeError(failure);
    draftNote.value = `${friendly.title}。${friendly.next}`;
  } finally {
    drafting.value = false;
  }
}

async function loadTemplates(): Promise<void> {
  try {
    const data = await api.listTemplates();
    templates.value = data.templates;
  } catch {
    templates.value = [];
  }
}

async function applyTemplate(name: string): Promise<void> {
  if (!workspace.project || !task.value) {
    return;
  }
  applyingTemplate.value = name;
  templateNote.value = "";
  templateError.value = "";
  try {
    const data = await api.applyTemplate({
      projectId: workspace.project.project_id,
      taskId: task.value.task_id,
      templateName: name,
      ...(plan.value ? { planId: plan.value.plan_id } : {}),
    });
    const summary = data.template.summary;
    templateNote.value =
      `已应用 ${data.template.name}：适用 ${summary.applicable}，` +
      `不适用 ${summary.not_applicable}，不支持 ${summary.unsupported}，无法判断 ${summary.unknown}。`;
    if (workspace.project && workspace.plan) {
      const detail = await api.showPlan(workspace.project.project_id, workspace.plan.plan_id);
      workspace.plan = detail.plan;
      workspace.planDetail = detail;
    }
  } catch (failure) {
    const friendly = describeError(failure);
    templateError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    applyingTemplate.value = "";
  }
}

async function suggestModules(): Promise<void> {
  if (!workspace.project) {
    return;
  }
  suggestingModules.value = true;
  moduleNote.value = "";
  moduleError.value = "";
  try {
    const data = await api.suggestModules(workspace.project.project_id);
    moduleSuggestions.value = data.modules;
    moduleRegistered.value = data.registered;
    moduleNote.value = data.modules.length
      ? "AI 已给出模块建议；逐条采用后才会写入项目。"
      : "没有新的模块建议。";
  } catch (failure) {
    const friendly = describeError(failure);
    moduleError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    suggestingModules.value = false;
  }
}

async function adoptModule(suggestion: ModuleSuggestion): Promise<void> {
  if (!workspace.project) {
    return;
  }
  adoptingModule.value = suggestion.name;
  moduleNote.value = "";
  moduleError.value = "";
  try {
    await api.addModule(workspace.project.project_id, {
      name: suggestion.name,
      responsibility: suggestion.responsibility,
      owner: suggestion.owner,
      deps: suggestion.deps,
    });
    moduleRegistered.value = [...moduleRegistered.value, suggestion.name];
    moduleSuggestions.value = moduleSuggestions.value.filter(
      (item) => item.name !== suggestion.name,
    );
    moduleNote.value = `已采用模块：${suggestion.name}。`;
  } catch (failure) {
    const friendly = describeError(failure);
    moduleError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    adoptingModule.value = "";
  }
}

async function saveDelivery(): Promise<void> {
  if (!workspace.project || !task.value) {
    return;
  }
  note.value = "";
  error.value = "";
  try {
    await api.saveDelivery({
      projectId: workspace.project.project_id,
      taskId: task.value.task_id,
      commit: deliveryCommit.value.trim(),
      done: lines(deliveryDone.value),
      not_done: lines(deliveryNotDone.value),
      changed_modules: delivery.value?.changed_modules ?? [],
      interface_changes: delivery.value?.interface_changes ?? [],
      run_commands: lines(deliveryCommands.value),
      test_data: deliveryTestData.value,
      dependencies: delivery.value?.dependencies ?? [],
      mocks: delivery.value?.mocks ?? [],
      self_test_evidence: delivery.value?.self_test_evidence ?? [],
    });
    note.value = "交付说明已保存。";
    editingDelivery.value = false;
    await loadDelivery();
  } catch (failure) {
    const friendly = describeError(failure);
    error.value = `${friendly.title}。${friendly.next}`;
  }
}

function caseItems(item: PlanCase): string[] {
  return [
    ...(item.acceptance_item_id ? [item.acceptance_item_id] : []),
    ...(item.acceptance_item_ids ?? []),
  ];
}

function itemText(itemId: string): string {
  return items.value.find((item) => item.item_id === itemId)?.text ?? "未命名验收项";
}

onMounted(() => {
  fill();
  void loadDelivery();
  void loadTemplates();
});
</script>

<template>
  <div class="ui-sheet" role="dialog" aria-modal="true" aria-labelledby="plan-title">
    <header class="ui-sheet-head">
      <div class="head-left">
        <button type="button" class="ui-link" @click="closeSheet">返回测试</button>
        <strong id="plan-title">任务与计划</strong>
        <span class="plan-label">{{ planLabel }}</span>
      </div>
      <span class="ui-help">{{ locked ? "只读" : "可编辑" }}</span>
    </header>

    <div class="ui-sheet-body document">
      <p v-if="stale" class="warning">
        验收项或验证方式已变化，当前计划不能再运行。
        <button type="button" class="ui-link" @click="closeSheet">回测试页生成新计划</button>
      </p>
      <p v-if="workspace.historical" class="warning">
        这是历史运行的计划快照，只读；修改请回到当前计划。
      </p>

      <section class="section">
        <div class="section-head">
          <h2>任务</h2>
          <span class="ui-help">
            修订 {{ task?.revision ?? "—" }} · {{ INTENT_LABEL[intent] }}
          </span>
        </div>
        <template v-if="!editingTask">
          <p class="task-title">{{ title || "未填写标题" }}</p>
          <p class="ui-help">{{ goal || "未填写目标" }}</p>
          <p class="ui-help">验证方式：{{ INTENT_LABEL[intent] }}</p>
          <button v-if="!locked" type="button" class="ui-btn small" @click="editingTask = true">
            编辑任务与验收项
          </button>
        </template>
        <template v-else>
          <label class="field">
            <span class="ui-label">标题</span>
            <input v-model="title" class="ui-input" :disabled="locked" />
          </label>
          <label class="field">
            <span class="ui-label">这次要验证什么</span>
            <textarea v-model="goal" class="ui-textarea" :disabled="locked" />
          </label>
          <label class="field">
            <span class="ui-label">验证方式</span>
            <select v-model="intent" class="ui-select" :disabled="locked">
              <option value="business">按验收项验证</option>
              <option value="smoke">项目快速检查</option>
            </select>
          </label>
          <div class="actions">
            <button type="button" class="ui-btn" @click="editingTask = false">取消</button>
            <button type="button" class="ui-btn primary" @click="saveTask">保存任务</button>
          </div>
        </template>
        <p v-if="locked" class="ui-help">运行中或历史运行不能改任务。</p>
      </section>

      <section class="section">
        <div class="section-head">
          <h2>验收项</h2>
          <span class="ui-help">本次范围 {{ selectedCount }} / 共 {{ applicable }} 项</span>
        </div>
        <div v-if="editingTask" class="items">
          <div v-for="(item, index) in items" :key="item.item_id ?? index" class="item-row">
            <input
              v-model="item.text"
              class="ui-input"
              :disabled="locked"
              :placeholder="`验收项 ${index + 1}`"
            />
            <label class="inline-check">
              <input v-model="item.observable" type="checkbox" :disabled="locked" />
              可观察
            </label>
            <select v-model="item.status" class="ui-select status-select" :disabled="locked">
              <option value="active">适用</option>
              <option value="pending">待补充</option>
            </select>
            <button
              v-if="!locked"
              type="button"
              class="ui-link"
              @click="removeItem(index)"
            >
              删除
            </button>
          </div>
          <button v-if="!locked" type="button" class="ui-link" @click="addItem">
            加一条验收项
          </button>
        </div>
        <div v-else class="items-read">
          <div v-for="item in items" :key="item.item_id ?? item.text" class="item-read">
            <span>{{ item.text }}</span>
            <span
              class="ui-help"
              :class="{ picked: scopeState(item) === '本次' }"
            >
              {{ scopeState(item) }}{{ item.observable ? "" : " · 不可观察" }}
            </span>
          </div>
        </div>
      </section>

      <section class="section">
        <div class="section-head">
          <h2>用例</h2>
          <span class="ui-help">{{ cases.length }} 条 · {{ plan?.status === "published" ? "已发布" : "未发布" }}</span>
        </div>
        <p v-if="!plan" class="ui-help">先在测试页选范围并生成计划。</p>
        <div v-for="(item, index) in cases" :key="item.case_id" class="case-block">
          <div class="case-head">
            <div>
              <strong>{{ item.title || `用例 ${index + 1}` }}</strong>
              <p class="ui-help">
                验收项：{{ caseItems(item).map(itemText).join("；") || "项目级检查" }}
              </p>
              <p class="ui-help">预期：{{ item.expected || "未填写" }}</p>
            </div>
            <div class="case-head-actions">
              <span class="ui-help">{{ LAYER_LABEL[item.layer] }} · {{ methodLabel(item.verify_method) }}</span>
              <button
                v-if="!planReadonly"
                type="button"
                class="ui-link"
                @click="editingCase = editingCase === item.case_id ? '' : item.case_id"
              >
                {{ editingCase === item.case_id ? "收起" : "编辑" }}
              </button>
            </div>
          </div>
          <template v-if="editingCase === item.case_id">
            <label class="field">
              <span class="ui-label">标题</span>
              <input v-model="item.title" class="ui-input" :disabled="planReadonly" />
            </label>
            <label class="field">
              <span class="ui-label">预期</span>
              <textarea v-model="item.expected" class="ui-textarea" :disabled="planReadonly" />
            </label>
            <label class="field">
              <span class="ui-label">预期依据</span>
              <input v-model="item.expectation_source" class="ui-input" :disabled="planReadonly" />
            </label>
            <label class="field">
              <span class="ui-label">前置条件（每行一条）</span>
              <textarea
                class="ui-textarea"
                :disabled="planReadonly"
                :value="(item.preconditions ?? []).join('\n')"
                @input="item.preconditions = lines(($event.target as HTMLTextAreaElement).value)"
              />
            </label>
            <label class="field">
              <span class="ui-label">步骤（每行一条）</span>
              <textarea
                class="ui-textarea"
                :disabled="planReadonly"
                :value="(item.steps ?? []).join('\n')"
                @input="item.steps = lines(($event.target as HTMLTextAreaElement).value)"
              />
            </label>
          </template>
        </div>
        <button v-if="plan && !planReadonly" type="button" class="ui-btn" @click="saveCases">
          保存为新版本
        </button>
      </section>

      <section class="section">
        <div class="section-head">
          <h2>交付说明</h2>
          <span class="ui-help">{{ delivery?.complete ? "完整" : "未完整" }}</span>
        </div>
        <p v-if="workspace.historical" class="ui-help">
          交付说明是当前项目记录，不是这次运行开始时的快照。
        </p>
        <template v-if="!editingDelivery">
          <dl class="delivery-summary">
            <div><dt>提交</dt><dd>{{ deliveryCommit || "未填写" }}</dd></div>
            <div><dt>运行命令</dt><dd>{{ lines(deliveryCommands).length }} 条</dd></div>
            <div><dt>已完成</dt><dd>{{ lines(deliveryDone).length }} 条</dd></div>
            <div><dt>测试数据</dt><dd>{{ deliveryTestData || "未填写" }}</dd></div>
          </dl>
          <div v-if="!locked" class="actions">
            <button type="button" class="ui-btn" :disabled="drafting" @click="draftDelivery">
              {{ drafting ? "正在起草…" : "AI 起草交付说明" }}
            </button>
            <button type="button" class="ui-btn small" @click="editingDelivery = true">
              手工填写
            </button>
          </div>
        </template>
        <template v-else>
          <label class="field">
            <span class="ui-label">提交</span>
            <input v-model="deliveryCommit" class="ui-input" :disabled="locked" />
          </label>
          <label class="field">
            <span class="ui-label">运行命令（每行一条）</span>
            <textarea v-model="deliveryCommands" class="ui-textarea" :disabled="locked" />
          </label>
          <label class="field">
            <span class="ui-label">已完成（每行一条）</span>
            <textarea v-model="deliveryDone" class="ui-textarea" :disabled="locked" />
          </label>
          <label class="field">
            <span class="ui-label">未完成（每行一条）</span>
            <textarea v-model="deliveryNotDone" class="ui-textarea" :disabled="locked" />
          </label>
          <label class="field">
            <span class="ui-label">测试数据</span>
            <textarea v-model="deliveryTestData" class="ui-textarea" :disabled="locked" />
          </label>
          <div class="actions">
            <button type="button" class="ui-btn" @click="editingDelivery = false">取消</button>
            <button type="button" class="ui-btn primary" @click="saveDelivery">保存交付说明</button>
          </div>
        </template>
        <p v-if="draftNote" class="ui-help">{{ draftNote }}</p>
      </section>

      <section class="section">
        <div class="section-head">
          <h2>检查模板</h2>
          <span class="ui-help">只作说明，不进入范围</span>
        </div>
        <p v-if="templates.length === 0" class="ui-help">还没有内置模板。</p>
        <div v-else class="template-list">
          <div v-for="item in templates" :key="item.template_id" class="template-row">
            <div>
              <strong>{{ item.name }}</strong>
              <span class="ui-help">{{ item.item_count }} 项 · {{ item.version }}</span>
            </div>
            <button
              type="button"
              class="ui-btn small"
              :disabled="applyingTemplate !== ''"
              @click="applyTemplate(item.template_id)"
            >
              {{ applyingTemplate === item.template_id ? "正在应用…" : "应用" }}
            </button>
          </div>
        </div>
        <p v-if="templateError" class="ui-error">{{ templateError }}</p>
        <p v-if="templateNote" class="ui-help">{{ templateNote }}</p>
      </section>

      <section class="section">
        <div class="section-head">
          <h2>模块建议</h2>
          <span class="ui-help">只写入项目，不改变范围</span>
        </div>
        <button type="button" class="ui-btn" :disabled="suggestingModules" @click="suggestModules">
          {{ suggestingModules ? "正在读项目…" : "让 AI 建议模块" }}
        </button>
        <div v-if="moduleSuggestions.length" class="module-list">
          <div v-for="item in moduleSuggestions" :key="item.name" class="module-row">
            <div>
              <strong>{{ item.name }}</strong>
              <span class="ui-help">{{ item.responsibility || "未填写职责" }}</span>
            </div>
            <button
              type="button"
              class="ui-btn small"
              :disabled="adoptingModule !== ''"
              @click="adoptModule(item)"
            >
              {{ adoptingModule === item.name ? "正在采用…" : "采用" }}
            </button>
          </div>
        </div>
        <p v-if="moduleRegistered.length" class="ui-help">
          已登记：{{ moduleRegistered.join("、") }}
        </p>
        <p v-if="moduleError" class="ui-error">{{ moduleError }}</p>
        <p v-if="moduleNote" class="ui-help">{{ moduleNote }}</p>
      </section>

      <section class="section">
        <div class="section-head">
          <h2>环境与依赖</h2>
          <span class="ui-help">在设置里维护</span>
        </div>
        <p class="ui-help">
          服务、构建、运行器、证据预算和调用链配置属于项目配置；这里只显示计划使用的依赖摘要。
        </p>
        <p class="ui-help">
          依赖：{{ [...new Set(cases.flatMap((item) => item.dependencies ?? []))].join("、") || "无额外依赖" }}
        </p>
        <p v-if="plan" class="ui-help">计划生成于 {{ formatDateTime(plan.created_at) }}</p>
      </section>

      <p v-if="error" class="ui-error">{{ error }}</p>
      <p v-if="note" class="ui-help">{{ note }}</p>
    </div>
  </div>
</template>

<style scoped>
.head-left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.plan-label {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  white-space: nowrap;
}

.document {
  padding-top: var(--space-3);
  padding-bottom: var(--space-6);
}

.warning {
  margin-bottom: var(--space-3);
  padding: 6px 8px;
  border-left: 2px solid var(--incomplete);
  background: var(--surface-raised);
  color: var(--incomplete);
  font-size: var(--fs-11);
  line-height: 1.5;
}

.section {
  padding: 12px 0;
  border-top: 1px solid var(--line);
}

.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: 7px;
}

.section-head h2 {
  margin: 0;
  font-size: var(--fs-13);
}

.task-title {
  margin: 0 0 3px;
  color: var(--text);
  font-size: var(--fs-14);
  font-weight: 600;
  line-height: 1.4;
}

.items-read {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.item-read {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 4px 0;
  border-bottom: 1px solid var(--line);
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.45;
}

.item-read span:last-child {
  flex: none;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-9, 9px);
}

.case-head-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  flex: none;
}

.delivery-summary {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 14px;
  margin: 0 0 6px;
  font-size: var(--fs-11);
  line-height: 1.45;
}

.delivery-summary div {
  min-width: 0;
}

.delivery-summary dt {
  color: var(--text-3);
  font-size: var(--fs-9, 9px);
}

.delivery-summary dd {
  margin: 0;
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.field {
  display: block;
  margin-bottom: 7px;
}

.items {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.item-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  gap: 7px;
  align-items: center;
}

.inline-check {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--text-3);
  font-size: var(--fs-10);
  white-space: nowrap;
}

.status-select {
  width: 76px;
}

.template-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.template-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--line);
}

.module-list {
  margin-top: 6px;
}

.module-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--line);
}

.module-row strong {
  display: block;
  font-size: var(--fs-11);
}

.template-row strong {
  display: block;
  font-size: var(--fs-11);
}

.actions {
  display: flex;
  gap: 6px;
  margin-top: 4px;
}

.item-read .picked {
  color: var(--accent);
}

.case-block {
  padding: 8px 0 4px;
  border-top: 1px dashed var(--line-strong);
}

.case-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: 4px;
}

.case-head strong {
  font-size: var(--fs-12);
}

@media (max-width: 520px) {
  .item-row {
    grid-template-columns: 1fr 1fr;
  }

  .head-left {
    gap: var(--space-2);
  }
}
</style>
