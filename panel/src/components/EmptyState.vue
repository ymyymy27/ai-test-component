<script setup lang="ts">
defineProps<{
  title: string;
  text?: string;
}>();
</script>

<template>
  <div class="empty-state">
    <h2 class="empty-title">{{ title }}</h2>
    <p v-if="text" class="empty-text">{{ text }}</p>
    <div v-if="$slots.default" class="empty-actions">
      <slot />
    </div>
  </div>
</template>
