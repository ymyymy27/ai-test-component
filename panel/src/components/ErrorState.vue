<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string;
    text?: string;
    retryLabel?: string;
  }>(),
  {
    title: "加载失败",
    text: "请重试；如果问题持续出现，检查本地存储或宿主连接。",
    retryLabel: "重试",
  },
);

const emit = defineEmits<{ retry: [] }>();
</script>

<template>
  <div class="error-state" role="alert">
    <p class="error-title">{{ title }}</p>
    <p class="error-text">{{ text }}</p>
    <div class="empty-actions">
      <button class="btn btn-sm" type="button" @click="emit('retry')">{{ retryLabel }}</button>
      <slot />
    </div>
  </div>
</template>
