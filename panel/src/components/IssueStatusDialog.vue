<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import { ISSUE_STATUS_LABEL } from "@/domain/labels";
import type { Issue, IssueDisposition, IssueStatus } from "@/domain/types";

import Icon from "./Icon.vue";

const props = defineProps<{
  issue: Issue | null;
  busy: boolean;
}>();

const emit = defineEmits<{
  save: [input: { status: IssueStatus; reason: string; disposition: IssueDisposition | null }];
}>();

const open = defineModel<boolean>({ default: false });
const dialogRef = ref<HTMLDialogElement | null>(null);
const status = ref<IssueStatus>("in_progress");
const reason = ref("");
const disposition = ref<IssueDisposition | null>(null);
const error = ref("");

const options = computed<IssueStatus[]>(() => {
  const current = props.issue?.status;
  if (current === "pending") return ["in_progress"];
  if (current === "in_progress") return ["retest", "pending"];
  if (current === "retest") return ["in_progress"];
  if (current === "closed") return ["in_progress"];
  return [];
});

watch(open, async (value) => {
  const dialog = dialogRef.value;
  if (!dialog) {
    return;
  }
  if (value) {
    status.value = options.value[0] ?? "in_progress";
    reason.value = "";
    disposition.value = status.value === "retest" ? "fixed" : null;
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
  if (!props.issue) {
    return;
  }
  if (status.value !== "pending" && !reason.value.trim()) {
    error.value = "这个状态变化需要写理由。";
    return;
  }
  error.value = "";
  emit("save", {
    status: status.value,
    reason: reason.value.trim(),
    disposition: disposition.value,
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
    aria-labelledby="issue-status-title"
    @cancel.prevent="close"
    @close="open = false"
    @click="onDialogClick"
  >
    <form class="dialog-form" novalidate @submit.prevent="submit">
      <header class="dialog-head">
        <h2 id="issue-status-title" class="dialog-title">更新问题状态</h2>
        <button class="icon-btn" type="button" aria-label="关闭" @click="close">
          <Icon name="close" :size="16" />
        </button>
      </header>
      <p class="dialog-text">
        当前状态：{{ issue ? ISSUE_STATUS_LABEL[issue.status] : "—" }}。关闭问题仍要绑定一次新运行。
      </p>
      <div class="field">
        <label for="issue-next-status">目标状态</label>
        <select id="issue-next-status" v-model="status">
          <option v-for="option in options" :key="option" :value="option">
            {{ ISSUE_STATUS_LABEL[option] }}
          </option>
        </select>
      </div>
      <div v-if="status === 'retest' || issue?.status === 'closed'" class="field">
        <label for="issue-disposition">处置</label>
        <select id="issue-disposition" v-model="disposition">
          <option :value="null">不填写</option>
          <option value="fixed">已修复</option>
          <option value="non_defect">非缺陷</option>
          <option value="duplicate">重复</option>
          <option value="deferred">延期</option>
        </select>
      </div>
      <div class="field" :class="{ 'has-error': error }">
        <label for="issue-reason"
          >理由 <span v-if="status !== 'pending'" class="optional">必填</span></label
        >
        <textarea
          id="issue-reason"
          v-model="reason"
          rows="3"
          :aria-describedby="error ? 'issue-status-error' : undefined"
          @input="error = ''"
        />
        <p v-if="error" id="issue-status-error" class="field-error" role="alert">{{ error }}</p>
      </div>
      <footer class="dialog-actions">
        <button class="btn" type="button" @click="close">取消</button>
        <button class="btn btn-primary" type="submit" :disabled="busy" :aria-busy="busy">
          {{ busy ? "正在保存…" : "保存状态" }}
        </button>
      </footer>
    </form>
  </dialog>
</template>
