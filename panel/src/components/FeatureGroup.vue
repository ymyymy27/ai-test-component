<script setup lang="ts">
import { computed, ref, watchEffect } from "vue";

import type { AcceptanceItem } from "@/domain/types";
import Icon from "./Icon.vue";

const props = defineProps<{
  feature: { name: string; items: AcceptanceItem[]; modules: string[] };
  selectedIds: string[];
  filtered?: boolean;
}>();

const emit = defineEmits<{
  toggleItem: [itemId: string];
  toggleFeature: [itemIds: string[], selected: boolean];
}>();

const featureCheckbox = ref<HTMLInputElement | null>(null);
const selectedSet = computed(() => new Set(props.selectedIds));
const selectedCount = computed(
  () => props.feature.items.filter((item) => selectedSet.value.has(item.id)).length,
);
const allSelected = computed(
  () => selectedCount.value === props.feature.items.length && props.feature.items.length > 0,
);
const someSelected = computed(() => selectedCount.value > 0 && !allSelected.value);

watchEffect(() => {
  if (featureCheckbox.value) {
    featureCheckbox.value.indeterminate = someSelected.value;
  }
});

function toggleFeature(): void {
  emit(
    "toggleFeature",
    props.feature.items.map((item) => item.id),
    !allSelected.value,
  );
}
</script>

<template>
  <details class="scope-group" open>
    <summary>
      <span class="scope-group-title">
        {{ feature.name }}
        <span class="scope-group-modules">{{ feature.modules.join(" · ") }}</span>
      </span>
      <span class="scope-group-count">
        {{ selectedCount }}/{{ feature.items.length }} 项<template v-if="filtered">（本筛选）</template>
      </span>
      <Icon name="chevron-down" :size="14" class="ledger-chevron" />
    </summary>
    <div class="scope-items">
      <label class="scope-group-select checkline">
        <input
          ref="featureCheckbox"
          type="checkbox"
          :checked="allSelected"
          :aria-label="filtered ? `选择本筛选中的 ${feature.name}` : `选择业务功能 ${feature.name}`"
          @change="toggleFeature"
        />
        <span>选择整个业务功能</span>
      </label>
      <label v-for="item in feature.items" :key="item.id" class="scope-item">
        <input
          type="checkbox"
          :checked="selectedSet.has(item.id)"
          @change="emit('toggleItem', item.id)"
        />
        <span class="ledger-index">{{ item.no }}</span>
        <span class="scope-item-text">{{ item.text }}</span>
        <span class="scope-item-module">{{ item.moduleName }}</span>
      </label>
    </div>
  </details>
</template>
