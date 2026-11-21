<script setup lang="ts">
withDefaults(
  defineProps<{
    text?: string;
    lines?: number;
  }>(),
  { text: "正在加载…", lines: 0 },
);
</script>

<template>
  <div class="loading-state" role="status" aria-live="polite" aria-busy="true">
    <span class="spinner" aria-hidden="true"></span>
    <span>{{ text }}</span>
    <span v-if="lines > 0" class="loading-lines" aria-hidden="true">
      <span v-for="line in lines" :key="line" class="loading-line"></span>
    </span>
  </div>
</template>
