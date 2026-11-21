<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import { parseLineList } from "@/domain/validation";
import type { Issue, Task } from "@/domain/types";
import type { IssueCreateInput } from "@/repositories/ProjectRepository";

const props = defineProps<{
  tasks: Task[];
  initialTaskId: string;
  busy: boolean;
}>();

const emit = defineEmits<{ save: [input: IssueCreateInput] }>();
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

const selectedTask = computed(() => props.tasks.find((task) => task.id === taskId.value) ?? null);
const items = computed(() => selectedTask.value?.acceptanceItems ?? []);

watch(open, async (value) => {
  const dialog = dialogRef.value;
  if (!dialog) {
    return;
  }
  if (value) {
    taskId.value = props.initialTaskId || props.tasks[0]?.id || "";
    itemId.value = "";
    title.value = "";
    expected.value = "";
    actual.value = "";
    steps.value = "";
    severity.value = "P2";
    error.value = "";
    await nextTick();
    if (!dialog.open) {
      dialog.showModal();
    }
  } else if (dialog.open) {
    dialog.close();
  }
});

function close(): void {
  open.value = false;
}

function submit(): void {
  if (!title.value.trim()) {
    error.value = "填写问题标题。";
    return;
  }
  if (!actual.value.trim() || !expected.value.trim()) {
    error.value = "填写实际与预期。";
    return;
  }
  if (!taskId.value) {
    error.value = "选择所属任务。";
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
    @cancel.prevent="close"
    @close="open = false"
    @click="onDialogClick"
  >
    <form class="dialog-form" novalidate @submit.prevent="submit">
      <header class="dialog-head">
        <h2 id="issue-create-title" class="dialog-title">登记问题</h2>
        <button class="icon-btn" type="button" aria-label="关闭" @click="close">
          <svg
            viewBox="0 0 16 16"
            width="15"
            height="15"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
          >
            <path d="M4 4l8 8M12 4l-8 8" stroke-linecap="round" />
          </svg>
        </button>
      </header>
      <p class="dialog-text">保存后进入“待确认”；关闭仍需一次包含该用例的新通过运行。</p>
      <div class="field" :class="{ 'has-error': error }">
        <label for="issue-title">标题</label>
        <input
          id="issue-title"
          v-model="title"
          type="text"
          maxlength="80"
          :aria-describedby="error ? 'issue-error' : undefined"
          @input="error = ''"
        />
      </div>
      <div class="form-grid">
        <div class="field">
          <label for="issue-task">所属任务</label>
          <select id="issue-task" v-model="taskId" @change="itemId = ''">
            <option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.title }}</option>
          </select>
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
      <div class="field">
        <label for="issue-actual">实际</label>
        <textarea
          id="issue-actual"
          v-model="actual"
          rows="2"
          placeholder="实际发生了什么"
          @input="error = ''"
        />
      </div>
      <div class="field">
        <label for="issue-expected">预期</label>
        <textarea
          id="issue-expected"
          v-model="expected"
          rows="2"
          placeholder="应该发生什么"
          @input="error = ''"
        />
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
          <option value="P1">P1 · 影响主流程</option>
          <option value="P2">P2 · 影响局部</option>
          <option value="P3">P3 · 待观察</option>
        </select>
      </div>
      <p v-if="error" id="issue-error" class="field-error" role="alert">{{ error }}</p>
      <footer class="dialog-actions">
        <button class="btn" type="button" @click="close">取消</button>
        <button class="btn btn-primary" type="submit" :disabled="busy" :aria-busy="busy">
          {{ busy ? "正在保存…" : "保存问题" }}
        </button>
      </footer>
    </form>
  </dialog>
</template>
