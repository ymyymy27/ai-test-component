<script setup lang="ts">
import type { FriendlyError } from "@/api/errors";
import AppButton from "./AppButton.vue";
import Icon from "./Icon.vue";

withDefaults(
  defineProps<{
    error: FriendlyError;
    retryLabel?: string;
    busy?: boolean;
    showRetry?: boolean;
  }>(),
  { retryLabel: "重试", busy: false, showRetry: true },
);

defineEmits<{ retry: [] }>();
</script>

<template>
  <div class="notice" role="alert">
    <span class="notice-icon" aria-hidden="true"><Icon name="alert" :size="16" /></span>
    <div class="notice-copy">
      <p class="notice-title">{{ error.title }}</p>
      <p v-if="error.detail" class="notice-detail">{{ error.detail }}</p>
      <p class="notice-next">下一步：{{ error.next }}</p>
    </div>
    <AppButton
      v-if="showRetry"
      size="sm"
      variant="secondary"
      :loading="busy"
      @click="$emit('retry')"
    >
      {{ retryLabel }}
    </AppButton>
  </div>
</template>

<style scoped>
.notice {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--failed-fill);
  border: 1px solid var(--failed-border);
  border-radius: var(--r-control);
}

.notice-icon {
  color: var(--failed);
  margin-top: 2px;
}

.notice-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1 1 auto;
  min-width: 0;
}

.notice-title {
  font-size: var(--fs-13);
  font-weight: 600;
  color: var(--text);
}

.notice-detail {
  font-size: var(--fs-12);
  color: var(--text-2);
  word-break: break-word;
}

.notice-next {
  font-size: var(--fs-12);
  color: var(--text-2);
}
</style>
