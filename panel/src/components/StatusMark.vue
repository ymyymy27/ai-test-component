<script setup lang="ts">
import type { CaseResult, Verdict } from "@/domain/types";

const props = defineProps<{
  result: CaseResult | Verdict | "unselected";
  label?: string;
}>();
</script>

<template>
  <span class="status-mark" :class="`is-${props.result}`" :aria-hidden="props.label ? undefined : 'true'">
    <svg
      viewBox="0 0 16 16"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      stroke-width="1.5"
    >
      <path
        v-if="props.result === 'passed'"
        d="M3.5 8.5l3 3 6-6"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <rect
        v-else-if="props.result === 'failed'"
        x="4"
        y="4"
        width="8"
        height="8"
        fill="currentColor"
        stroke="none"
      />
      <template v-else-if="props.result === 'blocked'">
        <rect x="3.5" y="3.5" width="9" height="9" />
        <path d="M4.5 11.5l7-7" />
      </template>
      <path v-else-if="props.result === 'error'" d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke-linecap="round" />
      <template v-else-if="props.result === 'manual_pending' || props.result === 'incomplete'">
        <circle cx="8" cy="8" r="4.5" />
        <path d="M8 3.5v9" />
      </template>
      <template v-else-if="props.result === 'unselected' || props.result === 'not_applicable'">
        <path d="M4.5 8h7" stroke-linecap="round" />
      </template>
      <circle v-else cx="8" cy="8" r="4" />
    </svg>
    <span v-if="props.label" class="visually-hidden">{{ props.label }}</span>
  </span>
</template>
