<script setup lang="ts">
import Icon from "./Icon.vue";

withDefaults(
  defineProps<{
    title: string;
    icon?: "folder" | "history" | "file" | "info";
  }>(),
  { icon: "info" },
);
</script>

<template>
  <div class="empty">
    <span class="empty-icon" aria-hidden="true"><Icon :name="icon" :size="20" /></span>
    <div class="empty-copy">
      <h2>{{ title }}</h2>
      <div class="empty-body"><slot name="body" /></div>
      <div v-if="$slots.actions" class="empty-actions"><slot name="actions" /></div>
    </div>
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  gap: var(--space-4);
  padding: var(--space-5);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
}

.empty-icon {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex: none;
  border-radius: var(--r-control);
  border: 1px solid var(--line);
  background: var(--surface-raised);
  color: var(--text-2);
}

.empty-copy {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.empty-copy h2 {
  font-size: var(--fs-14);
}

.empty-body {
  color: var(--text-2);
  font-size: var(--fs-13);
  line-height: 1.6;
  max-width: 58ch;
}

.empty-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

@media (max-width: 420px) {
  .empty {
    padding: var(--space-4);
    gap: var(--space-3);
  }
}
</style>
