<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string;
    text?: string;
    retryLabel?: string;
    retryBusy?: boolean;
    hideRetry?: boolean;
  }>(),
  {
    title: "加载失败",
    text: "请重试；如果问题持续出现，检查本地存储或宿主连接。",
    retryLabel: "重试",
    retryBusy: false,
    hideRetry: false,
  },
);

const emit = defineEmits<{ retry: [] }>();
</script>

<template>
  <div class="error-state" role="alert">
    <h2 class="error-title">{{ title }}</h2>
    <p class="error-text">{{ text }}</p>
    <div class="empty-actions">
      <button
        v-if="!hideRetry"
        class="btn btn-sm"
        type="button"
        :disabled="retryBusy"
        :aria-busy="retryBusy"
        @click="emit('retry')"
      >
        {{ retryLabel }}
      </button>
      <slot />
    </div>
  </div>
</template>
