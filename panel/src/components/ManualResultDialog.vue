<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

import Icon from "./Icon.vue";

import type { RunCase } from "@/domain/types";
import type { EvidenceInput } from "@/repositories/ProjectRepository";
import {
  assertMediaBudget,
  fileToEvidence,
  type EvidenceFileLimits,
} from "@/services/evidenceFiles";

const props = defineProps<{
  testCase: RunCase | null;
  busy: boolean;
  allowFiles: boolean;
  limits?: EvidenceFileLimits;
}>();

const emit = defineEmits<{
  save: [input: { result: "passed" | "failed"; note: string; evidence: EvidenceInput[] }];
}>();

const open = defineModel<boolean>({ default: false });
const dialogRef = ref<HTMLDialogElement | null>(null);
const result = ref<"" | "passed" | "failed">("");
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
    result.value = "";
    note.value = "";
    files.value = [];
    error.value = "";
    await nextTick();
    if (!dialog.open) {
      dialog.showModal();
    }
    dialog.querySelector<HTMLElement>('input[name="manual-result"]')?.focus();
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
  if (!props.testCase) {
    return;
  }
  if (!result.value) {
    error.value = "先选择通过或失败。";
    return;
  }
  if (files.value.length > 0 && note.value.trim() === "") {
    error.value = "附证据时写一句说明，方便复查。";
    return;
  }
  hashing.value = true;
  try {
    assertMediaBudget(files.value, props.limits ?? {});
    const evidence = await Promise.all(
      files.value.map((file) =>
        fileToEvidence(file, note.value.trim(), props.testCase?.id ?? "", props.limits ?? {}),
      ),
    );
    emit("save", {
      result: result.value as "passed" | "failed",
      note: note.value.trim(),
      evidence,
    });
  } catch (cause) {
    error.value =
      cause instanceof Error && cause.message
        ? cause.message
        : "读取证据文件失败，换一个文件或稍后重试。";
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
    aria-labelledby="manual-dialog-title"
    aria-describedby="manual-dialog-desc"
    @cancel.prevent="close"
    @close="open = false"
    @click="onDialogClick"
  >
    <form class="dialog-form" novalidate @submit.prevent="save">
      <header class="dialog-head">
        <h2 id="manual-dialog-title" class="dialog-title">记录人工结果</h2>
        <button class="icon-btn" type="button" aria-label="关闭" @click="close">
          <Icon name="close" :size="15" />
        </button>
      </header>
      <p id="manual-dialog-desc" class="dialog-text">{{ testCase?.title }}</p>
      <dl v-if="testCase" class="dialog-context">
        <div>
          <dt>预期</dt>
          <dd>{{ testCase.expected || "未记录" }}</dd>
        </div>
        <div>
          <dt>实际</dt>
          <dd>{{ testCase.actual || "未记录" }}</dd>
        </div>
        <div>
          <dt>已有证据</dt>
          <dd>{{ testCase.evidence.length }} 条</dd>
        </div>
      </dl>
      <fieldset class="field">
        <legend class="field-label">结果</legend>
        <label class="checkline">
          <input v-model="result" name="manual-result" type="radio" value="passed" />
          <span>通过</span>
        </label>
        <label class="checkline">
          <input v-model="result" name="manual-result" type="radio" value="failed" />
          <span>失败</span>
        </label>
      </fieldset>
      <div class="field" :class="{ 'has-error': error }">
        <label for="manual-note">说明 <span class="optional">附证据时必填</span></label>
        <textarea
          id="manual-note"
          v-model="note"
          rows="3"
          placeholder="实际观察、复现步骤或判定依据"
          :aria-describedby="error ? 'manual-error' : undefined"
          @input="error = ''"
        />
        <p v-if="error" id="manual-error" class="field-error" role="alert">{{ error }}</p>
      </div>
      <div v-if="allowFiles" class="field">
        <span class="field-label">证据文件 <span class="optional">选填</span></span>
        <input
          id="manual-files"
          class="visually-hidden"
          type="file"
          multiple
          @change="onFiles"
        />
        <label class="btn btn-sm file-picker" for="manual-files">选择文件</label>
        <p v-if="files.length" class="field-hint">
          已选 {{ files.length }} 个：{{ files.map((file) => file.name).join("、") }}；读取文件计算摘要并登记元数据，不保存文件内容。
        </p>
      </div>
      <p v-else class="field-hint">
        宿主模式的证据需要绝对文件路径；请在 CLI 执行 evidence.add，或在证据列表中使用已有引用。
      </p>
      <footer class="dialog-actions">
        <button class="btn" type="button" @click="close">取消</button>
        <button
          class="btn btn-primary"
          type="submit"
          :disabled="busy || hashing || !result"
          :aria-busy="busy || hashing"
        >
          {{ hashing ? "正在读取证据…" : busy ? "正在保存…" : "保存人工结果" }}
        </button>
      </footer>
    </form>
  </dialog>
</template>
