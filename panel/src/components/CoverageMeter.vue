<script setup lang="ts">
import { computed } from "vue";

import UncoveredList from "./UncoveredList.vue";

const props = withDefaults(
  defineProps<{
    selected: number;
    applicable: number;
    size?: "md" | "sm";
    /** Project-level checks (for example a smoke run) have no acceptance items. */
    caseTotal?: number;
    casePassed?: number;
    /** "id：text" rows from the backend; empty means the whole applicable set was selected. */
    uncoveredItems?: string[];
  }>(),
  { size: "md", caseTotal: 0, casePassed: 0, uncoveredItems: () => [] },
);

const projectMode = computed(() => props.applicable <= 0 && props.caseTotal > 0);
const ratio = computed(() => {
  if (projectMode.value) {
    return Math.min(1, Math.max(0, props.casePassed / props.caseTotal));
  }
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
      <span class="coverage-label">{{ projectMode ? "项目检查" : "范围覆盖" }}</span>
      <span v-if="projectMode" class="coverage-value numeric">
        <strong>{{ casePassed }}</strong><span class="slash">/</span>{{ caseTotal }} 通过
      </span>
      <span v-else class="coverage-value numeric">
        <strong>{{ selected }}</strong><span class="slash">/</span>{{ applicable }}
      </span>
    </div>
    <div
      class="track"
      role="progressbar"
      :aria-valuenow="projectMode ? casePassed : selected"
      :aria-valuemin="0"
      :aria-valuemax="projectMode ? caseTotal : applicable"
      :aria-label="
        projectMode
          ? `项目检查 ${casePassed} / ${caseTotal} 通过`
          : `范围覆盖 ${selected} / ${applicable}`
      "
    >
      <span class="fill" :style="{ width: `${percent}%` }"></span>
    </div>
    <p class="coverage-note">
      <template v-if="projectMode">
        项目级检查不绑定验收项，结果只说明这些检查是否通过。
      </template>
      <template v-else-if="applicable === 0">没有适用的验收项。</template>
      <template v-else-if="uncovered > 0">
        还有 {{ uncovered }} 条验收项未纳入本次范围，结论只对所选范围成立。
      </template>
      <template v-else>所选范围已覆盖全部适用验收项。</template>
    </p>
    <UncoveredList v-if="size === 'md'" :items="uncoveredItems" />
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
