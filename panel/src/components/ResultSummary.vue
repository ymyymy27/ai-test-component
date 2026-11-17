<script setup lang="ts">
import { computed } from "vue";

import { VERDICT_LABEL, VERDICT_TONE } from "@/api/labels";
import type { Verdict } from "@/api/types";
import UncoveredList from "./UncoveredList.vue";

const props = withDefaults(
  defineProps<{
    verdict: Verdict | null;
    statusLabel?: string;
    statusTone?: "passed" | "failed" | "incomplete" | "neutral" | "accent" | "running";
    timeLabel?: string;
    selected: number;
    applicable: number;
    projectMode?: boolean;
    projectPassed?: number;
    projectTotal?: number;
    caseTotal: number;
    casePassed: number;
    caseFailed: number;
    caseIncomplete: number;
    caseManual: number;
    pendingItems: number;
    assumptions: number;
    uncoveredItems?: string[];
    note?: string;
  }>(),
  {
    statusLabel: "",
    statusTone: "neutral",
    timeLabel: "",
    projectMode: false,
    projectPassed: 0,
    projectTotal: 0,
    uncoveredItems: () => [],
    note: "",
  },
);

const tone = computed(() =>
  props.verdict ? VERDICT_TONE[props.verdict] : "neutral",
);
const word = computed(() =>
  props.verdict ? VERDICT_LABEL[props.verdict] : "结论未生成",
);

const countsLine = computed(() => {
  if (props.projectMode) {
    return `${props.projectPassed} / ${props.projectTotal} 项项目检查通过`;
  }
  const parts = [`${props.caseTotal} 项检查`, `${props.casePassed} 项通过`];
  if (props.caseFailed > 0) {
    parts.push(`${props.caseFailed} 项失败`);
  }
  if (props.caseIncomplete > 0) {
    parts.push(`${props.caseIncomplete} 项未完成`);
  }
  if (props.caseManual > 0) {
    parts.push(`${props.caseManual} 项待确认`);
  }
  return parts.join(" · ");
});

const uncoveredCount = computed(() => props.uncoveredItems.length);

const gap = computed(() => {
  const parts: string[] = [];
  if (props.pendingItems > 0) {
    parts.push(`${props.pendingItems} 项没有结论`);
  }
  if (props.assumptions > 0) {
    parts.push(`${props.assumptions} 条推测未验证`);
  }
  if (uncoveredCount.value > 0) {
    parts.push(`${uncoveredCount.value} 项未测，不计入结论`);
  }
  return parts.join(" · ");
});
</script>

<template>
  <section class="summary" :class="`tone-${tone}`" data-testid="result-summary">
    <div class="summary-line">
      <h2 class="word" data-testid="verdict-word">{{ word }}</h2>
      <span v-if="statusLabel" class="run-status">{{ statusLabel }}</span>
      <span v-if="timeLabel" class="time">{{ timeLabel }}</span>
    </div>
    <p class="counts" data-testid="coverage">{{ countsLine }}</p>
    <p v-if="note" class="note">{{ note }}</p>
    <p v-if="gap" class="gap">{{ gap }}</p>
    <UncoveredList v-if="uncoveredItems.length" :items="uncoveredItems" />
  </section>
</template>

<style scoped>
.summary {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--space-3) 0 var(--space-4);
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.summary-line {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  min-width: 0;
}

.word {
  font-size: 26px;
  line-height: 1.2;
  letter-spacing: -0.02em;
  font-weight: 600;
}

.run-status,
.time {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.time {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

.counts {
  font-size: var(--fs-13);
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}

.note,
.gap {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.55;
}

.tone-passed .word {
  color: var(--passed);
}

.tone-failed .word {
  color: var(--failed);
}

.tone-incomplete .word {
  color: var(--incomplete);
}

.tone-neutral .word {
  color: var(--text-2);
  font-size: var(--fs-20);
}

@media (max-width: 640px) {
  .word {
    font-size: 23px;
  }

  .summary-line {
    flex-wrap: wrap;
  }

  .time {
    margin-left: 0;
  }
}
</style>
