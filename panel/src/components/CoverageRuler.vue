<script setup lang="ts">
import { computed } from "vue";

import { RESULT_LABEL } from "@/domain/labels";
import type { CaseResult } from "@/domain/types";
import StatusMark from "./StatusMark.vue";

const props = withDefaults(
  defineProps<{
    items: Array<{ itemId: string; no: string; text: string }>;
    selectedIds: string[];
    outcomes: Record<string, CaseResult>;
    planned?: boolean;
  }>(),
  { planned: false },
);

const emit = defineEmits<{ select: [itemId: string] }>();

const selectedSet = computed(() => new Set(props.selectedIds));

function statusFor(itemId: string): CaseResult | "unselected" {
  if (!selectedSet.value.has(itemId)) {
    return "unselected";
  }
  if (props.planned) {
    return "not_run";
  }
  return props.outcomes[itemId] ?? "not_run";
}

function labelFor(status: CaseResult | "unselected"): string {
  if (status === "unselected") {
    return "未纳入";
  }
  if (props.planned && status === "not_run") {
    return "待执行";
  }
  return RESULT_LABEL[status];
}
</script>

<template>
  <div v-if="items.length >= 3" class="coverage">
    <div class="ruler" role="list" aria-label="验收项覆盖">
      <button
        v-for="item in items"
        :key="item.itemId"
        class="tick"
        :class="`is-${statusFor(item.itemId)}`"
        type="button"
        role="listitem"
        :title="`第 ${item.no} 项 · ${labelFor(statusFor(item.itemId))}`"
        :aria-label="`第 ${item.no} 项，${item.text}，${labelFor(statusFor(item.itemId))}`"
        @click="emit('select', item.itemId)"
      ></button>
    </div>
    <div class="legend" aria-hidden="true">
      <span class="legend-item"><StatusMark result="passed" />通过</span>
      <span class="legend-item"><StatusMark result="failed" />失败</span>
      <span class="legend-item"><StatusMark result="blocked" />阻塞</span>
      <span class="legend-item"
        ><StatusMark result="not_run" />{{ planned ? "待执行" : "未运行" }}</span
      >
      <span class="legend-item"><StatusMark result="unselected" />未纳入</span>
    </div>
  </div>
</template>
