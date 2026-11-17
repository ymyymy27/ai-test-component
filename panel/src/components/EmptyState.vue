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
  max-width: 680px;
  margin: var(--space-6) auto 0;
  padding: var(--space-5) 0;
  background: none;
  border: 0;
  border-top: 1px solid var(--line-strong);
  border-bottom: 1px solid var(--line-strong);
  border-radius: 0;
}

.empty-icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 0;
  border: 1px solid var(--line-strong);
  background: none;
  color: var(--accent);
}

.empty-copy {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.empty-copy h2 {
  font-size: var(--fs-20);
  letter-spacing: -0.01em;
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

@media (max-width: 520px) {
  .empty {
    margin: var(--space-5) var(--space-3) 0;
    padding: var(--space-4) 0;
    gap: var(--space-3);
  }
}
</style>
