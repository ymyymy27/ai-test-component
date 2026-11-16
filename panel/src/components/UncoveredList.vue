<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    /** Already-rendered "id：text" rows from the backend; the panel does not rewrite them. */
    items: string[];
    limit?: number;
  }>(),
  { limit: 5 },
);

const shown = computed(() => props.items.slice(0, props.limit));
const hidden = computed(() => Math.max(0, props.items.length - props.limit));
</script>

<template>
  <div v-if="items.length" class="uncovered" data-testid="uncovered-list">
    <p class="uncovered-title">未覆盖（{{ items.length }}）</p>
    <ul class="uncovered-rows">
      <li v-for="item in shown" :key="item">{{ item }}</li>
      <li v-if="hidden > 0" class="more">还有 {{ hidden }} 条未显示</li>
    </ul>
  </div>
</template>

<style scoped>
.uncovered {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 6px;
  border-top: 1px dashed var(--line);
}

.uncovered-title {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.uncovered-rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.uncovered-rows li {
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.5;
  word-break: break-word;
}

.uncovered-rows .more {
  color: var(--text-3);
}
</style>
