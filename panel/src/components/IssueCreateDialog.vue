<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import { parseLineList } from "@/domain/validation";
import type { Issue, Task } from "@/domain/types";
import type { IssueCreateInput } from "@/repositories/ProjectRepository";
import { useUiStore } from "@/stores/ui";

import Icon from "./Icon.vue";

const props = defineProps<{
  tasks: Task[];
  initialTaskId: string;
  busy: boolean;
}>();

const emit = defineEmits<{ save: [input: IssueCreateInput] }>();
const ui = useUiStore();
const open = defineModel<boolean>({ default: false });
const dialogRef = ref<HTMLDialogElement | null>(null);
const taskId = ref("");
const itemId = ref("");
const title = ref("");
const expected = ref("");
const actual = ref("");
const steps = ref("");
const severity = ref<Issue["severity"]>("P2");
const error = ref("");
const titleError = ref("");
const actualError = ref("");
const expectedError = ref("");
const taskError = ref("");

const selectedTask = computed(() => props.tasks.find((task) => task.id === taskId.value) ?? null);
const items = computed(() => selectedTask.value?.acceptanceItems ?? []);

watch(open, async (value) => {
  const dialog = dialogRef.value;
  if (!dialog) {
    return;
  }
  if (value) {
    taskId.value = props.initialTaskId || "";
    itemId.value = "";
    title.value = "";
    expected.value = "";
    actual.value = "";
    steps.value = "";
    severity.value = "P2";
    error.value = "";
    titleError.value = "";
    actualError.value = "";
    expectedError.value = "";
    taskError.value = "";
    await nextTick();
    if (!dialog.open) {
      dialog.showModal();
    }
    dialog.querySelector<HTMLElement>("#issue-title")?.focus();
  } else if (dialog.open) {
    dialog.close();
  }
});

async function close(): Promise<void> {
  if (props.busy) {
    return;
  }
  const dirty = Boolean(
    title.value.trim() ||
      actual.value.trim() ||
      expected.value.trim() ||
      steps.value.trim(),
  );
  if (dirty) {
    const answer = await ui.askConfirm({
      title: "放弃登记问题？",
      body: "已填写的内容会丢失。",
      confirmLabel: "放弃",
      tone: "danger",
    });
    if (answer === null) {
      return;
    }
  }
  open.value = false;
}

function submit(): void {
  titleError.value = title.value.trim() ? "" : "填写问题标题。";
  actualError.value = actual.value.trim() ? "" : "填写实际结果。";
  expectedError.value = expected.value.trim() ? "" : "填写预期结果。";
  taskError.value = taskId.value ? "" : "选择所属任务。";
  if (titleError.value || actualError.value || expectedError.value || taskError.value) {
    error.value = "补全标红的必填项后再保存。";
    const target = document.querySelector<HTMLElement>(
      titleError.value ? "#issue-title" : actualError.value ? "#issue-actual" : expectedError.value ? "#issue-expected" : "#issue-task",
    );
    target?.focus();
    return;
  }
  error.value = "";
  emit("save", {
    taskId: taskId.value,
    itemId: itemId.value,
    title: title.value,
    expected: expected.value,
    actual: actual.value,
    steps: parseLineList(steps.value),
    severity: severity.value,
  });
}

function onDialogClick(event: MouseEvent): void {
  if (event.target === dialogRef.value) {
    close();
  }
}
</script>

<template>
  <dialog
    ref="dialogRef"
    class="dialog"
    aria-labelledby="issue-create-title"
    aria-describedby="issue-create-desc"
    @cancel.prevent="close"
    @close="open = false"
    @click="onDialogClick"
  >
    <form class="dialog-form" novalidate @submit.prevent="submit">
      <header class="dialog-head">
        <h2 id="issue-create-title" class="dialog-title">登记问题</h2>
        <button
          class="icon-btn"
          type="button"
          aria-label="关闭"
          :disabled="busy"
          @click="close"
        >
          <Icon name="close" :size="16" />
        </button>
      </header>
      <p id="issue-create-desc" class="dialog-text">
        保存后进入“待确认”；关闭仍需一次包含该用例的新通过运行。
      </p>
      <div class="field" :class="{ 'has-error': titleError }">
        <label for="issue-title">标题 <span class="optional">必填</span></label>
        <input
          id="issue-title"
          v-model="title"
          type="text"
          maxlength="80"
          :aria-required="true"
          :aria-invalid="titleError ? 'true' : undefined"
          :aria-describedby="titleError ? 'issue-title-error' : undefined"
          @input="titleError = ''"
        />
        <p v-if="titleError" id="issue-title-error" class="field-error" role="alert">
          {{ titleError }}
        </p>
      </div>
      <div class="form-grid">
        <div class="field" :class="{ 'has-error': taskError }">
          <label for="issue-task">所属任务 <span class="optional">必填</span></label>
          <select
            id="issue-task"
            v-model="taskId"
            :aria-invalid="taskError ? 'true' : undefined"
            @change="itemId = ''"
          >
            <option value="">选择任务</option>
            <option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.title }}</option>
          </select>
          <p v-if="taskError" class="field-error" role="alert">{{ taskError }}</p>
        </div>
        <div class="field">
          <label for="issue-item">关联验收项 <span class="optional">选填</span></label>
          <select id="issue-item" v-model="itemId">
            <option value="">不关联验收项</option>
            <option v-for="item in items" :key="item.id" :value="item.id">
              {{ item.no }} {{ item.text }}
            </option>
          </select>
        </div>
      </div>
      <div class="field" :class="{ 'has-error': expectedError }">
        <label for="issue-expected">预期 <span class="optional">必填</span></label>
        <textarea
          id="issue-expected"
          v-model="expected"
          rows="2"
          placeholder="应该发生什么"
          :aria-required="true"
          :aria-invalid="expectedError ? 'true' : undefined"
          :aria-describedby="expectedError ? 'issue-expected-error' : undefined"
          @input="expectedError = ''"
        />
        <p v-if="expectedError" id="issue-expected-error" class="field-error" role="alert">
          {{ expectedError }}
        </p>
      </div>
      <div class="field" :class="{ 'has-error': actualError }">
        <label for="issue-actual">实际 <span class="optional">必填</span></label>
        <textarea
          id="issue-actual"
          v-model="actual"
          rows="2"
          placeholder="实际发生了什么"
          :aria-required="true"
          :aria-invalid="actualError ? 'true' : undefined"
          :aria-describedby="actualError ? 'issue-actual-error' : undefined"
          @input="actualError = ''"
        />
        <p v-if="actualError" id="issue-actual-error" class="field-error" role="alert">
          {{ actualError }}
        </p>
      </div>
      <div class="field">
        <label for="issue-steps">复现步骤 <span class="optional">一行一步</span></label>
        <textarea
          id="issue-steps"
          v-model="steps"
          rows="3"
          placeholder="打开同步批次&#10;删除一条记录&#10;重新读取批次"
        />
      </div>
      <div class="field">
        <label for="issue-severity">严重度</label>
        <select id="issue-severity" v-model="severity">
          <option value="P0">P0 · 阻断</option>
          <option value="P1">P1 · 影响主流程</option>
          <option value="P2">P2 · 影响局部</option>
          <option value="P3">P3 · 待观察</option>
        </select>
      </div>
      <p v-if="error" id="issue-error" class="field-error" role="alert">{{ error }}</p>
      <footer class="dialog-actions">
        <button class="btn" type="button" :disabled="busy" @click="close">取消</button>
        <button class="btn btn-primary" type="submit" :disabled="busy" :aria-busy="busy">
          {{ busy ? "正在保存…" : "保存问题" }}
        </button>
      </footer>
    </form>
  </dialog>
</template>
