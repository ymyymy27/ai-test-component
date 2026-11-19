<script setup lang="ts">
withDefaults(
  defineProps<{
    text?: string;
  }>(),
  { text: "正在加载…" },
);
</script>

<template>
  <div class="loading-state" role="status" aria-live="polite" aria-busy="true">
    <span class="spinner" aria-hidden="true"></span>
    <span>{{ text }}</span>
  </div>
</template>
