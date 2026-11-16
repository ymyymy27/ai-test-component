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
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import CoverageMeter from "@/components/CoverageMeter.vue";
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
    deliveryText[field.key] = draft[field.key].join("\n");
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
    delivery.saved = "草稿已填入，可修改后保存；还没有写入工作空间。";
    flash("已生成交付说明草稿");
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
  } catch (error) {
    delivery.error = describeError(error);
  } finally {
    delivery.saving = false;
  }
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
      <div class="project-head">
        <div class="head-copy">
          <h1>{{ session.project.name }}</h1>
          <p class="root mono" :title="session.project.root">{{ session.project.root }}</p>
        </div>
        <AppButton variant="ghost" size="sm" @click="openPicker">
          <Icon name="plus" :size="14" />
          登记其他目录
        </AppButton>
      </div>

      <div class="scan" role="list" aria-label="项目扫描结果">
        <div class="scan-item" role="listitem">
          <span class="scan-label">模块</span>
          <span class="scan-value numeric">{{ session.project.modules.length }}</span>
          <div v-if="session.project.modules.length" class="chips">
            <span
              v-for="module in session.project.modules"
              :key="module.module_id"
              class="chip"
              :title="module.responsibility || module.status || ''"
            >
              {{ module.name }}
            </span>
          </div>
          <span v-else class="scan-note">还没有登记模块</span>
        </div>
        <div class="scan-item" role="listitem">
          <span class="scan-label">入口</span>
          <span class="scan-value">运行前检查</span>
          <span class="scan-note">由源码可运行性检查确认构建与入口，面板不单独保存入口路径。</span>
        </div>
        <div class="scan-item" role="listitem">
          <span class="scan-label">最近运行</span>
          <span v-if="lastRun" class="scan-value">{{ formatDateTime(lastRun.startedAt) }}</span>
          <span v-else class="scan-value">还没有</span>
          <span v-if="lastRun" class="scan-note">
            {{ RUN_STATUS_LABEL[lastRun.status] }}，覆盖
            <span class="numeric">{{ lastRun.selected }}/{{ lastRun.applicable }}</span>
          </span>
          <span v-else class="scan-note">第一次运行后这里会显示最近一次状态。</span>
        </div>
      </div>

      <section class="card stack" data-testid="delivery-draft">
        <div class="section-title">
          <h2>交付说明</h2>
          <span class="section-note">AI 只起草，保存前可以改</span>
        </div>
        <div class="delivery-head">
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
        <div class="row">
          <AppButton
            :loading="delivery.loading"
            :disabled="!delivery.taskId"
            data-testid="delivery-draft-button"
            @click="draftDelivery"
          >
            AI 起草交付说明
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
        </div>
        <p class="hint">
          起草会读取 README、项目清单与 git 改动统计，不会自动保存；保存只写入工作空间。
        </p>
        <p v-if="delivery.saved" class="hint" data-testid="delivery-saved">{{ delivery.saved }}</p>
        <ErrorNotice
          v-if="delivery.error"
          :error="delivery.error"
          :show-retry="false"
          @retry="() => {}"
        />
        <div class="delivery-grid">
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
        </div>
      </section>

      <section class="card stack" data-testid="module-suggest">
        <div class="section-title">
          <h2>模块建议</h2>
          <span class="section-note">建议不写库，确认后逐个登记</span>
        </div>
        <div class="row">
          <AppButton
            :loading="moduleSuggest.loading"
            data-testid="module-suggest-button"
            @click="suggestModules"
          >
            AI 建议模块
          </AppButton>
          <AppButton
            v-if="moduleSuggest.items.length"
            variant="primary"
            :loading="moduleSuggest.saving"
            data-testid="module-confirm-button"
            @click="confirmSuggestions"
          >
            确认写入 {{ moduleSuggest.items.length }} 个模块
          </AppButton>
          <AppButton
            v-if="moduleSuggest.items.length"
            variant="ghost"
            @click="clearSuggestions"
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
        <ul v-if="moduleSuggest.items.length" class="suggestions" data-testid="module-suggestions">
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
      </section>

      <section class="last card">
        <div class="section-title">
          <h2>上次结论</h2>
          <span v-if="lastRun" class="section-note">
            {{ formatDateTime(lastRun.startedAt) }}
          </span>
        </div>
        <template v-if="lastRun">
          <div class="last-row">
            <div class="last-verdict">
              <Badge
                v-if="lastRun.outcome"
                :tone="VERDICT_TONE[lastRun.outcome]"
              >
                {{ VERDICT_LABEL[lastRun.outcome] }}
              </Badge>
              <Badge v-else tone="running">结论未生成</Badge>
              <span class="status-prefix">运行</span>
              <Badge :tone="RUN_STATUS_BADGE[lastRun.status]">
                {{ RUN_STATUS_LABEL[lastRun.status] }}
              </Badge>
            </div>
            <CoverageMeter
              :selected="lastRun.selected"
              :applicable="lastRun.applicable"
              size="sm"
            />
            <AppButton size="sm" variant="secondary" @click="openResult(session.history[0].run_id)">
              查看结果
            </AppButton>
          </div>
          <p class="hint">
            结论只对最近一次运行所选的范围成立；未选验收项不计入。
          </p>
        </template>
        <div v-else class="last-empty">
          <p class="muted">这个项目还没有运行记录。</p>
          <p class="hint">从“新建测试”开始：选目标、确认验收标准、选定范围，再执行。</p>
        </div>
      </section>

      <div class="actions">
        <AppButton variant="primary" @click="startWizard">
          <Icon name="play" :size="14" />
          新建测试
        </AppButton>
        <AppButton variant="secondary" @click="openHistory">
          <Icon name="history" :size="14" />
          查看历史
        </AppButton>
      </div>
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
}

.project-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
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
}

.scan {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.scan-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: var(--space-4);
  min-width: 0;
}

.scan-item + .scan-item {
  border-left: 1px solid var(--line);
}

.scan-label {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.scan-value {
  font-size: var(--fs-16);
  font-weight: 600;
}

.scan-note {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.chip {
  font-size: var(--fs-12);
  color: var(--text-2);
  background: var(--surface-raised);
  border: 1px solid var(--line);
  border-radius: var(--r-chip);
  padding: 0 6px;
  white-space: nowrap;
}

.last-row {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: var(--space-4);
}

.last-verdict {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.status-prefix {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.last-empty {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.delivery-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-3);
}

.delivery-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}

.textarea {
  min-height: 62px;
  resize: vertical;
  line-height: 1.55;
}

.suggestions {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}

.suggestions li {
  padding: var(--space-3) var(--space-4);
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

@media (max-width: 700px) {
  .delivery-head,
  .delivery-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .scan {
    grid-template-columns: 1fr;
  }

  .scan-item + .scan-item {
    border-left: 0;
    border-top: 1px solid var(--line);
  }

  .last-row {
    grid-template-columns: 1fr;
    align-items: start;
    gap: var(--space-3);
  }

  .last-row > :last-child {
    justify-self: start;
  }
}
</style>
