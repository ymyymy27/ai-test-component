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
  padding: 0;
  border: 0;
  background: none;
  font-size: var(--fs-12);
  line-height: 1.5;
  font-weight: 500;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.tone-passed {
  color: var(--passed);
}

.tone-failed {
  color: var(--failed);
}

.tone-incomplete {
  color: var(--incomplete);
}

.tone-neutral {
  color: var(--text-3);
}

.tone-accent {
  color: var(--accent);
}

.tone-running {
  color: var(--accent);
}
</style>
