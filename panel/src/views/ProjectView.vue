<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import {
  RUN_STATUS_BADGE,
  RUN_STATUS_LABEL,
  VERDICT_LABEL,
  VERDICT_TONE,
  formatDateTime,
} from "@/api/labels";
import type { ModuleSuggestion, TaskSummary } from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import AppDrawer from "@/components/AppDrawer.vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import {
  addProjectByPath,
  flash,
  go,
  isBusy,
  openHistory,
  openProject,
  openResult,
  session,
  startWizard,
} from "@/state/session";

const picking = ref(false);
const pathValue = ref("");
const nameValue = ref("");
const touched = ref(false);
const pathInput = ref<HTMLInputElement | null>(null);
const drawerOpen = ref(false);

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

const DELIVERY_FIELDS: {
  key: DeliveryTextKey;
  label: string;
  hint: string;
  rows: number;
}[] = [
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
  tasks: [] as TaskSummary[],
  taskId: "",
  commit: "",
  loading: false,
  saving: false,
  generated: false,
  error: null as FriendlyError | null,
  saved: "",
});

const moduleSuggest = reactive({
  items: [] as ModuleSuggestion[],
  loading: false,
  saving: false,
  error: null as FriendlyError | null,
  shown: false,
});

const busy = computed(() => isBusy("projectAdd") || isBusy("project"));
const trimmedPath = computed(() => pathValue.value.trim());
const pathError = computed(() =>
  touched.value && !trimmedPath.value ? "请填写项目根目录的绝对路径" : "",
);

const lastRun = computed(() => session.lastRun);
const currentTask = computed(
  () => delivery.tasks.find((task) => task.task_id === delivery.taskId) ?? null,
);

const hasDraft = computed(() =>
  DELIVERY_FIELDS.some((field) => deliveryText[field.key].trim().length > 0),
);

const deliverySummary = computed(() => [
  {
    key: "changed_modules",
    label: "变更模块",
    items: splitLines("changed_modules"),
  },
  { key: "run_commands", label: "运行方式", items: splitLines("run_commands") },
  { key: "mocks", label: "Mock", items: splitLines("mocks") },
  { key: "not_done", label: "已知问题", items: splitLines("not_done") },
]);

onMounted(() => {
  // 编辑器只有一个工作区目录时会带入路径，用户仍然可以修改。
  if (!pathValue.value && session.prefillRoot) {
    pathValue.value = session.prefillRoot;
  }
  void loadTasks();
});

watch(
  () => session.prefillRoot,
  (value) => {
    if (!pathValue.value && value) {
      pathValue.value = value;
    }
  },
);

watch(
  () => session.project?.project_id,
  () => {
    // 切换项目时丢弃上一份草稿，避免把 A 项目的摘要显示在 B 项目下。
    for (const field of DELIVERY_FIELDS) {
      deliveryText[field.key] = "";
    }
    delivery.commit = "";
    delivery.saved = "";
    delivery.generated = false;
    delivery.error = null;
    drawerOpen.value = false;
    void loadTasks();
  },
);

async function loadTasks(): Promise<void> {
  if (!session.project) {
    delivery.tasks = [];
    delivery.taskId = "";
    return;
  }
  try {
    const data = await api.listTasks(session.project.project_id);
    delivery.tasks = data.tasks ?? [];
    if (delivery.taskId && !delivery.tasks.some((task) => task.task_id === delivery.taskId)) {
      delivery.taskId = "";
    }
    if (!delivery.taskId && delivery.tasks.length > 0) {
      delivery.taskId = delivery.tasks[0].task_id;
    }
  } catch {
    delivery.tasks = [];
  }
}

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

async function draftDelivery(): Promise<void> {
  if (!session.project || !delivery.taskId) {
    return;
  }
  delivery.loading = true;
  delivery.error = null;
  delivery.saved = "";
  try {
    const result = await api.draftDelivery(
      session.project.project_id,
      delivery.taskId,
      delivery.commit.trim() || undefined,
    );
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
  if (!session.project || !delivery.taskId) {
    return;
  }
  delivery.saving = true;
  delivery.error = null;
  try {
    const result = await api.saveDelivery({
      projectId: session.project.project_id,
      taskId: delivery.taskId,
      commit: delivery.commit.trim(),
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
    drawerOpen.value = false;
  } catch (error) {
    delivery.error = describeError(error);
  } finally {
    delivery.saving = false;
  }
}

function openDeliveryDrawer(): void {
  drawerOpen.value = true;
}

async function suggestModules(): Promise<void> {
  if (!session.project) {
    return;
  }
  moduleSuggest.loading = true;
  moduleSuggest.error = null;
  moduleSuggest.shown = false;
  try {
    const result = await api.suggestModules(session.project.project_id);
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
  if (!session.project || moduleSuggest.items.length === 0) {
    return;
  }
  moduleSuggest.saving = true;
  moduleSuggest.error = null;
  try {
    for (const item of moduleSuggest.items) {
      await api.addModule(session.project.project_id, {
        name: item.name,
        responsibility: item.responsibility,
        owner: item.owner,
        deps: item.deps,
      });
    }
    flash(`已写入 ${moduleSuggest.items.length} 个模块`);
    moduleSuggest.items = [];
    moduleSuggest.shown = false;
    await openProject(session.project.project_id, { keepView: true });
  } catch (error) {
    moduleSuggest.error = describeError(error);
  } finally {
    moduleSuggest.saving = false;
  }
}

function clearSuggestions(): void {
  moduleSuggest.items = [];
  moduleSuggest.shown = false;
  moduleSuggest.error = null;
}

function openPicker(): void {
  picking.value = true;
  touched.value = false;
  if (!pathValue.value && session.prefillRoot) {
    pathValue.value = session.prefillRoot;
  }
  void nextTick(() => pathInput.value?.focus());
}

async function submit(): Promise<void> {
  touched.value = true;
  if (!trimmedPath.value) {
    pathInput.value?.focus();
    return;
  }
  const ok = await addProjectByPath(trimmedPath.value, nameValue.value.trim() || undefined);
  if (ok) {
    picking.value = false;
    pathValue.value = "";
    nameValue.value = "";
  }
}
</script>

<template>
  <div class="stack">
    <ErrorNotice
      v-if="session.bootError"
      :error="session.bootError"
      :show-retry="false"
      @retry="() => {}"
    />

    <form v-if="picking" class="card stack" @submit.prevent="submit">
      <div class="section-title">
        <h2>登记项目</h2>
        <span class="section-note">面板无法直接弹出系统目录选择，请粘贴绝对路径</span>
      </div>
      <label class="field">
        <span class="label">项目根目录</span>
        <input
          id="project-root"
          ref="pathInput"
          v-model="pathValue"
          class="input mono"
          type="text"
          placeholder="例如 E:\work\order-service"
          spellcheck="false"
          autocomplete="off"
          :aria-invalid="Boolean(pathError)"
          aria-describedby="project-root-hint"
          @blur="touched = true"
        />
        <span v-if="pathError" class="error-text">{{ pathError }}</span>
        <span v-else id="project-root-hint" class="hint">
          目录必须存在且可写；同一个目录重复登记会直接切换到已有项目。
        </span>
      </label>
      <label class="field">
        <span class="label">项目名称（可选）</span>
        <input
          v-model="nameValue"
          class="input"
          type="text"
          placeholder="默认使用目录名"
          autocomplete="off"
        />
      </label>
      <div class="row">
        <AppButton html-type="submit" variant="primary" :loading="isBusy('projectAdd')">
          添加此目录
        </AppButton>
        <AppButton variant="ghost" @click="picking = false">取消</AppButton>
      </div>
    </form>

    <!-- 无项目：只保留选择目录与说明 -->
    <template v-if="!session.project">
      <EmptyState title="先选择一个项目文件夹" icon="folder">
        <template #body>
          面板只读取你选择的目录，用它登记项目、扫描模块并保存测试记录。数据保存在本机的用户数据工作空间，不写进被测源码。
        </template>
        <template #actions>
          <AppButton variant="primary" :disabled="busy" @click="openPicker">
            选择项目文件夹
          </AppButton>
        </template>
      </EmptyState>

      <div class="footnote">
        <Icon name="info" :size="14" />
        <span>需要先设置用户数据工作空间与 Python 解释器，才能登记项目。</span>
        <button type="button" class="link" @click="go('settings')">打开设置</button>
      </div>
    </template>

    <!-- 有项目 -->
    <template v-else>
      <section class="overview card" data-testid="project-overview">
        <div class="overview-head">
          <div class="head-copy">
            <h1>{{ session.project.name }}</h1>
            <p class="root mono" :title="session.project.root">{{ session.project.root }}</p>
          </div>
          <div class="head-actions">
            <AppButton variant="primary" @click="startWizard">
              <Icon name="play" :size="14" />
              新建测试
            </AppButton>
            <AppButton variant="secondary" @click="openHistory">
              <Icon name="history" :size="14" />
              查看历史
            </AppButton>
          </div>
        </div>

        <dl class="facts">
          <div class="fact">
            <dt>模块</dt>
            <dd>
              <span class="numeric">{{ session.project.modules.length }}</span>
              <span v-if="session.project.modules.length" class="chips">
                <span
                  v-for="module in session.project.modules"
                  :key="module.module_id"
                  class="chip"
                  :title="module.responsibility || module.status || ''"
                >
                  {{ module.name }}
                </span>
              </span>
              <span v-else class="quiet">还没有登记模块</span>
            </dd>
          </div>
          <div class="fact">
            <dt>入口</dt>
            <dd>
              运行前检查
              <span class="fact-note">由源码可运行性检查确认，面板不单独保存入口路径</span>
            </dd>
          </div>
          <div class="fact">
            <dt>最近运行</dt>
            <dd v-if="lastRun">
              <span class="numeric">{{ formatDateTime(lastRun.startedAt) }}</span>
              <Badge :tone="RUN_STATUS_BADGE[lastRun.status]">
                {{ RUN_STATUS_LABEL[lastRun.status] }}
              </Badge>
            </dd>
            <dd v-else>还没有运行</dd>
          </div>
          <div class="fact">
            <dt>上次结论</dt>
            <dd>
              <template v-if="lastRun">
                <Badge v-if="lastRun.outcome" :tone="VERDICT_TONE[lastRun.outcome]">
                  {{ VERDICT_LABEL[lastRun.outcome] }}
                </Badge>
                <Badge v-else tone="neutral">结论未生成</Badge>
                <span class="coverage-line numeric">
                  {{ lastRun.selected }} / {{ lastRun.applicable }}
                </span>
                <span class="quiet">已选 / 适用</span>
                <button
                  type="button"
                  class="link"
                  @click="openResult(session.history[0].run_id)"
                >
                  查看结果
                </button>
              </template>
              <span v-else class="quiet">第一次运行后显示</span>
            </dd>
          </div>
        </dl>

        <details class="more" data-testid="module-suggest">
          <summary>更多</summary>
          <div class="more-panel">
            <div class="more-row">
              <AppButton
                size="sm"
                :loading="moduleSuggest.loading"
                data-testid="module-suggest-button"
                @click="suggestModules"
              >
                AI 建议模块
              </AppButton>
              <AppButton
                v-if="moduleSuggest.items.length"
                size="sm"
                variant="primary"
                :loading="moduleSuggest.saving"
                data-testid="module-confirm-button"
                @click="confirmSuggestions"
              >
                确认写入 {{ moduleSuggest.items.length }} 个模块
              </AppButton>
              <AppButton
                v-if="moduleSuggest.items.length"
                size="sm"
                variant="ghost"
                @click="clearSuggestions"
              >
                取消
              </AppButton>
              <AppButton size="sm" variant="ghost" @click="openPicker">登记其他目录</AppButton>
            </div>
            <ErrorNotice
              v-if="moduleSuggest.error"
              :error="moduleSuggest.error"
              :show-retry="false"
              @retry="() => {}"
            />
            <ul
              v-if="moduleSuggest.items.length"
              class="suggestions"
              data-testid="module-suggestions"
            >
              <li v-for="item in moduleSuggest.items" :key="item.name">
                <div class="suggestion-head">
                  <strong>{{ item.name }}</strong>
                  <span v-if="item.owner" class="quiet">负责人：{{ item.owner }}</span>
                </div>
                <p v-if="item.responsibility" class="suggestion-note">{{ item.responsibility }}</p>
                <p v-if="item.deps.length" class="suggestion-note">
                  依赖：
                  {{ item.deps.map((dep) => `${dep.to}（${dep.type}）`).join("、") }}
                </p>
              </li>
            </ul>
            <p v-else-if="moduleSuggest.shown" class="hint">模型没有给出模块建议。</p>
            <p v-else class="hint">建议不写库；确认后再逐条登记。</p>
          </div>
        </details>
      </section>

      <section class="card delivery" data-testid="delivery-draft">
        <div class="section-title">
          <h2>交付说明</h2>
          <div class="title-actions">
            <span v-if="currentTask" class="section-note truncate">
              {{ currentTask.title || currentTask.task_id }}
            </span>
            <span v-else class="section-note">还没有任务</span>
            <AppButton
              v-if="hasDraft"
              size="sm"
              variant="secondary"
              data-testid="delivery-edit"
              @click="openDeliveryDrawer"
            >
              查看 / 编辑
            </AppButton>
          </div>
        </div>

        <div v-if="hasDraft" class="summary">
          <div v-for="group in deliverySummary" :key="group.key" class="summary-group">
            <span class="summary-key">{{ group.label }}</span>
            <span v-if="group.items.length" class="chips">
              <span v-for="item in group.items" :key="item" class="chip">{{ item }}</span>
            </span>
            <span v-else class="quiet summary-empty">未记录</span>
          </div>
          <p class="hint summary-note">
            {{
              delivery.generated
                ? "这是本次生成的草稿；保存后写入工作空间，重开面板需要重新生成。"
                : "草稿只在本次面板会话中保留。"
            }}
          </p>
        </div>

        <div v-else class="empty-line">
          <p class="muted">
            AI 读取 README、项目清单与 git 改动，起草变更模块、运行方式、Mock 与已知问题。
          </p>
          <AppButton
            size="sm"
            :loading="delivery.loading"
            :disabled="!delivery.taskId"
            data-testid="delivery-draft-button"
            @click="draftDelivery"
          >
            AI 生成摘要
          </AppButton>
        </div>

        <p v-if="!delivery.taskId" class="hint">
          新建测试后会生成一条任务，交付说明从那条任务起草。
        </p>
        <p v-if="delivery.saved" class="hint" data-testid="delivery-saved">{{ delivery.saved }}</p>
        <ErrorNotice
          v-if="delivery.error && !drawerOpen"
          :error="delivery.error"
          :show-retry="false"
          @retry="() => {}"
        />
      </section>

      <AppDrawer
        :open="drawerOpen"
        title="交付说明"
        note="AI 只起草，字段都可以改；保存写入工作空间。"
        @close="drawerOpen = false"
      >
        <div class="drawer-fields">
          <div class="drawer-top">
            <label class="field">
              <span class="label">任务</span>
              <select v-model="delivery.taskId" class="input" data-testid="delivery-task">
                <option v-if="delivery.tasks.length === 0" value="">还没有任务</option>
                <option v-for="task in delivery.tasks" :key="task.task_id" :value="task.task_id">
                  {{ task.title || task.task_id }}
                </option>
              </select>
            </label>
            <label class="field">
              <span class="label">提交（可选）</span>
              <input
                v-model="delivery.commit"
                class="input mono"
                type="text"
                placeholder="例如 abc123"
                spellcheck="false"
              />
            </label>
          </div>

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

          <p class="hint">
            起草会读取 README、项目清单与 git 改动统计，不会自动保存；保存只写入工作空间。
          </p>
          <ErrorNotice
            v-if="delivery.error"
            :error="delivery.error"
            :show-retry="false"
            @retry="() => {}"
          />
        </div>

        <template #footer>
          <AppButton :loading="delivery.loading" :disabled="!delivery.taskId" @click="draftDelivery">
            AI 起草
          </AppButton>
          <AppButton
            variant="primary"
            :loading="delivery.saving"
            :disabled="!delivery.taskId"
            data-testid="delivery-save-button"
            @click="saveDelivery"
          >
            保存交付说明
          </AppButton>
        </template>
      </AppDrawer>
    </template>
  </div>
</template>

<style scoped>
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.label {
  font-size: var(--fs-12);
  color: var(--text-2);
  font-weight: 500;
}

.input {
  width: 100%;
  min-height: 34px;
  padding: 6px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-13);
}

.input::placeholder {
  color: var(--text-disabled);
}

.input:focus {
  border-color: var(--accent-line);
  outline: none;
  box-shadow: 0 0 0 2px rgba(130, 175, 205, 0.18);
}

.footnote {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-12);
  color: var(--text-3);
  flex-wrap: wrap;
}

.link {
  background: none;
  border: 0;
  padding: 0;
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 3px;
  font-size: var(--fs-12);
}

.overview {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.overview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.head-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.root {
  color: var(--text-3);
  font-size: var(--fs-12);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 46ch;
}

.head-actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  overflow: hidden;
}

.fact {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
  padding: 10px var(--space-3);
  background: var(--surface-raised);
}

.fact dt {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.fact dd {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: var(--fs-13);
  min-width: 0;
}

.fact dd :deep(.badge) {
  flex: none;
}

.coverage-line {
  font-size: var(--fs-14);
  font-weight: 600;
  color: var(--text);
}

.fact-note {
  display: block;
  flex: 1 1 100%;
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
}

.chips {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
}

.chip {
  font-size: var(--fs-12);
  color: var(--text-2);
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-chip);
  padding: 0 6px;
  white-space: nowrap;
}

.more {
  border-top: 1px solid var(--line);
  padding-top: var(--space-3);
}

.more summary {
  cursor: pointer;
  list-style: none;
  width: fit-content;
  font-size: var(--fs-12);
  color: var(--text-3);
  padding: 2px 4px;
  border-radius: 4px;
}

.more summary::-webkit-details-marker {
  display: none;
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

.more summary:hover {
  color: var(--text-2);
  background: var(--surface-raised);
}

.more-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.more-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.delivery {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.delivery .section-title {
  margin-bottom: 0;
}

.title-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.summary {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.summary-group {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  align-items: baseline;
  gap: var(--space-3);
}

.summary-key {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.summary-empty {
  font-size: var(--fs-12);
}

.summary-note {
  padding-top: var(--space-1);
  border-top: 1px solid var(--line);
}

.empty-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.empty-line .muted {
  font-size: var(--fs-13);
  max-width: 54ch;
  line-height: 1.6;
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
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--line);
}

.suggestions li:last-child {
  border-bottom: 0;
}

.suggestion-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.suggestion-note {
  margin-top: 3px;
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.55;
}

.drawer-fields {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.drawer-top {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-3);
}

.textarea {
  min-height: 56px;
  resize: vertical;
  line-height: 1.55;
}

@media (max-width: 720px) {
  .facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .facts {
    grid-template-columns: 1fr;
  }

  .head-actions {
    width: 100%;
  }

  .head-actions :deep(.btn) {
    flex: 1 1 auto;
  }

  .summary-group {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .drawer-top {
    grid-template-columns: 1fr;
  }
}
</style>
