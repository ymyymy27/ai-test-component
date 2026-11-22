<script setup lang="ts">
import { computed, ref } from "vue";

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
const buttons = ref<HTMLButtonElement[]>([]);
const activeIndex = ref(0);

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

function move(event: KeyboardEvent, index: number): void {
  const delta =
    event.key === "ArrowRight" || event.key === "ArrowDown"
      ? 1
      : event.key === "ArrowLeft" || event.key === "ArrowUp"
        ? -1
        : 0;
  if (!delta) {
    return;
  }
  event.preventDefault();
  const next = (index + delta + props.items.length) % props.items.length;
  activeIndex.value = next;
  buttons.value[next]?.focus();
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
  <div v-if="items.length > 1" class="coverage">
    <div class="ruler" role="group" aria-label="验收项覆盖">
      <button
        v-for="(item, index) in items"
        :key="item.itemId"
        ref="buttons"
        class="tick"
        :class="`is-${statusFor(item.itemId)}`"
        :data-no="item.no"
        type="button"
        :tabindex="index === activeIndex ? 0 : -1"
        :title="`第 ${item.no} 项 · ${labelFor(statusFor(item.itemId))}`"
        :aria-label="`第 ${item.no} 项，${item.text}，${labelFor(statusFor(item.itemId))}`"
        @click="emit('select', item.itemId)"
        @keydown="move($event, index)"
      ></button>
    </div>
    <div class="legend" aria-hidden="true">
      <template v-if="planned">
        <span class="legend-item"><StatusMark result="not_run" />待执行</span>
        <span class="legend-item"><StatusMark result="unselected" />未纳入</span>
      </template>
      <template v-else>
        <span class="legend-item"><StatusMark result="passed" />通过</span>
        <span class="legend-item"><StatusMark result="failed" />失败</span>
        <span class="legend-item"><StatusMark result="blocked" />阻塞</span>
        <span class="legend-item"><StatusMark result="not_run" />未运行</span>
        <span class="legend-item"><StatusMark result="unselected" />未纳入</span>
      </template>
    </div>
  </div>
</template>
