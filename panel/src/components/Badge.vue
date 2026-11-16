<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?:
      | "passed"
      | "failed"
      | "incomplete"
      | "neutral"
      | "accent"
      | "running";
  }>(),
  { tone: "neutral" },
);
</script>

<template>
  <span :class="['badge', `tone-${tone}`]"><slot /></span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 1px 7px;
  border-radius: var(--r-chip);
  border: 1px solid transparent;
  font-size: var(--fs-12);
  line-height: 1.6;
  font-weight: 500;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.tone-passed {
  color: var(--passed);
  background: var(--passed-fill);
  border-color: rgba(134, 179, 148, 0.25);
}

.tone-failed {
  color: var(--failed);
  background: var(--failed-fill);
  border-color: rgba(214, 141, 132, 0.28);
}

.tone-incomplete {
  color: var(--incomplete);
  background: var(--incomplete-fill);
  border-color: rgba(201, 168, 111, 0.25);
}

.tone-neutral {
  color: var(--text-2);
  background: var(--neutral-fill);
  border-color: var(--line);
}

.tone-accent {
  color: var(--accent);
  background: var(--accent-fill);
  border-color: rgba(130, 175, 205, 0.25);
}

.tone-running {
  color: var(--accent);
  background: var(--accent-fill);
  border-color: rgba(130, 175, 205, 0.3);
}
</style>
