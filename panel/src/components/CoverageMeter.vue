<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    selected: number;
    applicable: number;
    size?: "md" | "sm";
  }>(),
  { size: "md" },
);

const ratio = computed(() => {
  if (props.applicable <= 0) {
    return 0;
  }
  return Math.min(1, Math.max(0, props.selected / props.applicable));
});

const percent = computed(() => Math.round(ratio.value * 100));
const uncovered = computed(() => Math.max(0, props.applicable - props.selected));
</script>

<template>
  <div class="coverage" :class="`coverage-${size}`" data-testid="coverage">
    <div class="coverage-head">
      <span class="coverage-label">范围覆盖</span>
      <span class="coverage-value numeric">
        <strong>{{ selected }}</strong><span class="slash">/</span>{{ applicable }}
      </span>
    </div>
    <div
      class="track"
      role="progressbar"
      :aria-valuenow="selected"
      :aria-valuemin="0"
      :aria-valuemax="applicable"
      :aria-label="`范围覆盖 ${selected} / ${applicable}`"
    >
      <span class="fill" :style="{ width: `${percent}%` }"></span>
    </div>
    <p class="coverage-note">
      <template v-if="applicable === 0">没有适用的验收项。</template>
      <template v-else-if="uncovered > 0">
        还有 {{ uncovered }} 条验收项未纳入本次范围，结论只对所选范围成立。
      </template>
      <template v-else>所选范围已覆盖全部适用验收项。</template>
    </p>
  </div>
</template>

<style scoped>
.coverage {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.coverage-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.coverage-label {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.coverage-value {
  font-size: var(--fs-13);
  color: var(--text-2);
}

.coverage-value strong {
  font-size: var(--fs-16);
  font-weight: 600;
  color: var(--text);
}

.slash {
  margin: 0 2px;
  color: var(--text-3);
}

.track {
  height: 4px;
  border-radius: 2px;
  background: var(--surface-sunken);
  overflow: hidden;
  border: 1px solid var(--line);
}

.fill {
  display: block;
  height: 100%;
  background: var(--accent-line);
  transition: width 220ms ease;
}

.coverage-note {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
}

.coverage-sm .coverage-value strong {
  font-size: var(--fs-14);
}

.coverage-sm .coverage-note {
  display: none;
}
</style>
