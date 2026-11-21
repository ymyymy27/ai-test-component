<script setup lang="ts">
import { computed } from "vue";

import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
const isError = computed(() => ui.toast?.tone === "error");
</script>

<template>
  <div
    class="toast"
    :class="{
      'is-visible': ui.toastVisible,
      'is-error': isError,
      'is-success': ui.toast?.tone === 'success',
    }"
    :role="isError ? 'alert' : 'status'"
    :aria-live="isError ? 'assertive' : 'polite'"
  >
    <span class="toast-message">{{ ui.toast?.message ?? "" }}</span>
    <button
      v-if="ui.toastVisible"
      class="toast-close"
      type="button"
      aria-label="关闭提示"
      @click="ui.dismissToast()"
    >
      ×
    </button>
  </div>
</template>
