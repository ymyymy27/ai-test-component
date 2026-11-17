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
  padding: 0 2px;
  border: 0;
  border-radius: 0;
  background: none;
  font-family: var(--font-mono);
  font-size: 10px;
  line-height: 1.6;
  letter-spacing: 0.04em;
  font-weight: 400;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.badge::before {
  content: "";
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
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
