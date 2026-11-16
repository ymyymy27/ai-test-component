<script setup lang="ts">
import { computed } from "vue";

import {
  VERDICT_LABEL,
  VERDICT_SENTENCE,
  VERDICT_TONE,
} from "@/api/labels";
import type { Verdict } from "@/api/types";
import Badge from "./Badge.vue";
import CoverageMeter from "./CoverageMeter.vue";

const props = defineProps<{
  verdict: Verdict | null;
  selected: number;
  applicable: number;
  statusLabel?: string;
  statusTone?: "passed" | "failed" | "incomplete" | "neutral" | "accent" | "running";
  timeLabel?: string;
}>();

const tone = computed(() =>
  props.verdict ? VERDICT_TONE[props.verdict] : "neutral",
);
const word = computed(() =>
  props.verdict ? VERDICT_LABEL[props.verdict] : "结论未生成",
);
const sentence = computed(() =>
  props.verdict
    ? VERDICT_SENTENCE[props.verdict]
    : "运行还没有结束，结束后才会给出结论。",
);
</script>

<template>
  <section class="verdict" :class="`tone-${tone}`">
    <div class="head">
      <span class="head-left">
        <span v-if="statusLabel" class="status-label">运行状态</span>
        <Badge v-if="statusLabel" :tone="statusTone ?? 'neutral'">{{ statusLabel }}</Badge>
      </span>
      <span v-if="timeLabel" class="time">{{ timeLabel }}</span>
    </div>
    <h2 class="word" data-testid="verdict-word">{{ word }}</h2>
    <p class="sentence">{{ sentence }}</p>
    <CoverageMeter :selected="selected" :applicable="applicable" />
  </section>
</template>

<style scoped>
.verdict {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-4) var(--space-4);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.head-left {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.status-label {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.time {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.word {
  font-size: var(--fs-26);
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.2;
}

.sentence {
  font-size: var(--fs-13);
  color: var(--text-2);
  max-width: 60ch;
}

.tone-passed {
  background: linear-gradient(180deg, var(--passed-fill), transparent 120px), var(--surface);
  border-color: rgba(134, 179, 148, 0.28);
}

.tone-passed .word {
  color: var(--passed);
}

.tone-failed {
  background: linear-gradient(180deg, var(--failed-fill), transparent 120px), var(--surface);
  border-color: rgba(214, 141, 132, 0.3);
}

.tone-failed .word {
  color: var(--failed);
}

.tone-incomplete {
  background: linear-gradient(180deg, var(--incomplete-fill), transparent 120px), var(--surface);
  border-color: rgba(201, 168, 111, 0.28);
}

.tone-incomplete .word {
  color: var(--incomplete);
}

.tone-neutral .word {
  color: var(--text-2);
  font-size: var(--fs-20);
}
</style>
