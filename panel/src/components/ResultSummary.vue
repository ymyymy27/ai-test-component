<script setup lang="ts">
import { computed } from "vue";

import {
  VERDICT_LABEL,
  VERDICT_SENTENCE,
  VERDICT_TONE,
} from "@/api/labels";
import type { Verdict } from "@/api/types";
import Badge from "./Badge.vue";
import UncoveredList from "./UncoveredList.vue";

export type SummaryTickTone =
  | "passed"
  | "failed"
  | "incomplete"
  | "selected"
  | "unselected";

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
    ticks?: { tone: SummaryTickTone; label: string }[];
    uncoveredItems?: string[];
    note?: string;
    confirmation?: string;
  }>(),
  {
    statusLabel: "",
    statusTone: "neutral",
    timeLabel: "",
    projectMode: false,
    projectPassed: 0,
    projectTotal: 0,
    ticks: () => [],
    uncoveredItems: () => [],
    note: "",
    confirmation: "",
  },
);

const tone = computed(() =>
  props.verdict ? VERDICT_TONE[props.verdict] : "neutral",
);
const word = computed(() =>
  props.verdict ? VERDICT_LABEL[props.verdict] : "结论未生成",
);
const code = computed(() => {
  switch (props.verdict) {
    case "passed":
      return "PASS";
    case "failed":
      return "FAIL";
    case "incomplete":
      return "INCOMPLETE";
    case "not_applicable":
      return "N/A";
    default:
      return "PENDING";
  }
});
const sentence = computed(() =>
  props.verdict
    ? VERDICT_SENTENCE[props.verdict]
    : "运行还没有结束，结束后才会给出结论。",
);

const current = computed(() =>
  props.projectMode ? props.projectPassed : props.selected,
);
const total = computed(() =>
  props.projectMode ? props.projectTotal : props.applicable,
);

const tickCounts = computed(() => {
  const counts: Record<SummaryTickTone, number> = {
    passed: 0,
    failed: 0,
    incomplete: 0,
    selected: 0,
    unselected: 0,
  };
  for (const tick of props.ticks) {
    counts[tick.tone] += 1;
  }
  return counts;
});

const useBar = computed(() => props.ticks.length > 12);
const barSegments = computed(() => {
  if (!useBar.value || props.ticks.length === 0) {
    return [];
  }
  const order: SummaryTickTone[] = [
    "passed",
    "failed",
    "incomplete",
    "selected",
    "unselected",
  ];
  return order
    .map((tone) => ({
      tone,
      width: (tickCounts.value[tone] / props.ticks.length) * 100,
    }))
    .filter((segment) => segment.width > 0);
});

const caption = computed(() => {
  if (props.projectMode) {
    return `项目检查 ${props.projectPassed} / ${props.projectTotal} 通过`;
  }
  const parts = [
    `通过 ${props.casePassed}`,
    `失败 ${props.caseFailed}`,
    `未完成 ${props.caseIncomplete}`,
  ];
  if (props.caseManual > 0) {
    parts.push(`待人工 ${props.caseManual}`);
  }
  return `用例 ${props.caseTotal}：${parts.join(" · ")}`;
});

const uncoveredCount = computed(() =>
  props.projectMode
    ? props.uncoveredItems.length
    : Math.max(props.uncoveredItems.length, props.applicable - props.selected),
);

const gap = computed(() => {
  const parts: string[] = [];
  if (props.pendingItems > 0) {
    parts.push(`范围内未完成 ${props.pendingItems} 条验收项`);
  }
  if (props.caseManual > 0) {
    parts.push(`待人工 ${props.caseManual} 条`);
  }
  if (props.assumptions > 0) {
    parts.push(`AI 假设 ${props.assumptions} 条未验证`);
  }
  if (uncoveredCount.value > 0) {
    parts.push(`还有 ${uncoveredCount.value} 条验收项未纳入本次范围`);
  }
  return parts.join(" · ");
});
</script>

<template>
  <section class="summary" :class="`tone-${tone}`" data-testid="result-summary">
    <div class="summary-main">
      <div class="summary-status">
        <span v-if="statusLabel" class="status-line">
          <span class="status-label">运行状态</span>
          <Badge :tone="statusTone">{{ statusLabel }}</Badge>
        </span>
        <span v-if="timeLabel" class="time">{{ timeLabel }}</span>
      </div>
      <p class="verdict-code">{{ code }}</p>
      <h2 class="word" data-testid="verdict-word">{{ word }}</h2>
      <p class="sentence">{{ sentence }}</p>
      <p v-if="note" class="note">{{ note }}</p>
      <p v-if="confirmation" class="confirmation">{{ confirmation }}</p>
    </div>

    <div class="metrics" data-testid="coverage">
      <div class="metric-head">
        <span class="metric-label">{{ projectMode ? "项目检查" : "验收项范围" }}</span>
        <span class="ratio numeric">
          <strong>{{ current }}</strong><span class="slash">/</span>{{ total }}
        </span>
      </div>
      <div v-if="useBar" class="bar" :aria-label="`${current} / ${total}`">
        <span
          v-for="segment in barSegments"
          :key="segment.tone"
          class="bar-seg"
          :class="`tick-${segment.tone}`"
          :style="{ width: `${segment.width}%` }"
        ></span>
      </div>
      <div v-else-if="ticks.length" class="ticks" :aria-label="`${current} / ${total}`">
        <span
          v-for="(tick, index) in ticks"
          :key="index"
          class="tick"
          :class="`tick-${tick.tone}`"
          :title="tick.label"
        ></span>
      </div>
      <p class="metric-caption">{{ caption }}</p>
      <p v-if="gap" class="metric-gap">{{ gap }}</p>
      <p v-else class="metric-gap quiet">纳入的检查均已覆盖。</p>
      <UncoveredList v-if="uncoveredItems.length" :items="uncoveredItems" />
    </div>
  </section>
</template>

<style scoped>
.summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 264px;
  gap: var(--space-6);
  padding: var(--space-4) 0 var(--space-5);
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.summary-main {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.summary-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.status-line {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.status-label,
.time {
  font-size: var(--fs-11);
  color: var(--text-3);
}

.verdict-code {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.22em;
  color: var(--text-3);
}

.word {
  margin-top: 0;
  font-size: 44px;
  line-height: 1.02;
  letter-spacing: -0.03em;
  font-weight: 600;
}

.sentence {
  max-width: 52ch;
  font-size: var(--fs-13);
  color: var(--text-2);
  line-height: 1.6;
}

.note,
.confirmation {
  font-size: var(--fs-11);
  color: var(--text-3);
  line-height: 1.5;
}

.metrics {
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
  padding-left: var(--space-4);
  border-left: 1px solid var(--line);
}

.metric-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}

.metric-label {
  font-size: var(--fs-11);
  color: var(--text-3);
}

.ratio {
  font-size: var(--fs-13);
  color: var(--text-2);
}

.ratio strong {
  font-size: var(--fs-20);
  font-weight: 600;
  color: var(--text);
}

.slash {
  margin: 0 2px;
  color: var(--text-3);
}

.ticks {
  display: flex;
  gap: 3px;
  height: 12px;
}

.tick {
  flex: 1 1 0;
  min-width: 2px;
  border-radius: 1px;
  background: var(--line);
}

.bar {
  display: flex;
  height: 12px;
  overflow: hidden;
  border-radius: 2px;
  background: var(--line);
}

.bar-seg {
  display: block;
  height: 100%;
}

.tick-passed,
.bar-seg.tick-passed {
  background: var(--passed);
}

.tick-failed,
.bar-seg.tick-failed {
  background: var(--failed);
}

.tick-incomplete,
.bar-seg.tick-incomplete {
  background: var(--incomplete);
}

.tick-selected,
.bar-seg.tick-selected {
  background: var(--accent-line);
}

.tick-unselected,
.bar-seg.tick-unselected {
  background: var(--line);
}

.metric-caption {
  font-size: var(--fs-12);
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}

.metric-gap {
  font-size: var(--fs-11);
  color: var(--text-3);
  line-height: 1.5;
}

.metric-gap.quiet {
  color: var(--text-3);
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
  .summary {
    grid-template-columns: 1fr;
    gap: var(--space-4);
  }

  .metrics {
    padding-left: 0;
    border-left: 0;
    padding-top: var(--space-3);
    border-top: 1px solid var(--line);
  }

  .word {
    font-size: 34px;
  }
}
</style>
