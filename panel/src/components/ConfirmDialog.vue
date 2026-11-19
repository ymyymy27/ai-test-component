<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";

import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
const dialogRef = ref<HTMLDialogElement | null>(null);
const text = ref("");
const textError = ref("");
let lastFocus: HTMLElement | null = null;

watch(
  () => ui.confirmRequest,
  async (request) => {
    const dialog = dialogRef.value;
    if (!dialog) {
      return;
    }
    if (request) {
      text.value = "";
      textError.value = "";
      lastFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      await nextTick();
      if (!dialog.open) {
        dialog.showModal();
      }
      await nextTick();
      const target = dialog.querySelector<HTMLElement>("textarea, input, .dialog-confirm");
      target?.focus();
    } else if (dialog.open) {
      dialog.close();
    }
  },
);

function closeWith(value: string | null): void {
  ui.resolveConfirm(value);
}

function cancel(): void {
  closeWith(null);
}

function confirm(): void {
  const request = ui.confirmRequest;
  if (!request) {
    return;
  }
  if (request.requireText && !text.value.trim()) {
    textError.value = request.textLabel
      ? `填写${request.textLabel}后再继续。`
      : "填写理由后再继续。";
    return;
  }
  closeWith(request.requireText ? text.value.trim() : "");
}

function onClose(): void {
  if (ui.confirmRequest) {
    ui.resolveConfirm(null);
  }
  lastFocus?.focus();
}

function onDialogClick(event: MouseEvent): void {
  if (event.target === dialogRef.value) {
    cancel();
  }
}

onBeforeUnmount(() => {
  if (ui.confirmRequest) {
    ui.resolveConfirm(null);
  }
});
</script>

<template>
  <dialog
    ref="dialogRef"
    class="dialog"
    aria-labelledby="confirm-dialog-title"
    @cancel.prevent="cancel"
    @close="onClose"
    @click="onDialogClick"
  >
    <div v-if="ui.confirmRequest" class="dialog-form">
      <header class="dialog-head">
        <h2 id="confirm-dialog-title" class="dialog-title">{{ ui.confirmRequest.title }}</h2>
        <button class="icon-btn" type="button" aria-label="关闭" @click="cancel">
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
      <p class="dialog-text">{{ ui.confirmRequest.body }}</p>
      <div v-if="ui.confirmRequest.requireText" class="field" :class="{ 'has-error': textError }">
        <label for="confirm-dialog-text">{{ ui.confirmRequest.textLabel || "理由" }}</label>
        <textarea
          id="confirm-dialog-text"
          v-model="text"
          rows="3"
          :placeholder="ui.confirmRequest.placeholder"
          :aria-describedby="textError ? 'confirm-dialog-error' : undefined"
          :aria-invalid="textError ? 'true' : undefined"
        />
        <p v-if="textError" id="confirm-dialog-error" class="field-error" role="alert">
          {{ textError }}
        </p>
      </div>
      <footer class="dialog-actions">
        <button class="btn" type="button" @click="cancel">
          {{ ui.confirmRequest.cancelLabel || "取消" }}
        </button>
        <button
          class="btn dialog-confirm"
          :class="ui.confirmRequest.tone === 'danger' ? 'btn-danger' : 'btn-primary'"
          type="button"
          @click="confirm"
        >
          {{ ui.confirmRequest.confirmLabel || "确认" }}
        </button>
      </footer>
    </div>
  </dialog>
</template>
