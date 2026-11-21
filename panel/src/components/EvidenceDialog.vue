<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

import type { RunCase } from "@/domain/types";
import type { EvidenceInput } from "@/repositories/ProjectRepository";
import { fileToEvidence } from "@/services/evidenceFiles";

const props = defineProps<{
  cases: RunCase[];
  defaultCaseId: string;
  busy: boolean;
}>();

const emit = defineEmits<{
  save: [evidence: EvidenceInput[]];
}>();

const open = defineModel<boolean>({ default: false });
const dialogRef = ref<HTMLDialogElement | null>(null);
const caseId = ref("");
const note = ref("");
const files = ref<File[]>([]);
const error = ref("");
const hashing = ref(false);

watch(open, async (value) => {
  const dialog = dialogRef.value;
  if (!dialog) {
    return;
  }
  if (value) {
    caseId.value = props.defaultCaseId;
    note.value = "";
    files.value = [];
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

function onFiles(event: Event): void {
  const target = event.target;
  if (target instanceof HTMLInputElement) {
    files.value = target.files ? [...target.files] : [];
  }
}

async function save(): Promise<void> {
  if (files.value.length === 0) {
    error.value = "先选择至少一个证据文件。";
    return;
  }
  hashing.value = true;
  try {
    const evidence = await Promise.all(
      files.value.map((file) => fileToEvidence(file, note.value.trim(), caseId.value)),
    );
    emit("save", evidence);
  } catch {
    error.value = "读取证据文件失败，换一个文件或稍后重试。";
  } finally {
    hashing.value = false;
  }
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
    aria-labelledby="evidence-dialog-title"
    @cancel.prevent="close"
    @close="open = false"
    @click="onDialogClick"
  >
    <form class="dialog-form" novalidate @submit.prevent="save">
      <header class="dialog-head">
        <h2 id="evidence-dialog-title" class="dialog-title">补充证据</h2>
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
      <p class="dialog-text">只登记文件名、大小和摘要，不读取文件正文；证据不会改变本次结论。</p>
      <div class="field">
        <label for="evidence-case">关联用例 <span class="optional">选填</span></label>
        <select id="evidence-case" v-model="caseId">
          <option value="">不关联具体用例</option>
          <option v-for="testCase in cases" :key="testCase.id" :value="testCase.id">
            {{ testCase.title }}
          </option>
        </select>
      </div>
      <div class="field">
        <label for="evidence-note">说明 <span class="optional">选填</span></label>
        <input id="evidence-note" v-model="note" type="text" maxlength="120" />
      </div>
      <div class="field" :class="{ 'has-error': error }">
        <span class="field-label">证据文件</span>
        <input
          id="evidence-files"
          class="visually-hidden"
          type="file"
          multiple
          @change="onFiles"
        />
        <label class="btn btn-sm file-picker" for="evidence-files">选择文件</label>
        <p v-if="files.length" class="field-hint">
          已选 {{ files.length }} 个：{{ files.map((file) => file.name).join("、") }}
        </p>
        <p v-else class="field-hint">还没有选择文件。</p>
        <p v-if="error" class="field-error" role="alert">{{ error }}</p>
      </div>
      <footer class="dialog-actions">
        <button class="btn" type="button" @click="close">取消</button>
        <button
          class="btn btn-primary"
          type="submit"
          :disabled="busy || hashing"
          :aria-busy="busy || hashing"
        >
          {{ hashing ? "正在读取…" : busy ? "正在保存…" : "登记证据" }}
        </button>
      </footer>
    </form>
  </dialog>
</template>
