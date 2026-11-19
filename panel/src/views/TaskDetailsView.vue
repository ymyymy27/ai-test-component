<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import ErrorState from "@/components/ErrorState.vue";
import Icon from "@/components/Icon.vue";
import PageHeader from "@/components/PageHeader.vue";
import { BUILTIN_TEMPLATES } from "@/domain/analysis";
import { currentPlan } from "@/domain/derive";
import { LAYER_LABEL, TEMPLATE_DISPOSITION_LABEL } from "@/domain/labels";
import { parseLineList } from "@/domain/validation";
import type { AcceptanceItem, Delivery, Task } from "@/domain/types";
import type { DeliveryInput, TaskPatch } from "@/repositories/ProjectRepository";
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
const plan = computed(() => (task.value ? currentPlan(task.value) : null));
const running = computed(() => task.value?.stage === "running");

const basic = ref({
  title: "",
  goal: "",
  intent: "business" as Task["intent"],
  scope: "",
  inputs: "",
  outputs: "",
  preconditions: "",
  executor: "",
  reviewer: "",
});
const items = ref<AcceptanceItem[]>([]);
const itemsError = ref("");
const delivery = ref({
  done: "",
  notDone: "",
  changedModules: "",
  interfaceChanges: "",
  runCommands: "",
  testData: "",
  dependencies: "",
  mocks: "",
  selfTestEvidence: "",
  commit: "",
});
const deliveryError = ref("");

const busyBasic = computed(() => workspace.busyKey(`task:${taskId.value}`));
const busyDelivery = computed(() => workspace.busyKey(`delivery:${taskId.value}`));
const busyTemplate = computed(() => workspace.busyKey(`template:${taskId.value}`));
const planStale = computed(() => Boolean(plan.value?.stale));
const backTarget = computed(() => {
  const stage = task.value?.stage;
  if (stage === "planned") {
    return `/projects/${projectId.value}/tasks/${taskId.value}/plan`;
  }
  if (stage === "result") {
    return `/projects/${projectId.value}/tasks/${taskId.value}/result`;
  }
  if (stage === "running") {
    return `/projects/${projectId.value}/tasks/${taskId.value}/run`;
  }
  return `/projects/${projectId.value}/tasks/${taskId.value}/scope`;
});

function fillBasic(current: Task): void {
  basic.value = {
    title: current.title,
    goal: current.goal,
    intent: current.intent,
    scope: current.scope,
    inputs: current.inputs,
    outputs: current.outputs,
    preconditions: current.preconditions.join("\n"),
    executor: current.executor,
    reviewer: current.reviewer,
  };
}

function fillItems(current: Task): void {
  items.value = current.acceptanceItems.map((item) => ({ ...item }));
}

function fillDelivery(current: Task): void {
  const value: Delivery | null = current.delivery;
  delivery.value = {
    done: value?.done.join("\n") ?? "",
    notDone: value?.notDone.join("\n") ?? "",
    changedModules: value?.changedModules.join("\n") ?? "",
    interfaceChanges: value?.interfaceChanges.join("\n") ?? "",
    runCommands: value?.runCommands.join("\n") ?? "",
    testData: value?.testData ?? "",
    dependencies: value?.dependencies.join("\n") ?? "",
    mocks: value?.mocks.join("\n") ?? "",
    selfTestEvidence: value?.selfTestEvidence.join("\n") ?? "",
    commit: value?.commit ?? "",
  };
}

function reset(): void {
  if (task.value) {
    fillBasic(task.value);
    fillItems(task.value);
    fillDelivery(task.value);
  }
}

onMounted(async () => {
  if (task.value && workspace.currentTask?.id !== taskId.value) {
    try {
      await workspace.setActiveTask(projectId.value, taskId.value);
    } catch {
      // store 已提示。
    }
  }
  reset();
});

async function saveBasic(): Promise<void> {
  if (!task.value || running.value) {
    return;
  }
  if (!basic.value.title.trim()) {
    ui.showToast("任务标题不能为空。", "error");
    return;
  }
  const patch: TaskPatch = {
    title: basic.value.title,
    goal: basic.value.goal,
    intent: basic.value.intent,
    scope: basic.value.scope,
    inputs: basic.value.inputs,
    outputs: basic.value.outputs,
    preconditions: parseLineList(basic.value.preconditions),
    executor: basic.value.executor,
    reviewer: basic.value.reviewer,
  };
  try {
    await workspace.updateTask(projectId.value, taskId.value, patch);
    reset();
    ui.showToast("任务资料已保存。", "success");
  } catch {
    // store 已提示。
  }
}

function addItem(): void {
  const nextNo = String(items.value.length + 1).padStart(2, "0");
  items.value.push({
    id: "",
    no: nextNo,
    text: "",
    observable: true,
    status: "active",
    source: "用户补充",
    moduleId: null,
    moduleName: "未归类",
    featureName: "未归类",
  });
}

function removeItem(index: number): void {
  items.value.splice(index, 1);
}

function onObservable(item: AcceptanceItem, event: Event): void {
  const target = event.target;
  if (target instanceof HTMLInputElement) {
    item.observable = target.checked;
    if (!target.checked) {
      item.status = "pending";
    } else if (item.status === "pending") {
      item.status = "active";
    }
  }
}

function onModule(item: AcceptanceItem, event: Event): void {
  const target = event.target;
  if (target instanceof HTMLSelectElement) {
    item.moduleId = target.value || null;
    item.moduleName =
      project.value?.modules.find((module) => module.id === target.value)?.name ?? "未归类";
  }
}

async function saveItems(): Promise<void> {
  if (!task.value || running.value) {
    return;
  }
  if (items.value.some((item) => !item.text.trim())) {
    itemsError.value = "验收项文本不能为空；先补全或删除空行。";
    return;
  }
  itemsError.value = "";
  try {
    await workspace.updateTask(projectId.value, taskId.value, {
      acceptanceItems: items.value,
    });
    reset();
    ui.showToast("验收项已保存；如契约变化，旧计划会标为过期。", "success");
  } catch {
    // store 已提示。
  }
}

async function draftDelivery(): Promise<void> {
  if (!task.value || running.value) {
    return;
  }
  try {
    const draft = await workspace.draftDelivery(projectId.value, taskId.value);
    delivery.value = {
      done: draft.done.join("\n"),
      notDone: draft.notDone.join("\n"),
      changedModules: draft.changedModules.join("\n"),
      interfaceChanges: draft.interfaceChanges.join("\n"),
      runCommands: draft.runCommands.join("\n"),
      testData: draft.testData,
      dependencies: draft.dependencies.join("\n"),
      mocks: draft.mocks.join("\n"),
      selfTestEvidence: draft.selfTestEvidence.join("\n"),
      commit: draft.commit,
    };
    ui.showToast("交付草稿已填入；请修改后保存。", "info");
  } catch {
    // store 已提示。
  }
}

async function saveDelivery(): Promise<void> {
  if (!task.value || running.value) {
    return;
  }
  const input: DeliveryInput = {
    done: parseLineList(delivery.value.done),
    notDone: parseLineList(delivery.value.notDone),
    changedModules: parseLineList(delivery.value.changedModules),
    interfaceChanges: parseLineList(delivery.value.interfaceChanges),
    runCommands: parseLineList(delivery.value.runCommands),
    testData: delivery.value.testData,
    dependencies: parseLineList(delivery.value.dependencies),
    mocks: parseLineList(delivery.value.mocks),
    selfTestEvidence: parseLineList(delivery.value.selfTestEvidence),
    commit: delivery.value.commit,
  };
  if (input.runCommands.length === 0 || !input.commit.trim()) {
    deliveryError.value = "缺运行命令或提交号时仍可保存，但会标为“交付信息不完整”。";
  } else {
    deliveryError.value = "";
  }
  try {
    await workspace.saveDelivery(projectId.value, taskId.value, input);
    reset();
    ui.showToast("交付说明已保存。", "success");
  } catch {
    // store 已提示。
  }
}

async function applyTemplate(templateId: string): Promise<void> {
  if (!task.value || running.value) {
    return;
  }
  try {
    await workspace.applyTemplate(projectId.value, taskId.value, templateId);
    reset();
    ui.showToast("检查模板已应用；不进入必测分母。", "success");
  } catch {
    // store 已提示。
  }
}

function deliveryComplete(): boolean {
  return Boolean(task.value?.delivery?.complete);
}
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
      kicker="任务资料"
      :title="task.title || '未命名任务'"
      :subtitle="`${project?.name ?? ''} · 第 ${task.revision} 版 · 任务资料、交付说明与检查模板`"
    >
      <template #actions>
        <button class="btn btn-sm" type="button" @click="router.push(backTarget)">返回</button>
      </template>
    </PageHeader>

    <div v-if="running" class="notice is-warn">
      <Icon name="warn" :size="14" />
      <span>运行中不能修改任务资料或交付说明；等待运行结束后再改。</span>
    </div>
    <div v-if="planStale" class="notice is-warn">
      <Icon name="warn" :size="14" />
      <span>{{ plan?.staleReason || "任务契约已变化，旧计划过期；重新生成计划后才能启动。" }}</span>
      <button
        class="link-btn"
        type="button"
        @click="router.push(`/projects/${projectId}/tasks/${taskId}/scope`)"
      >
        重新选范围
      </button>
    </div>

    <section class="section">
      <div class="section-head">
        <h2 class="section-title-lg">基本信息</h2>
        <span class="section-note">只改标题、目标、执行人、评审人不会使计划过期</span>
      </div>
      <form class="settings-form" novalidate @submit.prevent="saveBasic">
        <div class="form-grid">
          <div class="field">
            <label for="task-title">标题</label>
            <input
              id="task-title"
              v-model="basic.title"
              type="text"
              maxlength="80"
              :disabled="running"
            />
          </div>
          <div class="field">
            <label for="task-intent">验证意图</label>
            <select id="task-intent" v-model="basic.intent" :disabled="running">
              <option value="business">按业务功能验证</option>
              <option value="smoke">只验证能否跑通</option>
            </select>
          </div>
        </div>
        <div class="field">
          <label for="task-goal">目标</label>
          <textarea id="task-goal" v-model="basic.goal" rows="2" :disabled="running" />
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="task-scope">范围说明</label>
            <input id="task-scope" v-model="basic.scope" type="text" :disabled="running" />
          </div>
          <div class="field">
            <label for="task-executor">执行人</label>
            <input
              id="task-executor"
              v-model="basic.executor"
              type="text"
              maxlength="40"
              :disabled="running"
            />
          </div>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="task-inputs">输入</label>
            <input id="task-inputs" v-model="basic.inputs" type="text" :disabled="running" />
          </div>
          <div class="field">
            <label for="task-outputs">输出</label>
            <input id="task-outputs" v-model="basic.outputs" type="text" :disabled="running" />
          </div>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="task-preconditions">前置条件 <span class="optional">一行一条</span></label>
            <textarea
              id="task-preconditions"
              v-model="basic.preconditions"
              rows="3"
              :disabled="running"
            />
          </div>
          <div class="field">
            <label for="task-reviewer">评审人</label>
            <input
              id="task-reviewer"
              v-model="basic.reviewer"
              type="text"
              maxlength="40"
              :disabled="running"
            />
          </div>
        </div>
        <div class="form-actions">
          <button
            class="btn btn-primary"
            type="submit"
            :disabled="running || busyBasic"
            :aria-busy="busyBasic"
          >
            {{ busyBasic ? "正在保存…" : "保存基本信息" }}
          </button>
        </div>
      </form>
    </section>

    <section class="section">
      <div class="section-head">
        <h2 class="section-title-lg">验收项</h2>
        <span class="section-note">改文本、可观察性、所属模块或意图会使计划过期</span>
      </div>
      <div class="acceptance-editor">
        <div
          v-for="(item, index) in items"
          :key="item.id || `new-${index}`"
          class="acceptance-edit-row"
        >
          <input
            v-model="item.no"
            class="acceptance-no"
            type="text"
            maxlength="8"
            aria-label="编号"
            :disabled="running"
          />
          <input v-model="item.text" type="text" aria-label="验收项文本" :disabled="running" />
          <label class="checkline acceptance-check">
            <input
              :checked="item.observable"
              type="checkbox"
              :disabled="running"
              @change="onObservable(item, $event)"
            />
            <span>可观察</span>
          </label>
          <select
            :value="item.moduleId ?? ''"
            aria-label="所属模块"
            :disabled="running"
            @change="onModule(item, $event)"
          >
            <option value="">未归类</option>
            <option v-for="module in project?.modules ?? []" :key="module.id" :value="module.id">
              {{ module.name }}
            </option>
          </select>
          <button class="link-btn" type="button" :disabled="running" @click="removeItem(index)">
            删除
          </button>
        </div>
      </div>
      <p v-if="itemsError" class="field-error" role="alert">{{ itemsError }}</p>
      <div class="form-actions">
        <button class="btn btn-sm" type="button" :disabled="running" @click="addItem">
          新增验收项
        </button>
        <button
          class="btn btn-primary btn-sm"
          type="button"
          :disabled="running || busyBasic"
          :aria-busy="busyBasic"
          @click="saveItems"
        >
          {{ busyBasic ? "正在保存…" : "保存验收项" }}
        </button>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2 class="section-title-lg">交付说明</h2>
        <span class="badge" :class="deliveryComplete() ? 'is-pass' : 'is-warn'">
          {{ deliveryComplete() ? "已完成" : "交付信息不完整" }}
        </span>
      </div>
      <p class="doc-note">
        缺运行命令或提交号不阻止保存；依赖构建/服务的用例会在运行中按阻塞处理并说明原因。
      </p>
      <form class="settings-form" novalidate @submit.prevent="saveDelivery">
        <div class="form-grid">
          <div class="field">
            <label for="delivery-done">已完成 <span class="optional">一行一条</span></label>
            <textarea id="delivery-done" v-model="delivery.done" rows="3" :disabled="running" />
          </div>
          <div class="field">
            <label for="delivery-not-done">未完成 <span class="optional">一行一条</span></label>
            <textarea
              id="delivery-not-done"
              v-model="delivery.notDone"
              rows="3"
              :disabled="running"
            />
          </div>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="delivery-modules">改动模块 <span class="optional">一行一条</span></label>
            <textarea
              id="delivery-modules"
              v-model="delivery.changedModules"
              rows="2"
              :disabled="running"
            />
          </div>
          <div class="field">
            <label for="delivery-interfaces">接口变化 <span class="optional">一行一条</span></label>
            <textarea
              id="delivery-interfaces"
              v-model="delivery.interfaceChanges"
              rows="2"
              :disabled="running"
            />
          </div>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="delivery-commands">运行命令 <span class="optional">一行一条</span></label>
            <textarea
              id="delivery-commands"
              v-model="delivery.runCommands"
              rows="2"
              :disabled="running"
            />
          </div>
          <div class="field">
            <label for="delivery-commit">提交号</label>
            <input id="delivery-commit" v-model="delivery.commit" type="text" :disabled="running" />
          </div>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="delivery-deps">依赖 <span class="optional">一行一条</span></label>
            <textarea
              id="delivery-deps"
              v-model="delivery.dependencies"
              rows="2"
              :disabled="running"
            />
          </div>
          <div class="field">
            <label for="delivery-mocks">模拟依赖 <span class="optional">一行一条</span></label>
            <textarea id="delivery-mocks" v-model="delivery.mocks" rows="2" :disabled="running" />
          </div>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="delivery-data">测试数据</label>
            <input id="delivery-data" v-model="delivery.testData" type="text" :disabled="running" />
          </div>
          <div class="field">
            <label for="delivery-evidence">自测证据 <span class="optional">一行一条</span></label>
            <textarea
              id="delivery-evidence"
              v-model="delivery.selfTestEvidence"
              rows="2"
              :disabled="running"
            />
          </div>
        </div>
        <p v-if="deliveryError" class="field-hint">{{ deliveryError }}</p>
        <div class="form-actions">
          <button
            class="btn btn-sm"
            type="button"
            :disabled="running || busyDelivery"
            @click="draftDelivery"
          >
            起草交付说明
          </button>
          <button
            class="btn btn-primary"
            type="submit"
            :disabled="running || busyDelivery"
            :aria-busy="busyDelivery"
          >
            {{ busyDelivery ? "正在保存…" : "保存交付说明" }}
          </button>
        </div>
      </form>
    </section>

    <section class="section">
      <div class="section-head">
        <h2 class="section-title-lg">检查模板</h2>
        <span class="section-note">不进入必测分母，不改写验收项</span>
      </div>
      <div class="template-grid">
        <div v-for="template in BUILTIN_TEMPLATES" :key="template.id" class="template-card">
          <div>
            <strong>{{ template.name }}</strong>
            <span class="muted"> v{{ template.version }} · {{ template.items.length }} 条</span>
          </div>
          <button
            class="btn btn-sm"
            type="button"
            :disabled="running || busyTemplate"
            @click="applyTemplate(template.id)"
          >
            应用
          </button>
        </div>
      </div>
      <div v-if="task.templates.length" class="stack template-results">
        <div v-for="template in task.templates" :key="template.templateId">
          <p class="doc-p">
            <strong>{{ template.name }} v{{ template.version }}</strong> · 适用
            {{ template.summary.applicable }} · 不适用 {{ template.summary.not_applicable }} ·
            不支持 {{ template.summary.unsupported }} · 无法判断 {{ template.summary.unknown }}
          </p>
          <ul class="list-plain">
            <li v-for="item in template.items" :key="item.id">
              {{ LAYER_LABEL[item.layer] }} · {{ item.objective }} ·
              {{ TEMPLATE_DISPOSITION_LABEL[item.disposition] }} · {{ item.reason }}
            </li>
          </ul>
        </div>
      </div>
      <p v-else class="doc-note">还没有应用检查模板。</p>
    </section>
  </div>
</template>
