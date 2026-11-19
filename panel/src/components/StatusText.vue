<script setup lang="ts">
import { computed } from "vue";

import { RESULT_LABEL } from "@/domain/labels";
import type { CaseResult } from "@/domain/types";

const props = withDefaults(
  defineProps<{
    result: CaseResult | "unselected";
    label?: string;
  }>(),
  { label: "" },
);

const text = computed(() => {
  if (props.label) {
    return props.label;
  }
  return props.result === "unselected" ? "未纳入" : RESULT_LABEL[props.result];
});
</script>

<template>
  <span class="status-text" :class="`is-${result}`">{{ text }}</span>
</template>
