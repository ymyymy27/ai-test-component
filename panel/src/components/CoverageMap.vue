<script setup lang="ts">
export interface CoverageSegment {
  id: string;
  text: string;
  tone: "pass" | "fail" | "block" | "off" | "sel" | "wait" | "current";
  label: string;
}

import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    segments: CoverageSegment[];
    selected: number;
    applicable: number;
    compact?: boolean;
    phase?: "plan" | "running" | "result";
  }>(),
  { compact: false, phase: "result" },
);

const emit = defineEmits<{ select: [id: string] }>();

const counts = computed(() => {
  const rows = { pass: 0, fail: 0, block: 0, other: 0 };
  for (const segment of props.segments) {
    if (segment.tone === "pass") {
      rows.pass += 1;
    } else if (segment.tone === "fail") {
      rows.fail += 1;
    } else if (segment.tone === "block") {
      rows.block += 1;
    } else if (segment.tone === "sel" || segment.tone === "wait") {
      rows.other += 1;
    }
  }
  return rows;
});

const legend = computed(() => {
  const parts: string[] = [];
  if (counts.value.pass) {
    parts.push(`通过 ${counts.value.pass}`);
  }
  if (counts.value.fail) {
    parts.push(`失败 ${counts.value.fail}`);
  }
  if (counts.value.block) {
    parts.push(`阻塞 ${counts.value.block}`);
  }
  if (counts.value.other) {
    const label =
      props.phase === "plan" ? "已计划" : props.phase === "running" ? "等待中" : "未运行";
    parts.push(`${label} ${counts.value.other}`);
  }
  const excluded = Math.max(0, props.applicable - props.selected);
  if (excluded) {
    parts.push(`未纳入 ${excluded}`);
  }
  return parts.join(" · ");
});
</script>

<template>
  <div class="coverage">
    <div class="segments" role="list" aria-label="验收项覆盖">
      <button
        v-for="segment in segments"
        :key="segment.id"
        type="button"
        class="segment"
        :class="segment.tone"
        :title="`${segment.text} · ${segment.label}`"
        role="listitem"
        @click="emit('select', segment.id)"
      />
    </div>
    <div class="meta">
      <span>本次 <b>{{ selected }}</b> / 共 <b>{{ applicable }}</b> 项</span>
      <span v-if="!compact && legend" class="legend">{{ legend }}</span>
    </div>
  </div>
</template>

<style scoped>
.coverage {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.segments {
  display: flex;
  gap: 3px;
  height: 5px;
}

.segment {
  width: 24px;
  height: 5px;
  padding: 0;
  border: 0;
  background: var(--line-strong);
}

.segment.pass {
  background: var(--passed);
}

.segment.fail {
  background: var(--failed);
}

.segment.block {
  background: var(--incomplete);
}

.segment.wait {
  background: var(--accent);
  opacity: 0.5;
}

.segment.sel {
  background: var(--accent);
}

.segment.current {
  background: var(--accent);
  opacity: 0.42;
}

.segment.off {
  background: var(--line-strong);
}

.segment:hover {
  outline: 1px solid var(--text-2);
  outline-offset: 1px;
}

.meta {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  line-height: 1.4;
}

.meta b {
  color: var(--text-2);
  font-weight: 400;
}

@media (max-width: 520px) {
  .segment {
    width: 18px;
  }

  .meta {
    flex-wrap: wrap;
  }
}
</style>
