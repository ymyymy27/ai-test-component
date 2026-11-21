<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
const isError = computed(() => ui.toast?.tone === "error");
const toastRef = ref<HTMLElement | null>(null);
const supportsPopover =
  typeof HTMLElement !== "undefined" && "showPopover" in HTMLElement.prototype;

function syncTopLayer(visible: boolean): void {
  const element = toastRef.value;
  if (!element || !supportsPopover) {
    return;
  }
  try {
    if (visible && !element.matches(":popover-open")) {
      element.showPopover();
    } else if (!visible && element.matches(":popover-open")) {
      element.hidePopover();
    }
  } catch {
    // 旧浏览器或重复调用时退化为普通定位元素。
  }
}

watch(
  () => ui.toastVisible,
  async (visible) => {
    await nextTick();
    syncTopLayer(visible);
  },
);

onMounted(() => {
  syncTopLayer(ui.toastVisible);
});

onBeforeUnmount(() => {
  syncTopLayer(false);
});

async function copyToast(): Promise<void> {
  const message = ui.toast?.message;
  if (!message) {
    return;
  }
  try {
    await navigator.clipboard.writeText(message);
    ui.showToast("提示已复制", "success");
  } catch {
    ui.showToast("复制失败，请手动选择文本", "error");
  }
}
</script>

<template>
  <div
    ref="toastRef"
    :popover="supportsPopover ? 'manual' : undefined"
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
      class="toast-copy"
      type="button"
      aria-label="复制提示"
      @click="copyToast()"
    >
      复制
    </button>
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
